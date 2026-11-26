"""Tests del runner de escenarios (sin servicios: HTTP simulado)."""

import httpx
import pytest
from wonder_scenarios import runner

EXPECTED = {
    "slow-tool",
    "tool-error",
    "db-down",
    "bad-args",
    "catalog-hallucination",
    "prompt-injection",
    "tool-loop",
    "model-swap",
    "bedrock-throttling",
}


def test_the_nine_design_scenarios_are_defined_and_well_formed():
    assert set(runner.names()) == EXPECTED
    for name in runner.names():
        spec = runner.load(name)
        assert spec["name"] == name and spec["title"] and spec["summary"]
        assert {"arize", "dynatrace"} <= set(spec["look_at"])
        assert runner._requests(spec), f"{name}: sin peticiones"


def test_every_scenario_has_its_doc_sheet():
    from pathlib import Path

    docs = Path(__file__).parents[2] / "docs" / "scenarios"
    for name in EXPECTED:
        assert (docs / f"{name}.md").exists(), f"falta docs/scenarios/{name}.md"


def test_chaos_payloads_are_accepted_by_the_mcp_chaos_controller():
    from wonder_mcp.chaos import ChaosController

    for name in runner.names():
        spec = runner.load(name)
        for phase in spec.get("phases") or [spec]:
            if phase.get("chaos"):
                ChaosController().apply({**phase["chaos"], "ttl_seconds": 60})


def test_overrides_are_accepted_by_the_agent():
    from wonder_agent.config import RuntimeOverrides

    for name in runner.names():
        spec = runner.load(name)
        for phase in spec.get("phases") or [spec]:
            if phase.get("overrides"):
                RuntimeOverrides().apply(phase["overrides"])
    assert runner.load("model-swap")["phases"][1]["overrides"]["supervisor_model"].startswith("eu.")


@pytest.fixture
def fake_lab(monkeypatch):
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append((request.method, request.url.path))
        if request.url.path == "/chat":
            if any(m == "PUT" and p == "/chaos" for m, p in calls) and not any(
                m == "DELETE" and p == "/chaos" for m, p in calls
            ):
                tool = {"agent": "orders_agent", "tool": "purchase-product", "status": "error"}
            else:
                tool = {"agent": "orders_agent", "tool": "purchase-product", "status": "success"}
            return httpx.Response(
                200,
                json={
                    "answer": "ok",
                    "trace_id": "a" * 32,
                    "tool_calls": [tool],
                    "tool_call_count": 1,
                },
            )
        return httpx.Response(200, json={})

    transport = httpx.MockTransport(handler)
    for fn in ("post", "put", "delete", "get", "request"):

        def make(method_name):
            def call(*args, **kwargs):
                kwargs.pop("timeout", None)
                with httpx.Client(transport=transport) as client:
                    return getattr(client, method_name)(*args, **kwargs)

            return call

        monkeypatch.setattr(httpx, fn, make(fn))
    return calls


def test_run_applies_chaos_sends_requests_and_always_reverts(fake_lab, capsys):
    assert runner.run("tool-error") == 0
    methods = [(m, p) for m, p in fake_lab]
    assert methods[0] == ("PUT", "/chaos")
    assert methods.count(("POST", "/chat")) == 6  # 2 peticiones x 3 repeticiones
    assert methods[-2:] == [("DELETE", "/chaos"), ("DELETE", "/admin/overrides")]
    out = capsys.readouterr().out
    assert "Revertido" in out and "síntoma esperado no observado" not in out


def test_run_reverts_even_when_requests_fail(fake_lab, monkeypatch):
    def boom(*args, **kwargs):
        raise RuntimeError("runner crashed")

    monkeypatch.setattr(runner, "send", boom)
    with pytest.raises(RuntimeError):
        runner.run("slow-tool")
    assert fake_lab[-2:] == [("DELETE", "/chaos"), ("DELETE", "/admin/overrides")]


def test_expectations_flag_missing_symptoms():
    results = [{"http_status": 200, "latency_s": 1.0, "tool_calls": []}]
    problems = runner.check_expectations(
        {"tool": "check-order-status", "min_latency_s": 8, "http_status": 200}, results
    )
    assert len(problems) == 2
