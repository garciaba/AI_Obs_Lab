"""Tests del agente con LLM simulado y el servidor MCP real en memoria.

El modelo simulado (`LLM_PROVIDER=fake`) sustituye a Claude; las herramientas son las del
servidor FastMCP de verdad, servidas por una sesión MCP en memoria sobre un almacén en
memoria. Se comprueba el cableado supervisor → subagente → herramienta MCP, la memoria
de sesión, los ajustes en caliente y la forma de las trazas.
"""

import pytest
from fastmcp import Client
from langchain_mcp_adapters.tools import load_mcp_tools
from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from wonder_agent.config import RuntimeOverrides, Settings
from wonder_agent.service import AgentService
from wonder_mcp.chaos import ChaosController
from wonder_mcp.server import build_server
from wonder_mcp.store import MemoryStore

exporter = InMemorySpanExporter()
# El proveedor global solo puede fijarse una vez por proceso: se reutiliza si otro módulo
# de tests ya lo creó.
provider = trace.get_tracer_provider()
if not isinstance(provider, TracerProvider):
    provider = TracerProvider()
    trace.set_tracer_provider(provider)
provider.add_span_processor(SimpleSpanProcessor(exporter))

from openinference.instrumentation.langchain import LangChainInstrumentor  # noqa: E402

LangChainInstrumentor().instrument(tracer_provider=provider)


class FakeIndex:
    def __init__(self, products):
        self.products = products

    def search(self, query, n_results=20, where=None):
        words = [w for w in query.lower().split() if len(w) > 3]
        hits = [p for p in self.products if any(w in p["name"].lower() for w in words)]
        return [{"id": p["id"], "document": p["name"], "score": 0.9} for p in hits[:n_results]]


@pytest.fixture
async def lab(products):
    exporter.clear()
    store = MemoryStore(products)
    chaos = ChaosController()
    server = build_server(store, FakeIndex(products), chaos)
    async with Client(server) as client:
        tools = await load_mcp_tools(client.session)
        service = AgentService(Settings(), tools=tools)
        yield service, store, chaos


async def ask(service, text, **kwargs):
    return await service.chat([{"role": "user", "content": text}], **kwargs)


async def test_catalog_request_goes_supervisor_catalog_agent_mcp_tool(lab):
    service, *_ = lab
    result = await ask(service, "Show me rainbow stacking toys", user_id="u1")
    assert [(c["agent"], c["tool"]) for c in result.tool_calls] == [
        ("catalog_agent", "search-products")
    ]
    assert "toy-001" in result.answer
    assert result.input_tokens > 0 and not result.degraded


async def test_purchase_then_cancel_uses_orders_agent_and_user_id(lab):
    service, store, _ = lab
    bought = await ask(service, "I want to buy toy-001", user_id="u7")
    call = bought.tool_calls[0]
    assert (call["agent"], call["tool"]) == ("orders_agent", "purchase-product")
    assert call["args"]["user_id"] == "u7"
    order_id = next(iter(store.orders))
    assert order_id in bought.answer

    cancelled = await ask(service, f"Please cancel order {order_id}", user_id="u7")
    assert cancelled.tool_calls[0]["tool"] == "cancel-order"
    assert store.orders[order_id]["status"] == "cancelled"


async def test_small_talk_needs_no_tools(lab):
    service, *_ = lab
    result = await ask(service, "Hello!")
    assert result.tool_calls == [] and "Wonder Toys" in result.answer


async def test_session_memory_keeps_history_and_resets_tool_events(lab):
    service, *_ = lab
    first = await ask(service, "Show me rainbow stacking toys", session_id="s1")
    second = await ask(service, "Hello!", session_id="s1")
    assert first.session_id == second.session_id == "s1"
    assert second.tool_calls == []
    graph, _ = await service.graphs()
    state = await graph.aget_state({"configurable": {"thread_id": "s1"}})
    humans = [m.content for m in state.values["messages"] if m.type == "human"]
    assert humans == ["Show me rainbow stacking toys", "Hello!"]


async def test_tool_failure_reaches_the_answer_instead_of_crashing(lab):
    service, _, chaos = lab
    chaos.apply({"error_tools": {"purchase-product": "Payment gateway unavailable"}})
    result = await ask(service, "buy toy-001", user_id="u1")
    assert result.tool_calls[0]["status"] == "error"
    assert "Payment gateway unavailable" in result.answer


async def test_database_down_is_reported_not_raised(lab):
    service, store, _ = lab
    store.available = False
    result = await ask(service, "where is my order?", user_id="u1")
    assert result.tool_calls[0]["status"] == "error"
    assert "temporarily unavailable" in result.answer


async def test_recursion_limit_stops_a_looping_subagent(lab):
    service, *_ = lab
    service.overrides.apply({"subagent_recursion_limit": 2})
    result = await ask(service, "Show me rainbow stacking toys")
    # create_react_agent corta con un mensaje propio; si lanzase GraphRecursionError el
    # nodo lo traduce a "step limit". En ambos casos el turno termina sin excepción.
    assert "more steps" in result.answer or "step limit" in result.answer
    assert result.tool_calls == [] or result.tool_calls[0].get("output") is None


async def test_supervisor_model_override_and_expiry(lab, monkeypatch):
    service, *_ = lab
    service.overrides.apply({"supervisor_model": "claude-haiku-5-5"})
    result = await ask(service, "Hello!")
    assert result.supervisor_model == "claude-haiku-5-5"
    service.overrides.reset()
    assert service.supervisor_model_id == "claude-sonnet-5-5"

    overrides = RuntimeOverrides(default_ttl_s=10)
    overrides.apply({"supervisor_recursion_limit": 50})
    import wonder_agent.config as mod

    now = mod.time.monotonic()
    monkeypatch.setattr(mod.time, "monotonic", lambda: now + 11)
    assert overrides.to_dict() == {"overrides": {}, "expires_in_seconds": None}
    with pytest.raises(ValueError):
        overrides.apply({"nope": 1})


async def test_trace_is_joined_from_supervisor_to_mcp_retriever(lab):
    service, *_ = lab
    tracer = trace.get_tracer("test")
    with tracer.start_as_current_span("POST /chat") as root:
        await ask(service, "Show me rainbow stacking toys", session_id="s-trace", user_id="u1")
    spans = exporter.get_finished_spans()
    # La sesión MCP en memoria no propaga contexto (en producción lo hacen el transporte
    # HTTP y openinference-instrumentation-mcp; lo verifica `task gate:2`), así que aquí
    # solo se exige una única traza para los spans del lado del agente.
    server_side = ("fastmcp", "wonder_mcp")
    agent_spans = [s for s in spans if s.instrumentation_scope.name not in server_side]
    assert {s.context.trace_id for s in agent_spans} == {root.get_span_context().trace_id}

    kinds = {}
    for s in spans:
        kinds.setdefault(s.attributes.get("openinference.span.kind"), []).append(s.name)
    assert "wonder_toys_supervisor" in kinds["CHAIN"]
    assert "catalog_agent" in " ".join(kinds["CHAIN"] + kinds.get("AGENT", []))
    assert len(kinds["LLM"]) >= 3  # supervisor x2 + subagente
    assert "search-products" in kinds["TOOL"]
    assert kinds["RETRIEVER"] == ["catalog.vector_search"]

    llm = next(s for s in spans if s.attributes.get("openinference.span.kind") == "LLM")
    assert llm.attributes["session.id"] == "s-trace"
    assert llm.attributes["user.id"] == "u1"
    assert root.attributes["input.value"] == "Show me rainbow stacking toys"
    assert root.attributes["session.id"] == "s-trace"
