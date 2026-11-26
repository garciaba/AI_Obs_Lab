"""Runner de los escenarios de fallo del lab.

  scenario list                 escenarios disponibles
  scenario run <nombre>         provoca el fallo, lanza las peticiones y lo revierte
  scenario reset                quita cualquier caos o ajuste en caliente que quede
  scenario load [--repeat N]    las 25 peticiones de referencia, sin fallo (carga normal)
  scenario gate2                puerta de la fase 2: las 25 peticiones producen trazas

Cada escenario es un fichero YAML en `definitions/`. Son autorreversibles por tres vías:
el `finally` de este runner, el TTL de los ajustes en el servidor MCP y en el agente, y
(para db-down) el `defer` del Taskfile que vuelve a arrancar Postgres.
"""

from __future__ import annotations

import argparse
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from importlib import resources
from pathlib import Path

import httpx
import yaml
from wonder_evals import agent_client
from wonder_evals.dataset import reference_requests

MCP_ADMIN_URL = os.environ.get("MCP_ADMIN_URL", "http://mcp-server:8000")
DEFAULTS = {"SUBAGENT_MODEL_ID": "eu.anthropic.claude-haiku-5-5"}


def _definitions() -> Path:
    return Path(str(resources.files("wonder_scenarios").joinpath("definitions")))


def names() -> list[str]:
    return sorted(p.stem for p in _definitions().glob("*.yaml"))


def load(name: str) -> dict:
    path = _definitions() / f"{name}.yaml"
    if not path.exists():
        sys.exit(f"Escenario desconocido: {name}. Disponibles: {', '.join(names())}")
    text = path.read_text()
    for key, default in DEFAULTS.items():
        text = text.replace("${" + key + "}", os.environ.get(key, default))
    return yaml.safe_load(text)


def set_chaos(chaos: dict | None, ttl_seconds: int) -> None:
    if chaos:
        httpx.put(
            f"{MCP_ADMIN_URL}/chaos", json={**chaos, "ttl_seconds": ttl_seconds}, timeout=10
        ).raise_for_status()


def reset_all() -> None:
    """Deja el lab sin caos ni ajustes. Tolerante: los servicios pueden estar caídos."""
    for method, url in (
        ("DELETE", f"{MCP_ADMIN_URL}/chaos"),
        ("DELETE", f"{agent_client.AGENT_URL}/admin/overrides"),
    ):
        try:
            httpx.request(method, url, timeout=10)
        except httpx.HTTPError as exc:
            print(f"  aviso: no se pudo restablecer {url}: {exc}")


def _requests(spec: dict) -> list[dict]:
    if spec.get("requests_from") == "reference":
        rows = reference_requests()[: spec.get("request_limit") or None]
        return [{"tag": r["tag"], "messages": r["messages"], "user_id": r["user_id"]} for r in rows]
    return [
        {
            "tag": r["tag"],
            "messages": [{"role": "user", "content": r["message"]}],
            "user_id": r.get("user_id", "eval-user-001"),
        }
        for r in spec["requests"]
    ]


def send(requests: list[dict], *, concurrency: int, metadata: dict, timeout: float) -> list[dict]:
    def one(request: dict) -> dict:
        started = time.perf_counter()
        try:
            out = agent_client.chat(request, metadata=metadata, timeout=timeout)
        except httpx.HTTPError as exc:
            out = {"error": f"{type(exc).__name__}: {exc}", "http_status": 0, "tool_calls": []}
        out["tag"] = request["tag"]
        out["latency_s"] = round(time.perf_counter() - started, 1)
        return out

    with ThreadPoolExecutor(max_workers=max(1, concurrency)) as pool:
        return list(pool.map(one, requests))


def print_results(results: list[dict]) -> None:
    print(f"  {'petición':26} {'http':>4} {'seg':>6} {'herr.':>5}  {'trace_id':32}  herramientas")
    for r in results:
        calls = r.get("tool_calls") or []
        tools = (
            ",".join(c["tool"] + ("!" if c.get("status") == "error" else "") for c in calls) or "-"
        )
        note = f"  [{r['error']}]" if r.get("error") else ""
        print(
            f"  {r['tag'][:26]:26} {r.get('http_status', 0):>4} {r['latency_s']:>6} "
            f"{len(calls):>5}  {str(r.get('trace_id') or '-'):32}  {tools[:60]}{note[:80]}"
        )


def check_expectations(expect: dict, results: list[dict]) -> list[str]:
    """Comprobaciones blandas: avisan si el escenario no produjo el síntoma esperado."""
    problems: list[str] = []
    if not expect or not results:
        return problems
    calls = [c for r in results for c in (r.get("tool_calls") or [])]
    if "http_status" in expect and any(
        r.get("http_status") != expect["http_status"] for r in results
    ):
        problems.append(f"no todas las peticiones devolvieron HTTP {expect['http_status']}")
    tool = expect.get("tool")
    if tool and not any(c["tool"] == tool for c in calls):
        problems.append(f"ninguna petición llamó a la herramienta {tool}")
    if expect.get("tool_status") and not any(
        c.get("status") == expect["tool_status"] for c in calls if not tool or c["tool"] == tool
    ):
        problems.append(f"ninguna llamada a herramienta terminó con estado {expect['tool_status']}")
    if "min_latency_s" in expect and max(r["latency_s"] for r in results) < expect["min_latency_s"]:
        problems.append(f"ninguna petición tardó {expect['min_latency_s']} s o más")
    if (
        "min_tool_calls" in expect
        and max(len(r.get("tool_calls") or []) for r in results) < expect["min_tool_calls"]
    ):
        problems.append(f"ninguna petición hizo {expect['min_tool_calls']} llamadas o más")
    if expect.get("degraded") and not any(r.get("degraded") for r in results):
        problems.append("el agente no entró en modo degradado (sin memoria de sesión)")
    return problems


