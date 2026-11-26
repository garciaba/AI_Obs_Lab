"""Servicio del agente: carga de herramientas MCP, checkpointer y ejecución de turnos."""

from __future__ import annotations

import asyncio
import json
import logging
import time
import uuid
from dataclasses import dataclass, field
from typing import Any

from langchain_core.messages import AIMessage, HumanMessage
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.errors import GraphRecursionError
from openinference.instrumentation import using_attributes
from opentelemetry import metrics, trace

from wonder_agent.config import RuntimeOverrides, Settings
from wonder_agent.graph import build_graph, final_answer
from wonder_agent.llm import get_model
from wonder_agent.prompts import PROMPT_VERSION

logger = logging.getLogger("wonder_agent")
meter = metrics.get_meter("wonder_agent")

_requests = meter.create_counter(
    "wonder.agent.requests", unit="{request}", description="Peticiones de chat al agente"
)
_duration = meter.create_histogram(
    "wonder.agent.request.duration", unit="s", description="Duración de un turno del agente"
)
_tokens = meter.create_counter(
    "wonder.agent.tokens", unit="{token}", description="Tokens consumidos por el agente"
)
_tool_calls = meter.create_histogram(
    "wonder.agent.tool_calls", unit="{call}", description="Llamadas a herramientas por turno"
)


async def _trace_under_tool_span(request: Any, handler: Any) -> Any:
    """Ejecuta la llamada MCP con el span TOOL de LangChain como contexto activo.

    El instrumentador de LangChain crea sus spans desde callbacks y no los deja como
    contexto actual de OpenTelemetry; sin esto, la petición HTTP al servidor MCP (y todo
    lo que cuelga de ella en el servidor) aparecería como hija del span raíz en vez de
    como hija de la herramienta.
    """
    from openinference.instrumentation.langchain import get_current_span

    span = get_current_span()
    if span is None:
        return await handler(request)
    with trace.use_span(span, end_on_exit=False):
        return await handler(request)


@dataclass
class TurnResult:
    answer: str
    session_id: str
    user_id: str
    tool_calls: list[dict] = field(default_factory=list)
    supervisor_model: str = ""
    input_tokens: int = 0
    output_tokens: int = 0
    degraded: bool = False
    error: str | None = None


