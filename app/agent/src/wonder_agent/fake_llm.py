"""Modelo de chat simulado y determinista ("LLM simulado").

Reproduce el esqueleto del comportamiento de Claude en este agente sin llamar a ningún
LLM: el supervisor delega según palabras clave y redacta con lo que devuelven los
subagentes; cada subagente hace una llamada a herramienta y resume su resultado. Sirve
para los tests unitarios y para comprobar el cableado completo (API → grafo → MCP →
Postgres/Chroma → trazas) en CI sin credenciales.
"""

from __future__ import annotations

import re
from typing import Any

from langchain_core.callbacks import CallbackManagerForLLMRun
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, ToolMessage
from langchain_core.outputs import ChatGeneration, ChatResult

_ORDER_WORDS = re.compile(
    r"\b(buy|purchase|order|orders|cancel|ship|status|comprar|pedido)\b", re.I
)
_SMALL_TALK = re.compile(r"^\s*(hi|hello|hola|thanks|thank you)\b", re.I)
_TOY_ID = re.compile(r"toy-\d{3}", re.I)
_ORDER_ID = re.compile(r"\b[A-Z0-9]{8}\b")
_USER_ID = re.compile(r"user's ID is: (\S+?)\.?(\s|$)")

FAKE_ADDRESS = {
    "name": "Test Customer",
    "street": "1 Test Lane",
    "city": "Springfield",
    "state": "IL",
    "zip": "62701",
    "country": "US",
}


def _text(message: BaseMessage) -> str:
    content = message.content
    if isinstance(content, str):
        return content
    return " ".join(b.get("text", "") if isinstance(b, dict) else str(b) for b in content)


class FakeToyStoreModel(BaseChatModel):
    role: str = "supervisor"
    model_id: str = "fake-model"

    @property
    def _llm_type(self) -> str:
        return "fake-toy-store"

    @property
    def _identifying_params(self) -> dict[str, Any]:
        return {"model_name": self.model_id, "role": self.role}

    def bind_tools(self, tools: Any, **kwargs: Any) -> FakeToyStoreModel:
        return self

    def _generate(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: CallbackManagerForLLMRun | None = None,
        **kwargs: Any,
    ) -> ChatResult:
        reply = getattr(self, f"_{self.role}")(messages)
        prompt_chars = sum(len(_text(m)) for m in messages)
        reply.usage_metadata = {
            "input_tokens": max(1, prompt_chars // 4),
            "output_tokens": max(1, len(_text(reply)) // 4),
            "total_tokens": max(2, (prompt_chars + len(_text(reply))) // 4),
        }
        reply.response_metadata = {"model_name": self.model_id, "stopReason": "end_turn"}
        return ChatResult(generations=[ChatGeneration(message=reply)])

    # --- comportamiento por rol -------------------------------------------------

    @staticmethod
    def _call(name: str, args: dict[str, Any], n: int) -> AIMessage:
        return AIMessage(
            content="",
            tool_calls=[
                {"name": name, "args": args, "id": f"fake_{name}_{n}", "type": "tool_call"}
            ],
        )

    def _supervisor(self, messages: list[BaseMessage]) -> AIMessage:
        last = messages[-1]
        if isinstance(last, ToolMessage):
            results = []
            for m in reversed(messages):
                if not isinstance(m, ToolMessage):
                    break
                results.append(_text(m))
            return AIMessage(content="Here is what I found:\n\n" + "\n\n".join(reversed(results)))
        question = _text(last)
        if _SMALL_TALK.search(question):
            return AIMessage(content="Hello! Welcome to Wonder Toys. How can I help you today?")
        target = "orders_agent" if _ORDER_WORDS.search(question) else "catalog_agent"
        return self._call(target, {"task": question}, len(messages))

    def _subagent(self, messages: list[BaseMessage]) -> AIMessage:
        system = _text(messages[0]) if messages else ""
        if "orders specialist" in system:
            return self._orders(messages)
        return self._catalog(messages)

    def _catalog(self, messages: list[BaseMessage]) -> AIMessage:
        last = messages[-1]
        if isinstance(last, ToolMessage):
            return AIMessage(content=f"Catalog result: {_text(last)}")
        task = _text(next(m for m in reversed(messages) if isinstance(m, HumanMessage)))
        toy = _TOY_ID.search(task)
        if toy:
            return self._call("get-product", {"product_id": toy.group(0).lower()}, len(messages))
        query = task.split("\n")[0][:200]
        return self._call("search-products", {"query": query}, len(messages))

    def _orders(self, messages: list[BaseMessage]) -> AIMessage:
        last = messages[-1]
        if isinstance(last, ToolMessage):
            return AIMessage(content=f"Orders result: {_text(last)}")
        task = _text(next(m for m in reversed(messages) if isinstance(m, HumanMessage)))
        user = _USER_ID.search(task)
        user_id = user.group(1) if user else "anonymous"
        n = len(messages)
        if re.search(r"\bcancel", task, re.I):
            order = _ORDER_ID.search(task.split("\n")[0])
            return self._call(
                "cancel-order",
                {"user_id": user_id, "order_id": order.group(0) if order else "UNKNOWN0"},
                n,
            )
        toys = _TOY_ID.findall(task)
        if toys and re.search(r"\b(buy|purchase|comprar)\b", task, re.I):
            return self._call(
                "purchase-product",
                {
                    "user_id": user_id,
                    "items": [{"product_id": t.lower(), "quantity": 1} for t in toys],
                    "shipping_address": FAKE_ADDRESS,
                },
                n,
            )
        return self._call("check-order-status", {"user_id": user_id}, n)
