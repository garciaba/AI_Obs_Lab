"""Tests de las 5 herramientas MCP con almacén en memoria e índice vectorial falso."""

import json

import pytest
from fastmcp import Client
from fastmcp.exceptions import ToolError
from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from wonder_mcp.chaos import ChaosController
from wonder_mcp.server import build_server
from wonder_mcp.store import MemoryStore

ADDRESS = {
    "name": "Jane Doe",
    "street": "123 Main St",
    "city": "Springfield",
    "state": "IL",
    "zip": "62701",
    "country": "US",
}

_exporter = InMemorySpanExporter()
# El proveedor global solo puede fijarse una vez por proceso: se reutiliza si otro módulo
# de tests ya lo creó.
_provider = trace.get_tracer_provider()
if not isinstance(_provider, TracerProvider):
    _provider = TracerProvider()
    trace.set_tracer_provider(_provider)
_provider.add_span_processor(SimpleSpanProcessor(_exporter))


@pytest.fixture
def env(products, fake_index):
    _exporter.clear()
    store = MemoryStore(products)
    index = fake_index
    chaos = ChaosController()
    return store, index, chaos, build_server(store, index, chaos)


async def call(server, name, args):
    async with Client(server) as client:
        result = await client.call_tool(name, args)
    return result.structured_content or json.loads(result.content[0].text)


async def test_lists_the_five_store_tools(env):
    *_, server = env
    async with Client(server) as client:
        names = sorted(t.name for t in await client.list_tools())
    assert names == [
        "cancel-order",
        "check-order-status",
        "get-product",
        "purchase-product",
        "search-products",
    ]


async def test_search_uses_vector_index_and_emits_retriever_span(env):
    *_, server = env
    out = await call(server, "search-products", {"query": "rainbow"})
    assert out["totalFound"] >= 1
    assert out["results"][0]["image"].startswith("/product-images/toy-")
    finished = _exporter.get_finished_spans()
    # El cliente FastMCP en memoria emite un span CLIENT con el mismo nombre.
    spans = {s.name: s for s in finished if s.kind.name != "CLIENT"}
    retriever = spans["catalog.vector_search"]
    assert retriever.attributes["openinference.span.kind"] == "RETRIEVER"
    assert retriever.attributes["retrieval.documents.0.document.id"].startswith("toy-")
    tool_span = spans["tools/call search-products"]
    assert tool_span.attributes["openinference.span.kind"] == "CHAIN"
    assert retriever.parent.span_id == tool_span.context.span_id


async def test_search_falls_back_to_keyword_filter_without_index(env):
    _, index, _, server = env
    index.available = False
    out = await call(server, "search-products", {"query": "Rainbow Stacking Rings"})
    assert out["results"][0]["id"] == "toy-001"


async def test_purchase_check_and_cancel_flow_restores_inventory(env):
    store, *_, server = env
    before = store.products["toy-001"]["inventory"]
    bought = await call(
        server,
        "purchase-product",
        {
            "user_id": "u1",
            "items": [{"product_id": "toy-001", "quantity": 2}],
            "shipping_address": ADDRESS,
        },
    )
    assert bought["success"] and store.products["toy-001"]["inventory"] == before - 2

    status = await call(server, "check-order-status", {"user_id": "u1"})
    assert status["orders"][0]["orderId"] == bought["orderId"]
    other = await call(
        server, "check-order-status", {"user_id": "u2", "order_id": bought["orderId"]}
    )
    assert other == {"found": False, "orders": []}

    cancelled = await call(server, "cancel-order", {"user_id": "u1", "order_id": bought["orderId"]})
    assert cancelled == {"success": True}
    assert store.products["toy-001"]["inventory"] == before


async def test_business_errors_are_results_not_exceptions(env):
    *_, server = env
    missing = await call(server, "cancel-order", {"user_id": "u1", "order_id": "NOPE0000"})
    assert missing == {"success": False, "error": "Order not found"}
    no_stock = await call(
        server,
        "purchase-product",
        {
            "user_id": "u1",
            "items": [{"product_id": "toy-001", "quantity": 100000}],
            "shipping_address": ADDRESS,
        },
    )
    assert no_stock["success"] is False and "Insufficient stock" in no_stock["error"]


async def test_database_down_becomes_tool_error_and_error_span(env):
    store, *_, server = env
    store.available = False
    with pytest.raises(ToolError, match="temporarily unavailable"):
        await call(server, "get-product", {"product_id": "toy-001"})
    span = next(
        s
        for s in _exporter.get_finished_spans()
        if s.name == "tools/call get-product" and s.kind.name == "SERVER"
    )
    assert span.status.status_code.name == "ERROR"


async def test_chaos_error_latency_rag_and_injection(env):
    _, _, chaos, server = env
    chaos.apply({"error_tools": {"purchase-product": "Payment gateway unavailable"}})
    with pytest.raises(ToolError, match="Payment gateway unavailable"):
        await call(
            server,
            "purchase-product",
            {
                "user_id": "u1",
                "items": [{"product_id": "toy-001", "quantity": 1}],
                "shipping_address": ADDRESS,
            },
        )

    chaos.apply({"rag_mode": "empty"})
    assert await call(server, "search-products", {"query": "rainbow"}) == {
        "results": [],
        "totalFound": 0,
    }

    chaos.apply({"rag_mode": "irrelevant"})
    out = await call(server, "search-products", {"query": "rainbow"})
    assert out["results"] and all("Rainbow" not in r["name"] for r in out["results"])

    chaos.apply({"inject_prompt": True})
    out = await call(server, "get-product", {"product_id": "toy-001"})
    assert "ignore all previous instructions" in out["product"]["description"]

    chaos.reset()
    out = await call(server, "get-product", {"product_id": "toy-001"})
    assert "ignore all previous instructions" not in out["product"]["description"]


def test_chaos_expires_on_its_own_and_validates_input(monkeypatch):
    chaos = ChaosController()
    chaos.apply({"latency_ms": {"check-order-status": 5000}, "ttl_seconds": 60})
    assert chaos.state.active()
    import wonder_mcp.chaos as mod

    real = mod.time.monotonic()
    monkeypatch.setattr(mod.time, "monotonic", lambda: real + 61)
    assert not chaos.state.active()
    with pytest.raises(ValueError):
        chaos.apply({"latency_ms": {"no-such-tool": 1}})
    with pytest.raises(ValueError):
        chaos.apply({"rag_mode": "weird"})