def run(name: str, *, with_experiment: bool = True) -> int:
    spec = load(name)
    ttl = int(spec.get("ttl_seconds", 900))
    timeout = float(spec.get("timeout_s", 300))
    stamp = time.strftime("%Y%m%d-%H%M%S")
    print(f"\n▶ Escenario {spec['name']}: {spec['title']}\n  {spec['summary']}")
    phases = spec.get("phases") or [
        {"label": spec["name"], "chaos": spec.get("chaos"), "overrides": spec.get("overrides")}
    ]
    use_experiment = (
        with_experiment
        and spec.get("experiment")
        and os.environ.get("ARIZE_API_KEY")
        and os.environ.get("ARIZE_SPACE_ID")
    )
    all_results: list[dict] = []
    try:
        for phase in phases:
            label = phase["label"]
            print(
                f"\n  Fase {label}: caos={phase.get('chaos') or '-'} ajustes={phase.get('overrides') or '-'}"
            )
            set_chaos(phase.get("chaos"), ttl)
            metadata = {"scenario": spec["name"], "scenario_phase": label}
            with agent_client.overrides(phase.get("overrides"), ttl_seconds=ttl):
                if use_experiment:
                    from wonder_evals import experiment

                    exp_name = f"{spec['name']}-{label}-{stamp}"
                    print(f"  Lanzando el experimento {exp_name!r} en Arize AX…")
                    experiment.run_ax(exp_name, metadata=metadata)
                else:
                    requests = _requests(spec) * int(spec.get("repeat", 1))
                    results = send(
                        requests,
                        concurrency=int(spec.get("concurrency", 1)),
                        metadata=metadata,
                        timeout=timeout,
                    )
                    print_results(results)
                    all_results.extend(results)
            if phase.get("chaos"):
                httpx.delete(f"{MCP_ADMIN_URL}/chaos", timeout=10)
    finally:
        reset_all()
        print("\n  ↺ Revertido: sin caos en el servidor MCP ni ajustes en el agente.")

    problems = check_expectations(spec.get("expect") or {}, all_results)
    look = spec.get("look_at", {})
    print(f"\n  Qué mirar (ficha: docs/scenarios/{spec['name']}.md)")
    print(f"    Arize AX : {look.get('arize', '-')}")
    print(f"    Dynatrace: {look.get('dynatrace', '-')}")
    for p in problems:
        print(f"  ⚠ síntoma esperado no observado: {p}")
    return 0


def load_traffic(repeat: int, concurrency: int) -> list[dict]:
    rows = reference_requests()
    requests = [
        {"tag": r["tag"], "messages": r["messages"], "user_id": r["user_id"]} for r in rows
    ] * repeat
    results = send(
        requests, concurrency=concurrency, metadata={"scenario": "reference"}, timeout=600
    )
    print_results(results)
    return results


def gate2(concurrency: int) -> int:
    print("Puerta 2: las 25 peticiones de referencia producen trazas de extremo a extremo\n")
    results = load_traffic(1, concurrency)
    ok = [r for r in results if r.get("http_status") == 200 and r.get("trace_id")]
    with_tools = [r for r in ok if r.get("tool_calls")]
    print(
        f"\n  {len(ok)}/25 peticiones respondidas con trace_id; {len(with_tools)} usaron herramientas MCP"
    )
    ids = Path(os.environ.get("EVAL_RESULTS_DIR", "results")) / "gate2-trace-ids.txt"
    ids.parent.mkdir(parents=True, exist_ok=True)
    ids.write_text("\n".join(f"{r['tag']} {r['trace_id']}" for r in ok) + "\n")
    print(f"  trace_ids guardados en evals/results/{ids.name}")
    print("  Comprueba una de ellas en ambos backends:")
    print('    Dynatrace: fetch spans | filter trace.id == toUid("<trace id>")')
    print("    Arize AX : proyecto → Traces → busca el trace id (raíz POST /chat, CHAIN)")
    if len(ok) < 25 or len(with_tools) < 15:
        print("\nPUERTA 2 NO SUPERADA")
        return 1
    print("\nPUERTA 2 OK (lado de la aplicación). Verifica la traza unida en Arize AX y Dynatrace.")
    return 0


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="scenario", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("list")
    run_p = sub.add_parser("run")
    run_p.add_argument("name")
    run_p.add_argument("--no-experiment", action="store_true")
    sub.add_parser("reset")
    load_p = sub.add_parser("load")
    load_p.add_argument("--repeat", type=int, default=1)
    load_p.add_argument("--concurrency", type=int, default=2)
    gate_p = sub.add_parser("gate2")
    gate_p.add_argument("--concurrency", type=int, default=2)
    args = parser.parse_args()

    if args.command == "list":
        for name in names():
            spec = load(name)
            print(f"{name:24} {spec['title']:28} {spec['summary']}")
    elif args.command == "run":
        sys.exit(run(args.name, with_experiment=not args.no_experiment))
    elif args.command == "reset":
        reset_all()
        print("Sin caos ni ajustes en caliente.")
    elif args.command == "load":
        load_traffic(args.repeat, args.concurrency)
    elif args.command == "gate2":
        sys.exit(gate2(args.concurrency))


if __name__ == "__main__":
    main()
