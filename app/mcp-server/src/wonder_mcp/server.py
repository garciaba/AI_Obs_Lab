"""Servidor MCP de Wonder Toys: las 5 herramientas de la tienda sobre FastMCP.

Transporte streamable HTTP en `/mcp`. Además expone:
  GET  /health  estado del servidor y de Postgres
  GET|PUT|DELETE /chaos  control de los escenarios de fallo (solo red interna de compose)

Instrumentación:
  - FastMCP emite de forma nativa un span SERVER `tools/call <nombre>` por llamada.
  - `openinference-instrumentation-mcp` propaga el contexto de traza desde el agente.
  - Aquí se anota ese span con atributos OpenInference (CHAIN + entrada/salida) y se crea
    un span RETRIEVER para la búsqueda vectorial, con los documentos recuperados.
  - psycopg, httpx (cliente de Chroma) y el servidor ASGI se autoinstrumentan en `main()`.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from collections.abc import Awaitable, Callable
from typing import Annotated, Any

from fastmcp import FastMCP
from fastmcp.exceptions import ToolError
from opentelemetry import metrics, trace
from opentelemetry.trace import Status, StatusCode
from pydantic import BaseModel, Field
from starlette.requests import Request
from starlette.responses import JSONResponse

from wonder_mcp.catalog import VectorIndex
from wonder_mcp.chaos import INJECTION_PAYLOAD, ChaosController
from wonder_mcp.store import Store, StoreError

logger = logging.getLogger("wonder_mcp")
tracer = trace.get_tracer("wonder_mcp")
meter = metrics.get_meter("wonder_mcp")

_tool_calls = meter.create_counter(
    "wonder.mcp.tool.calls", unit="{call}", description="Llamadas a herramientas MCP"
)
_tool_duration = meter.create_histogram(
    "wonder.mcp.tool.duration", unit="s", description="Duración de las herramientas MCP"
)

INSTRUCTIONS = (
    "Wonder Toys store tools: search the catalog, get product details, purchase products, "
    "check order status and cancel orders."
)


class PurchaseItem(BaseModel):
    product_id: str = Field(description="The product ID to purchase (e.g. 'toy-001')")
    quantity: int = Field(ge=1, description="Quantity to purchase")


class ShippingAddress(BaseModel):
    name: str = Field(description="Recipient full name")
    street: str = Field(description="Street address")
    city: str = Field(description="City")
    state: str = Field(description="State/Province")
    zip: str = Field(description="ZIP/Postal code")
    country: str = Field(description="Country")


def _search_result(p: dict) -> dict:
    return {
        "id": p["id"],
        "name": p["name"],
        "description": p["description"],
        "price": p["price"],
        "ageRange": f"{p['ageRange']['min']}-{p['ageRange']['max']} years",
        "category": p["category"],
        "inStock": p["inventory"] > 0,
        "image": p["image"],
        "rating": p["rating"],
        "manufacturer": p["manufacturer"],
    }


def _order_view(o: dict) -> dict:
    return {
        "orderId": o["id"],
        "items": [
            {"productName": i["productName"], "quantity": i["quantity"], "price": i["price"]}
            for i in o["items"]
        ],
        "total": o["total"],
        "status": o["status"],
        "shippingAddress": {
            "name": o["shippingAddress"].get("name"),
            "city": o["shippingAddress"].get("city"),
            "state": o["shippingAddress"].get("state"),
        },
        "createdAt": o["createdAt"],
    }


def _dump(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, default=str)


def build_server(store: Store, index: VectorIndex, chaos: ChaosController) -> FastMCP:
    mcp = FastMCP("wonder-toys-store", instructions=INSTRUCTIONS)

    async def run_tool(name: str, args: dict[str, Any], fn: Callable[[], Awaitable[dict]]) -> dict:
        """Envoltura común: caos, atributos de span, métricas y traducción de errores."""
        span = trace.get_current_span()
        span.set_attributes(
            {
                "openinference.span.kind": "CHAIN",
                "tool.name": name,
                "input.value": _dump(args),
                "input.mime_type": "application/json",
            }
        )
        state = chaos.state
        started = time.perf_counter()
        outcome = "ok"
        try:
            delay_ms = state.latency_ms.get(name)
            if delay_ms:
                span.set_attribute("wonder.chaos.latency_ms", delay_ms)
                logger.warning("chaos: delaying %s by %d ms", name, delay_ms)
                await asyncio.sleep(delay_ms / 1000)
            if name in state.error_tools:
                span.set_attribute("wonder.chaos.error", True)
                raise ToolError(state.error_tools[name])
            result = await fn()
            span.set_attributes(
                {"output.value": _dump(result), "output.mime_type": "application/json"}
            )
            if result.get("success") is False or result.get("found") is False:
                outcome = "rejected"
            return result
        except StoreError as exc:
            # Error de negocio: se devuelve como resultado normal de la herramienta.
            outcome = "rejected"
            result = {"success": False, "error": str(exc)}
            span.set_attribute("output.value", _dump(result))
            return result
        except ToolError as exc:
            outcome = "error"
            logger.error("tool %s failed: %s", name, exc)
            raise
        except Exception as exc:
            outcome = "error"
            logger.exception("tool %s failed with an unexpected error", name)
            span.record_exception(exc)
            span.set_status(Status(StatusCode.ERROR, str(exc)))
            raise ToolError(
                f"The {name} tool is temporarily unavailable "
                f"({type(exc).__name__}). Please try again later."
            ) from exc
        finally:
            attrs = {"tool.name": name, "outcome": outcome}
            _tool_calls.add(1, attrs)
            _tool_duration.record(time.perf_counter() - started, attrs)
            logger.info(
                "tool %s finished outcome=%s duration_ms=%d",
                name,
                outcome,
                (time.perf_counter() - started) * 1000,
            )

    async def vector_search(query: str, where: dict | None, limit: int = 20) -> list[dict] | None:
        """Span RETRIEVER con los documentos recuperados (lo que AX muestra en el RAG)."""
        with tracer.start_as_current_span("catalog.vector_search") as span:
            span.set_attributes(
                {
                    "openinference.span.kind": "RETRIEVER",
                    "input.value": query,
                    "wonder.rag.mode": chaos.state.rag_mode,
                }
            )
            mode = chaos.state.rag_mode
            if mode == "empty":
                hits: list[dict] | None = []
            elif mode == "irrelevant":
                randoms = await store.random_products(5)
                hits = [
                    {"id": p["id"], "document": f"{p['name']}\n\n{p['description']}", "score": 0.0}
                    for p in randoms
                ]
            else:
                hits = await asyncio.to_thread(index.search, query, limit, where)
            if hits is None:
                span.set_attribute("wonder.rag.available", False)
                return None
            for i, hit in enumerate(hits):
                prefix = f"retrieval.documents.{i}.document"
                span.set_attribute(f"{prefix}.id", hit["id"])
                span.set_attribute(f"{prefix}.content", hit["document"])
                if hit.get("score") is not None:
                    span.set_attribute(f"{prefix}.score", hit["score"])
            span.set_attribute("wonder.rag.documents", len(hits))
            return hits

    @mcp.tool(name="search-products")
    async def search_products(
        query: Annotated[
            str | None,
            Field(description="Free-text search query matched against names and descriptions"),
        ] = None,
        keywords: Annotated[
            list[str] | None, Field(description="Keywords matched against product keyword tags")
        ] = None,
        min_age: Annotated[
            int | None, Field(description="Minimum age in years for the target child")
        ] = None,
        max_age: Annotated[
            int | None, Field(description="Maximum age in years for the target child")
        ] = None,
        category: Annotated[str | None, Field(description="Product category to filter by")] = None,
    ) -> dict:
        """Search the toy store inventory by text query, keywords, age range, or category.
        Use this when the user wants to find or browse products."""

        async def impl() -> dict:
            products: list[dict] | None = None
            total = 0
            if query:
                conditions: list[dict] = []
                if category:
                    conditions.append({"category": {"$eq": category.lower()}})
                if min_age is not None:
                    conditions.append({"ageMax": {"$gte": min_age}})
                if max_age is not None:
                    conditions.append({"ageMin": {"$lte": max_age}})
                where = (
                    None
                    if not conditions
                    else conditions[0]
                    if len(conditions) == 1
                    else {"$and": conditions}
                )
                hits = await vector_search(query, where)
                if hits is not None and (hits or chaos.state.rag_mode != "normal"):
                    products = await store.get_products([h["id"] for h in hits])
                    if keywords:
                        kws = [k.lower() for k in keywords]
                        products = [
                            p
                            for p in products
                            if any(kw in pk.lower() for kw in kws for pk in p["keywords"])
                        ]
                    total = len(products)
                    products = products[:10]
            if products is None:
                # Sin consulta, o índice vectorial no disponible / sin resultados: filtro SQL.
                products, total = await store.filter_products(
                    query=query,
                    keywords=keywords,
                    min_age=min_age,
                    max_age=max_age,
                    category=category,
                    limit=10,
                )
            results = [_search_result(p) for p in products]
            if chaos.state.inject_prompt and results:
                results[0]["description"] += INJECTION_PAYLOAD
            return {"results": results, "totalFound": total}

        return await run_tool(
            "search-products",
            {
                "query": query,
                "keywords": keywords,
                "min_age": min_age,
                "max_age": max_age,
                "category": category,
            },
            impl,
        )

    @mcp.tool(name="get-product")
    async def get_product(
        product_id: Annotated[str, Field(description="The product ID (e.g. 'toy-001')")],
    ) -> dict:
        """Get detailed information about a specific product by its ID. Use this when the
        user asks about a specific product or needs more details."""

        async def impl() -> dict:
            p = await store.get_product(product_id)
            if not p:
                return {"found": False}
            description = p["description"]
            if chaos.state.inject_prompt:
                description += INJECTION_PAYLOAD
            return {
                "found": True,
                "product": {
                    "id": p["id"],
                    "name": p["name"],
                    "description": description,
                    "marketingCopy": p["marketingCopy"],
                    "keywords": p["keywords"],
                    "ageRange": f"{p['ageRange']['min']}-{p['ageRange']['max']} years",
                    "price": p["price"],
                    "inventory": p["inventory"],
                    "category": p["category"],
                    "image": p["image"],
                    "rating": p["rating"],
                    "manufacturer": p["manufacturer"],
                    "dimensions": p["dimensions"],
                    "bestSellersRank": p["bestSellersRank"],
                },
            }

        return await run_tool("get-product", {"product_id": product_id}, impl)

    @mcp.tool(name="purchase-product")
    async def purchase_product(
        user_id: Annotated[str, Field(description="The current user's ID")],
        items: Annotated[
            list[PurchaseItem], Field(description="Products and quantities to purchase")
        ],
        shipping_address: Annotated[ShippingAddress, Field(description="Shipping address")],
    ) -> dict:
        """Purchase one or more products. The user's credit card is on file, so only
        shipping details are needed. Use this after the user has confirmed they want to buy
        and has provided shipping information."""

        async def impl() -> dict:
            order = await store.create_order(
                user_id, [i.model_dump() for i in items], shipping_address.model_dump()
            )
            return {
                "success": True,
                "orderId": order["id"],
                "total": order["total"],
                "items": [
                    {
                        "productName": i["productName"],
                        "quantity": i["quantity"],
                        "price": i["price"],
                    }
                    for i in order["items"]
                ],
            }

        return await run_tool(
            "purchase-product",
            {
                "user_id": user_id,
                "items": [i.model_dump() for i in items],
                "shipping_address": shipping_address.model_dump(),
            },
            impl,
        )

    @mcp.tool(name="check-order-status")
    async def check_order_status(
        user_id: Annotated[str, Field(description="The current user's ID")],
        order_id: Annotated[
            str | None, Field(description="Specific order ID to look up (e.g. 'A1B2C3D4')")
        ] = None,
        product_search: Annotated[
            str | None,
            Field(description="Search term to find orders by product name (e.g. 'puzzle')"),
        ] = None,
    ) -> dict:
        """Check the status of an order by order ID, or search for orders by product name.
        Use this when users ask about their order status, shipping, or delivery."""

        async def impl() -> dict:
            if order_id:
                order = await store.get_order(order_id)
                matched = [order] if order and order["userId"] == user_id else []
            else:
                matched = await store.orders_by_user(user_id)
                if product_search:
                    term = product_search.lower()
                    matched = [
                        o
                        for o in matched
                        if any(
                            term in i["productName"].lower() or term in i["productId"].lower()
                            for i in o["items"]
                        )
                    ]
            if not matched:
                return {"found": False, "orders": []}
            return {"found": True, "orders": [_order_view(o) for o in matched]}

        return await run_tool(
            "check-order-status",
            {"user_id": user_id, "order_id": order_id, "product_search": product_search},
            impl,
        )

    @mcp.tool(name="cancel-order")
    async def cancel_order(
        user_id: Annotated[str, Field(description="The current user's ID")],
        order_id: Annotated[str, Field(description="The order ID to cancel (e.g. 'A1B2C3D4')")],
    ) -> dict:
        """Cancel an order by its order ID. Only orders that are still processing or
        shipping can be cancelled. Delivered orders cannot be cancelled."""

        async def impl() -> dict:
            return await store.cancel_order(order_id, user_id)

        return await run_tool("cancel-order", {"user_id": user_id, "order_id": order_id}, impl)

    @mcp.custom_route("/health", methods=["GET"])
    async def health(_: Request) -> JSONResponse:
        db_ok = await store.ping()
        return JSONResponse(
            {"status": "ok", "database": "up" if db_ok else "down", "chaos": chaos.state.active()}
        )

    @mcp.custom_route("/chaos", methods=["GET", "PUT", "DELETE"])
    async def chaos_route(request: Request) -> JSONResponse:
        if request.method == "DELETE":
            chaos.reset()
            logger.warning("chaos reset")
        elif request.method == "PUT":
            try:
                state = chaos.apply(await request.json())
            except (ValueError, TypeError) as exc:
                return JSONResponse({"error": str(exc)}, status_code=400)
            logger.warning("chaos applied: %s", _dump(state.to_dict()))
        return JSONResponse(chaos.state.to_dict())

    return mcp


def main() -> None:
    from labcommon.telemetry import setup_telemetry

    setup_telemetry("wonder-toys-mcp")

    from openinference.instrumentation.mcp import MCPInstrumentor
    from opentelemetry.instrumentation.asgi import OpenTelemetryMiddleware
    from opentelemetry.instrumentation.httpx import HTTPXClientInstrumentor
    from opentelemetry.instrumentation.psycopg import PsycopgInstrumentor
    from opentelemetry.instrumentation.system_metrics import SystemMetricsInstrumentor

    MCPInstrumentor().instrument()
    HTTPXClientInstrumentor().instrument()
    PsycopgInstrumentor().instrument(enable_commenter=False)
    SystemMetricsInstrumentor().instrument()

    import uvicorn
    from starlette.applications import Starlette
    from starlette.routing import Mount

    from wonder_mcp.catalog import ChromaIndex
    from wonder_mcp.config import Settings
    from wonder_mcp.store import PostgresStore

    settings = Settings()
    store = PostgresStore(settings.database_url, settings.db_timeout_s)
    chaos = ChaosController(settings.chaos_default_ttl_s)
    mcp = build_server(store, ChromaIndex(settings.chroma_url, settings.chroma_collection), chaos)
    mcp_app = mcp.http_app(path="/mcp")

    from contextlib import asynccontextmanager

    @asynccontextmanager
    async def lifespan(app: Starlette):
        await store.open()
        async with mcp_app.lifespan(app):
            logger.info("wonder-toys-mcp listening on %s:%d", settings.host, settings.port)
            yield
        await store.close()

    app = Starlette(routes=[Mount("/", app=mcp_app)], lifespan=lifespan)
    from opentelemetry.util.http import parse_excluded_urls

    # Un span SERVER por petición HTTP (sin los spans internos send/receive de ASGI) y sin
    # trazar los healthchecks.
    traced_app = OpenTelemetryMiddleware(
        app,
        excluded_urls=parse_excluded_urls("health"),
        exclude_spans=["send", "receive"],
    )
    uvicorn.run(
        traced_app, host=settings.host, port=settings.port, log_config=None, access_log=False
    )


if __name__ == "__main__":
    main()
