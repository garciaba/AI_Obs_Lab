"""Cliente HTTP mínimo del agente y de sus ajustes en caliente."""

from __future__ import annotations

import json
import os
import uuid
from contextlib import contextmanager

import httpx

AGENT_URL = os.environ.get("AGENT_URL", "http://localhost:8080")


def chat(row: dict, *, metadata: dict[str, str] | None = None, timeout: float = 300) -> dict:
    """Envía al agente una fila del dataset (o una petición de escenario)."""
    messages = row.get("messages")
    if isinstance(messages, str):
        messages = json.loads(messages)
    payload = {
        "messages": messages or [{"role": "user", "content": row["input"]}],
        "user_id": row.get("user_id", "eval-user-001"),
        "session_id": row.get("session_id") or f"eval-{uuid.uuid4().hex[:12]}",
        "metadata": metadata or {},
    }
    response = httpx.post(f"{AGENT_URL}/chat", json=payload, timeout=timeout)
    if response.status_code >= 400:
        return {
            "answer": "",
            "error": f"HTTP {response.status_code}: {response.text[:300]}",
            "tool_calls": [],
            "tool_call_count": 0,
            "trace_id": None,
            "http_status": response.status_code,
        }
    return {**response.json(), "http_status": response.status_code}


def health() -> dict:
    return httpx.get(f"{AGENT_URL}/health", timeout=10).json()


@contextmanager
def overrides(values: dict | None, ttl_seconds: int = 1800):
    """Aplica ajustes en caliente y los revierte al salir (además caducan solos)."""
    if not values:
        yield
        return
    httpx.put(
        f"{AGENT_URL}/admin/overrides", json={**values, "ttl_seconds": ttl_seconds}, timeout=10
    ).raise_for_status()
    try:
        yield
    finally:
        httpx.delete(f"{AGENT_URL}/admin/overrides", timeout=10)
