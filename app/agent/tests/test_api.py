"""Tests de la API HTTP con el servicio sobre herramientas MCP en memoria."""

import pytest
from fastmcp import Client
from httpx import ASGITransport, AsyncClient
from langchain_mcp_adapters.tools import load_mcp_tools
from wonder_agent.config import Settings
from wonder_agent.service import AgentService
from wonder_mcp.catalog import NullIndex
from wonder_mcp.chaos import ChaosController
from wonder_mcp.server import build_server
from wonder_mcp.store import MemoryStore


@pytest.fixture
async def client(products):
    from wonder_agent.api import create_app

    server = build_server(MemoryStore(products), NullIndex(), ChaosController())
    async with Client(server) as mcp:
        service = AgentService(Settings(), tools=await load_mcp_tools(mcp.session))
        app = create_app(service)
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as http:
            yield http


async def test_chat_returns_answer_session_and_tool_calls(client):
    r = await client.post("/chat", json={"message": "Rainbow Stacking Rings", "user_id": "u1"})
    assert r.status_code == 200
    body = r.json()
    assert body["session_id"] and body["tool_call_count"] == 1
    assert body["tool_calls"][0]["tool"] == "search-products"
    assert "toy-001" in body["answer"]
    assert len(body["trace_id"]) == 32


async def test_chat_accepts_message_history_and_validates_input(client):
    r = await client.post(
        "/chat",
        json={
            "messages": [
                {"role": "user", "content": "Show me puzzles"},
                {"role": "assistant", "content": "Here are some great puzzles!"},
                {"role": "user", "content": "Hello!"},
            ]
        },
    )
    assert r.status_code == 200 and r.json()["tool_call_count"] == 0
    assert (await client.post("/chat", json={})).status_code == 422
    assert (await client.post("/chat", json={"message": "a", "messages": []})).status_code == 200
    bad = await client.post("/chat", json={"messages": [{"role": "assistant", "content": "hi"}]})
    assert bad.status_code == 422


async def test_admin_overrides_roundtrip(client):
    r = await client.put(
        "/admin/overrides",
        json={"supervisor_model": "eu.anthropic.claude-haiku-5-5", "ttl_seconds": 60},
    )
    assert r.json()["overrides"] == {"supervisor_model": "eu.anthropic.claude-haiku-5-5"}
    health = (await client.get("/health")).json()
    assert health["supervisor_model"] == "eu.anthropic.claude-haiku-5-5"
    assert (await client.put("/admin/overrides", json={"x": 1})).status_code == 400
    assert (await client.delete("/admin/overrides")).json()["overrides"] == {}
