"""API HTTP del agente Wonder Toys (FastAPI).

  POST   /chat              un turno de conversación (JSON, sin streaming)
  GET    /health            estado del agente
  GET    /admin/overrides   ajustes en caliente vigentes
  PUT    /admin/overrides   cambia modelo del supervisor o límites de recursión (con TTL)
  DELETE /admin/overrides   vuelve a la configuración de arranque

La telemetría se configura al importar este módulo, antes de cargar LangChain.
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from labcommon.telemetry import current_trace_id, setup_telemetry

telemetry = setup_telemetry("wonder-toys-agent")

from openinference.instrumentation.langchain import LangChainInstrumentor  # noqa: E402
from openinference.instrumentation.mcp import MCPInstrumentor  # noqa: E402
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor  # noqa: E402
from opentelemetry.instrumentation.httpx import HTTPXClientInstrumentor  # noqa: E402
from opentelemetry.instrumentation.psycopg import PsycopgInstrumentor  # noqa: E402
from opentelemetry.instrumentation.system_metrics import SystemMetricsInstrumentor  # noqa: E402
from opentelemetry.instrumentation.urllib3 import URLLib3Instrumentor  # noqa: E402

# OpenInference: spans de agente, LLM y herramienta de LangChain/LangGraph. Con
# OPENINFERENCE_ENABLE_GENAI_SEMCONV=true emite además los atributos gen_ai.*.
LangChainInstrumentor().instrument(tracer_provider=telemetry.tracer_provider)
# Propaga el contexto de traza dentro de las peticiones MCP (no genera spans).
MCPInstrumentor().instrument(tracer_provider=telemetry.tracer_provider)
# APM clásico: cliente HTTP hacia el servidor MCP, llamadas HTTP a Bedrock (botocore usa
# urllib3) y consultas a Postgres del checkpointer.
HTTPXClientInstrumentor().instrument()
URLLib3Instrumentor().instrument()
PsycopgInstrumentor().instrument(enable_commenter=False)
SystemMetricsInstrumentor().instrument()

from fastapi import FastAPI, HTTPException  # noqa: E402
from pydantic import BaseModel, Field, model_validator  # noqa: E402

from wonder_agent.config import Settings  # noqa: E402
from wonder_agent.service import AgentService  # noqa: E402

logger = logging.getLogger("wonder_agent.api")


class ChatMessage(BaseModel):
    role: str = Field(pattern="^(user|assistant)$")
    content: str


class ChatRequest(BaseModel):
    message: str | None = Field(default=None, description="Mensaje del usuario para este turno")
    messages: list[ChatMessage] | None = Field(
        default=None,
        description="Alternativa a `message`: turnos previos + mensaje actual (sesión nueva)",
    )
    session_id: str | None = Field(default=None, description="Sesión; se crea si no se indica")
    user_id: str = "anonymous"
    metadata: dict[str, str] | None = None

    @model_validator(mode="after")
    def _one_of(self) -> ChatRequest:
        if bool(self.message) == bool(self.messages):
            raise ValueError("provide exactly one of `message` or `messages`")
        return self


class ToolCall(BaseModel):
    agent: str
    tool: str
    args: dict = {}
    output: str | None = None
    status: str | None = None


class ChatResponse(BaseModel):
    answer: str
    session_id: str
    user_id: str
    trace_id: str | None
    tool_calls: list[ToolCall]
    tool_call_count: int
    supervisor_model: str
    input_tokens: int
    output_tokens: int
    degraded: bool
    error: str | None = None


def create_app(service: AgentService | None = None) -> FastAPI:
    svc = service or AgentService(Settings())

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        try:
            await svc.graphs()
        except Exception:  # noqa: BLE001 - se reintenta en la primera petición
            logger.exception("agent dependencies not ready at startup; will retry on demand")
        logger.info("wonder-toys-agent ready (supervisor=%s)", svc.supervisor_model_id)
        yield
        await svc.close()
        telemetry.shutdown()

    app = FastAPI(title="Wonder Toys Agent", version="0.1.0", lifespan=lifespan)
    app.state.service = svc

    @app.get("/health")
    async def health() -> dict:
        return {
            "status": "ok",
            "llm_provider": svc.settings.llm_provider,
            "supervisor_model": svc.supervisor_model_id,
            "subagent_model": svc.settings.subagent_model,
            **svc.overrides.to_dict(),
        }

    @app.post("/chat", response_model=ChatResponse)
    async def chat(req: ChatRequest) -> ChatResponse:
        messages = (
            [{"role": "user", "content": req.message}]
            if req.message
            else [m.model_dump() for m in req.messages or []]
        )
        try:
            result = await svc.chat(
                messages, session_id=req.session_id, user_id=req.user_id, metadata=req.metadata
            )
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        except Exception as exc:
            logger.exception("chat turn failed")
            raise HTTPException(
                status_code=502, detail=f"agent failed: {type(exc).__name__}: {exc}"
            ) from exc
        return ChatResponse(
            answer=result.answer,
            session_id=result.session_id,
            user_id=result.user_id,
            trace_id=current_trace_id(),
            tool_calls=[ToolCall(**c) for c in result.tool_calls],
            tool_call_count=len(result.tool_calls),
            supervisor_model=result.supervisor_model,
            input_tokens=result.input_tokens,
            output_tokens=result.output_tokens,
            degraded=result.degraded,
            error=result.error,
        )

    @app.get("/admin/overrides")
    async def get_overrides() -> dict:
        return svc.overrides.to_dict()

    @app.put("/admin/overrides")
    async def put_overrides(payload: dict) -> dict:
        try:
            state = svc.overrides.apply(payload)
        except (ValueError, TypeError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        logger.warning("runtime overrides applied: %s", state)
        return state

    @app.delete("/admin/overrides")
    async def delete_overrides() -> dict:
        svc.overrides.reset()
        logger.warning("runtime overrides reset")
        return svc.overrides.to_dict()

    FastAPIInstrumentor.instrument_app(
        app, excluded_urls="health", exclude_spans=["send", "receive"]
    )
    return app


app = create_app()


def main() -> None:
    import os

    import uvicorn

    uvicorn.run(
        app,
        host=os.environ.get("AGENT_HOST", "0.0.0.0"),
        port=int(os.environ.get("AGENT_PORT", "8080")),
        log_config=None,
        access_log=False,
    )


if __name__ == "__main__":
    main()