class AgentService:
    def __init__(self, settings: Settings, *, tools: list | None = None) -> None:
        self.settings = settings
        self.overrides = RuntimeOverrides(settings.overrides_default_ttl_s)
        self._tools = tools
        self._pool = None
        self._pg_graph = None
        self._memory_graph = None
        self._lock = asyncio.Lock()

    # --- configuración vigente ---------------------------------------------------

    @property
    def supervisor_model_id(self) -> str:
        return self.overrides.get("supervisor_model", self.settings.supervisor_model)

    def _supervisor_model(self):
        return get_model(self.settings, self.supervisor_model_id, "supervisor")

    @property
    def prompt_version(self) -> str:
        return self.overrides.get("prompt_version", PROMPT_VERSION)

    def _subagent_limit(self) -> int:
        return self.overrides.get(
            "subagent_recursion_limit", self.settings.subagent_recursion_limit
        )

    # --- dependencias --------------------------------------------------------------

    async def _load_tools(self) -> list:
        if self._tools is None:
            from langchain_mcp_adapters.client import MultiServerMCPClient

            client = MultiServerMCPClient(
                {"store": {"url": self.settings.mcp_url, "transport": "streamable_http"}},
                tool_interceptors=[_trace_under_tool_span],
            )
            self._tools = await client.get_tools()
            logger.info("loaded %d MCP tools from %s", len(self._tools), self.settings.mcp_url)
        return self._tools

    def _build(self, checkpointer: Any):
        # Los dos subagentes comparten modelo (Haiku) pero cada uno tiene su rol.
        subagent = get_model(self.settings, self.settings.subagent_model, "subagent")
        graph = build_graph(
            supervisor_model=self._supervisor_model,
            subagent_model=subagent,
            tools=self._tools or [],
            checkpointer=checkpointer,
            subagent_recursion_limit=self._subagent_limit,
            prompt_version=lambda: self.prompt_version,
        )
        return graph

    async def _postgres_graph(self):
        """Grafo con checkpointer en Postgres, o None si Postgres no responde."""
        from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
        from psycopg.rows import dict_row
        from psycopg_pool import AsyncConnectionPool

        try:
            if self._pool is None:
                self._pool = AsyncConnectionPool(
                    self.settings.database_url,
                    min_size=1,
                    max_size=5,
                    open=False,
                    timeout=3,
                    kwargs={
                        "autocommit": True,
                        "prepare_threshold": 0,
                        "row_factory": dict_row,
                        "connect_timeout": 3,
                    },
                    name="wonder-agent-checkpoints",
                )
                await self._pool.open(wait=False)
            async with self._pool.connection(timeout=3) as conn:
                await conn.execute("SELECT 1")
            if self._pg_graph is None:
                saver = AsyncPostgresSaver(self._pool)
                await saver.setup()
                self._pg_graph = self._build(saver)
            return self._pg_graph
        except Exception as exc:  # noqa: BLE001 - se degrada a memoria
            logger.error(
                "session store (Postgres) unavailable, running without persisted memory: %s",
                exc,
            )
            return None

    async def graphs(self) -> tuple[Any, bool]:
        """Devuelve (grafo, degradado). Degradado = sin memoria persistente."""
        async with self._lock:
            await self._load_tools()
            if self._memory_graph is None:
                self._memory_graph = self._build(InMemorySaver())
            if self.settings.database_url == "memory":
                return self._memory_graph, False
            graph = await self._postgres_graph()
        if graph is None:
            return self._memory_graph, True
        return graph, False

    async def close(self) -> None:
        if self._pool is not None:
            await self._pool.close()

    # --- un turno de conversación --------------------------------------------------

    async def chat(
        self,
        messages: list[dict],
        *,
        session_id: str | None = None,
        user_id: str = "anonymous",
        metadata: dict[str, str] | None = None,
    ) -> TurnResult:
        session_id = session_id or uuid.uuid4().hex
        lc_messages = [
            (AIMessage if m["role"] == "assistant" else HumanMessage)(content=m["content"])
            for m in messages
            if m.get("role") in ("user", "assistant") and m.get("content")
        ]
        if not lc_messages or not isinstance(lc_messages[-1], HumanMessage):
            raise ValueError("the last message must be a user message")
        question = lc_messages[-1].content

        supervisor_model = self.supervisor_model_id
        span = trace.get_current_span()
        # Entrada/salida y sesión en el span raíz (el span HTTP): el colector lo marca como
        # CHAIN para Arize AX y lo redacta para Dynatrace.
        span.set_attributes({"input.value": question, "session.id": session_id, "user.id": user_id})
        started = time.perf_counter()
        result = TurnResult(
            answer="", session_id=session_id, user_id=user_id, supervisor_model=supervisor_model
        )
        outcome = "ok"
        graph, result.degraded = await self.graphs()
        config = {
            "configurable": {"thread_id": session_id},
            "recursion_limit": self.overrides.get(
                "supervisor_recursion_limit", self.settings.supervisor_recursion_limit
            ),
            "metadata": {"prompt_version": self.prompt_version, **(metadata or {})},
        }
        graph_input = {"messages": lc_messages, "user_id": user_id, "tool_events": None}
        state: dict = {}
        try:
            with using_attributes(
                session_id=session_id,
                user_id=user_id,
                metadata={
                    "prompt_version": self.prompt_version,
                    "supervisor_model": supervisor_model,
                    **(metadata or {}),
                },
            ):
                try:
                    state = await graph.ainvoke(graph_input, config)
                except GraphRecursionError:
                    outcome = "recursion_limit"
                    result.error = "recursion_limit"
                    result.answer = (
                        "I'm sorry, I could not complete your request: it needed too many "
                        "steps. Could you rephrase it or split it into smaller requests?"
                    )
                    logger.warning("supervisor hit the recursion limit")
            if state:
                result.answer = final_answer(state)
                result.tool_calls = state.get("tool_events") or []
                for m in state.get("messages", [])[-12:]:
                    usage = getattr(m, "usage_metadata", None) or {}
                    result.input_tokens += usage.get("input_tokens", 0)
                    result.output_tokens += usage.get("output_tokens", 0)
            return result
        except Exception:
            outcome = "error"
            raise
        finally:
            elapsed = time.perf_counter() - started
            attrs = {"outcome": outcome, "supervisor.model": supervisor_model}
            _requests.add(1, attrs)
            _duration.record(elapsed, attrs)
            _tool_calls.record(len(result.tool_calls), attrs)
            if result.input_tokens:
                _tokens.add(result.input_tokens, {"direction": "input"})
                _tokens.add(result.output_tokens, {"direction": "output"})
            span.set_attributes(
                {
                    "output.value": result.answer,
                    "wonder.tool_call_count": len(result.tool_calls),
                    # Para las evals online de AX sobre el span raíz: qué herramientas se
                    # usaron y qué devolvieron (contexto de fidelidad al catálogo).
                    "wonder.tools_used": ",".join(c["tool"] for c in result.tool_calls),
                    "wonder.tool_results": json.dumps(
                        [
                            {"tool": c["tool"], "args": c.get("args"), "output": c.get("output")}
                            for c in result.tool_calls
                        ],
                        ensure_ascii=False,
                    )[:12000],
                    "wonder.supervisor_model": supervisor_model,
                    "wonder.prompt_version": self.prompt_version,
                    "wonder.degraded": result.degraded,
                }
            )
            logger.info(
                "chat turn finished outcome=%s session=%s tools=%d duration_ms=%d degraded=%s",
                outcome,
                session_id,
                len(result.tool_calls),
                elapsed * 1000,
                result.degraded,
            )
