"""Grafo LangGraph de Wonder Toys: un supervisor que deriva a dos subagentes.

    START → supervisor ──(sin llamadas)──→ END
                │  ▲
       Send por │  │ ToolMessage con el resultado
       llamada  ▼  │
        catalog_agent / orders_agent   (agentes ReAct con herramientas MCP)

- El supervisor (Sonnet) decide a quién delegar con dos "herramientas de traspaso" y
  redacta la respuesta final.
- Cada subagente (Haiku) es un agente ReAct con su subconjunto de herramientas del
  servidor MCP y su propio contexto: solo ve la tarea que le pasa el supervisor.
- El checkpointer guarda el estado del grafo principal por `thread_id` (= sesión).
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Sequence
from typing import Annotated, Any

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage
from langchain_core.runnables import RunnableConfig
from langchain_core.tools import BaseTool, tool
from langgraph.errors import GraphRecursionError
from langgraph.graph import END, START, MessagesState, StateGraph
from langgraph.prebuilt import ToolNode, create_react_agent
from langgraph.types import Send

from wonder_agent.prompts import (
    CATALOG_AGENT_PROMPT,
    ORDERS_AGENT_PROMPT,
    PROMPT_VERSION,
    supervisor_prompt,
)

logger = logging.getLogger("wonder_agent.graph")

CATALOG_TOOLS = ("search-products", "get-product")
ORDERS_TOOLS = ("purchase-product", "check-order-status", "cancel-order")
SUBAGENTS = ("catalog_agent", "orders_agent")
MAX_TOOL_OUTPUT_CHARS = 6000


def _merge_events(old: list[dict] | None, new: list[dict] | None) -> list[dict]:
    """Acumula eventos de herramienta dentro de un turno; `None` los reinicia."""
    if new is None:
        return []
    return (old or []) + new


class LabState(MessagesState):
    user_id: str
    tool_events: Annotated[list[dict], _merge_events]


@tool
def catalog_agent(task: str) -> str:
    """Delegate a task to the catalog specialist: search the toy catalog, compare products
    or get full details of a product. `task` must be self-contained."""
    raise NotImplementedError("handoff tool: executed by the graph, not called directly")


@tool
def orders_agent(task: str) -> str:
    """Delegate a task to the orders specialist: purchase products (needs product IDs,
    quantities and the full shipping address), check order status or cancel an order.
    `task` must be self-contained."""
    raise NotImplementedError("handoff tool: executed by the graph, not called directly")


HANDOFF_TOOLS = [catalog_agent, orders_agent]


def _message_text(message: Any) -> str:
    content = getattr(message, "content", message)
    if isinstance(content, str):
        return content
    parts = []
    for block in content or []:
        if isinstance(block, dict) and block.get("type") == "text":
            parts.append(block.get("text", ""))
        elif isinstance(block, str):
            parts.append(block)
    return "".join(parts)


def _tool_events(agent: str, messages: Sequence[Any]) -> list[dict]:
    """Extrae (herramienta, argumentos, resultado) de la conversación de un subagente."""
    calls: dict[str, dict] = {}
    events: list[dict] = []
    for m in messages:
        if isinstance(m, AIMessage):
            for tc in m.tool_calls or []:
                event = {"agent": agent, "tool": tc["name"], "args": tc.get("args", {})}
                calls[tc["id"]] = event
                events.append(event)
        elif isinstance(m, ToolMessage) and m.tool_call_id in calls:
            calls[m.tool_call_id]["output"] = _message_text(m)[:MAX_TOOL_OUTPUT_CHARS]
            calls[m.tool_call_id]["status"] = getattr(m, "status", "success")
    return events


def build_graph(
    *,
    supervisor_model: Callable[[], BaseChatModel],
    subagent_model: BaseChatModel,
    tools: Sequence[BaseTool],
    checkpointer: Any,
    subagent_recursion_limit: Callable[[], int],
    prompt_version: Callable[[], str] = lambda: PROMPT_VERSION,
):
    """Compila el grafo. `supervisor_model` y `subagent_recursion_limit` son funciones
    para que los ajustes en caliente de los escenarios se apliquen sin reiniciar."""
    by_name = {t.name: t for t in tools}
    missing = [n for n in (*CATALOG_TOOLS, *ORDERS_TOOLS) if n not in by_name]
    if missing:
        raise ValueError(f"MCP server did not expose tools: {', '.join(missing)}")

    def react(name: str, prompt: str, tool_names: Sequence[str]):
        return create_react_agent(
            subagent_model,
            ToolNode([by_name[n] for n in tool_names], handle_tool_errors=True),
            prompt=prompt,
            name=name,
            checkpointer=False,
        )

    subagents = {
        "catalog_agent": react("catalog_agent", CATALOG_AGENT_PROMPT, CATALOG_TOOLS),
        "orders_agent": react("orders_agent", ORDERS_AGENT_PROMPT, ORDERS_TOOLS),
    }

    async def supervisor(state: LabState, config: RunnableConfig) -> dict:
        model = supervisor_model().bind_tools(HANDOFF_TOOLS)
        context = SystemMessage(
            content=(
                f"{supervisor_prompt(prompt_version())}\n\n"
                f"The current user's ID is: {state['user_id']}."
            )
        )
        response = await model.ainvoke([context, *state["messages"]], config)
        return {"messages": [response]}

    def route(state: LabState) -> list[Send] | str:
        last = state["messages"][-1]
        calls = getattr(last, "tool_calls", None) or []
        if not calls:
            return END
        sends = []
        for tc in calls:
            target = tc["name"] if tc["name"] in SUBAGENTS else "catalog_agent"
            sends.append(
                Send(
                    target,
                    {
                        "task": str(tc.get("args", {}).get("task") or ""),
                        "tool_call_id": tc["id"],
                        "tool_name": tc["name"],
                        "user_id": state["user_id"],
                    },
                )
            )
        return sends

    def make_subagent_node(name: str):
        async def node(payload: dict, config: RunnableConfig) -> dict:
            task = payload["task"] or "Help the customer with their last request."
            prompt = (
                f"{task}\n\nThe current user's ID is: {payload['user_id']}. "
                "Use this user_id when making purchases or checking order status."
            )
            limit = subagent_recursion_limit()
            status = "success"
            events: list[dict] = []
            try:
                result = await subagents[name].ainvoke(
                    {"messages": [HumanMessage(content=prompt)]},
                    {**config, "recursion_limit": limit},
                )
                events = _tool_events(name, result["messages"])
                answer = _message_text(result["messages"][-1]) or "(no answer)"
            except GraphRecursionError:
                status = "error"
                answer = (
                    f"The {name} specialist stopped after reaching its step limit ({limit}) "
                    "without finishing the task."
                )
                logger.warning("%s hit the recursion limit (%d)", name, limit)
            except Exception as exc:  # noqa: BLE001 - el supervisor decide cómo responder
                status = "error"
                answer = f"The {name} specialist failed: {type(exc).__name__}: {exc}"
                logger.exception("%s failed", name)
            return {
                "messages": [
                    ToolMessage(
                        content=answer,
                        tool_call_id=payload["tool_call_id"],
                        name=payload["tool_name"],
                        status=status,
                    )
                ],
                "tool_events": events,
            }

        node.__name__ = name
        return node

    builder = StateGraph(LabState)
    builder.add_node("supervisor", supervisor)
    for name in SUBAGENTS:
        builder.add_node(name, make_subagent_node(name))
        builder.add_edge(name, "supervisor")
    builder.add_edge(START, "supervisor")
    builder.add_conditional_edges("supervisor", route, [*SUBAGENTS, END])
    return builder.compile(checkpointer=checkpointer, name="wonder_toys_supervisor")


def final_answer(state: dict) -> str:
    for message in reversed(state.get("messages", [])):
        if isinstance(message, AIMessage) and not message.tool_calls:
            return _message_text(message)
    return ""
