#!/usr/bin/env python3
"""Comprueba, sobre la salida a fichero del colector de referencia, que el pipeline hace
lo que pide el diseño. Es la versión sin credenciales de las puertas 1 y 2.

Uso: verify_pipeline.py <dir-salida> <trace_id> [--tree] [--poc]

Con --poc verifica la traza sintética de `gate 1` (sin agente ni servidor MCP): solo el
reparto, la redacción y el tratamiento de los spans HTTP del SDK de Anthropic.

Comprobaciones para la traza indicada:
  Dynatrace  recibe todos los spans (agente y servidor MCP en una sola traza, con spans de
             HTTP y de base de datos) y ninguno lleva contenido de prompts o respuestas.
  Arize AX   recibe solo spans de IA, con contenido completo; la raíz HTTP llega marcada
             como CHAIN, con session.id, y el proyecto va como atributo de recurso.
  Logs       hay logs OTLP de la app y logs de contenedor con el trace_id de la traza.
  Métricas   llegan las métricas de la app y de los contenedores; los contadores, en delta.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

REDACTED = re.compile(
    r"^(input\.value|output\.value|wonder\.tool_results|llm\.(input|output)_messages\..*|llm\.prompts?\..*|"
    r"llm\.tools.*|gen_ai\.(input|output)\.messages|gen_ai\.system_instructions|"
    r"gen_ai\.tool\.call\.(arguments|result)|retrieval\.documents\..*\.content)$"
)
AI_KEYS = (
    "openinference.span.kind",
    "gen_ai.operation.name",
    "gen_ai.request.model",
    "gen_ai.tool.name",
)


def _attrs(items) -> dict:
    return {a["key"]: next(iter(a["value"].values()), None) for a in items or []}


def load_spans(path: Path) -> list[dict]:
    spans = []
    if not path.exists():
        return spans
    for line in path.read_text().splitlines():
        for rs in json.loads(line).get("resourceSpans", []):
            resource = _attrs(rs.get("resource", {}).get("attributes"))
            for ss in rs.get("scopeSpans", []):
                for span in ss.get("spans", []):
                    span["resource"] = resource
                    span["scope"] = ss.get("scope", {}).get("name", "")
                    span["attrs"] = _attrs(span.get("attributes"))
                    spans.append(span)
    return spans


def load_logs(path: Path) -> list[dict]:
    logs = []
    if not path.exists():
        return logs
    for line in path.read_text().splitlines():
        for rl in json.loads(line).get("resourceLogs", []):
            resource = _attrs(rl.get("resource", {}).get("attributes"))
            for sl in rl.get("scopeLogs", []):
                for record in sl.get("logRecords", []):
                    record["resource"] = resource
                    record["attrs"] = _attrs(record.get("attributes"))
                    logs.append(record)
    return logs


def load_metrics(path: Path) -> dict[str, set]:
    metrics: dict[str, set] = {}
    if not path.exists():
        return metrics
    for line in path.read_text().splitlines():
        for rm in json.loads(line).get("resourceMetrics", []):
            for sm in rm.get("scopeMetrics", []):
                for m in sm.get("metrics", []):
                    for kind in ("sum", "histogram", "gauge", "exponentialHistogram"):
                        if kind in m:
                            temporality = m[kind].get("aggregationTemporality", 0)
                            monotonic = m[kind].get("isMonotonic", kind == "histogram")
                            metrics.setdefault(m["name"], set()).add((kind, temporality, monotonic))
    return metrics


def print_tree(title: str, spans: list[dict]) -> None:
    by_id = {s["spanId"]: s for s in spans}

    def depth(s: dict) -> int:
        d = 0
        while s.get("parentSpanId") in by_id:
            s = by_id[s["parentSpanId"]]
            d += 1
        return d

    print(f"\n{title} ({len(spans)} spans)")
    for s in sorted(spans, key=lambda s: int(s["startTimeUnixNano"])):
        kind = s["attrs"].get("openinference.span.kind", "-")
        service = s["resource"].get("service.name", "?")
        print(f"  {'  ' * depth(s)}{s['name'][:50]:50} {service:18} {kind}")


def main() -> int:
    if len(sys.argv) < 3:
        print(__doc__)
        return 2
    out, trace_id = Path(sys.argv[1]), sys.argv[2].lower()
    failures: list[str] = []

    def check(ok: bool, message: str) -> None:
        print(f"  {'ok  ' if ok else 'FAIL'} {message}")
        if not ok:
            failures.append(message)

    poc = "--poc" in sys.argv
    dt = [s for s in load_spans(out / "dynatrace-traces.jsonl") if s["traceId"] == trace_id]
    ax = [s for s in load_spans(out / "arize-traces.jsonl") if s["traceId"] == trace_id]
    if "--tree" in sys.argv:
        print_tree("Dynatrace", dt)
        print_tree("Arize AX", ax)

    print(f"\nDynatrace: traza {trace_id}")
    services = {s["resource"].get("service.name") for s in dt}
    check(len(dt) > 0, f"la traza llega ({len(dt)} spans)")
    if not poc:
        check(
            {"wonder-toys-agent", "wonder-toys-mcp"} <= services,
            f"agente y servidor MCP en la misma traza ({sorted(x for x in services if x)})",
        )
    ids = {s["spanId"] for s in dt}
    orphans = [s["name"] for s in dt if s.get("parentSpanId") and s["parentSpanId"] not in ids]
    check(not orphans, f"traza unida, sin spans huérfanos {orphans[:3] or ''}")
    roots = [s for s in dt if not s.get("parentSpanId")]
    check(len(roots) == 1, "una única raíz")
    check(
        any(s["attrs"].get("db.system") or s["attrs"].get("db.system.name") for s in dt),
        "incluye spans de base de datos",
    )
    check(
        any(s["attrs"].get("openinference.span.kind") == "LLM" for s in dt),
        "incluye los spans de IA (árbol sin huecos)",
    )
    leaked = sorted({k for s in dt for k in s["attrs"] if REDACTED.match(k)})
    check(not leaked, f"contenido de prompts redactado {leaked[:4] or ''}")
    check(
        any("gen_ai.request.model" in s["attrs"] for s in dt),
        "conserva atributos gen_ai.* (modelo, tokens)",
    )

    sdk_dt = [s for s in dt if s["scope"].startswith("com.anthropic.sdk")]
    if poc or sdk_dt:
        check(bool(sdk_dt), f"incluye los spans HTTP del SDK de Anthropic ({len(sdk_dt)})")
        check(
            not any(k.startswith("gen_ai.usage.") for s in sdk_dt for k in s["attrs"]),
            "sin tokens duplicados en los spans HTTP del SDK",
        )

    print("\nArize AX")
    check(len(ax) > 0, f"la traza llega ({len(ax)} spans)")
    non_ai = [s["name"] for s in ax if not any(k in s["attrs"] for k in AI_KEYS)]
    check(not non_ai, f"solo spans de IA {non_ai[:3] or ''}")
    ax_roots = [s for s in ax if not s.get("parentSpanId")]
    check(
        len(ax_roots) == 1 and ax_roots[0]["attrs"].get("openinference.span.kind") == "CHAIN",
        "la raíz llega marcada como CHAIN",
    )
    if ax_roots:
        root = ax_roots[0]["attrs"]
        check(
            bool(root.get("input.value")) and "output.value" in root,
            "la raíz lleva entrada y salida",
        )
        check(bool(root.get("session.id")), "la raíz lleva session.id")
    check(
        not any(s["scope"].startswith("com.anthropic.sdk") for s in ax),
        "sin spans HTTP del SDK de Anthropic (no duplican el span LLM)",
    )
    kinds = {s["attrs"].get("openinference.span.kind") for s in ax}
    check(
        ({"LLM", "CHAIN"} if poc else {"LLM", "TOOL", "CHAIN"}) <= kinds,
        f"tipos de span presentes: {sorted(k for k in kinds if k)}",
    )
    check(
        any(k.startswith("llm.input_messages") for s in ax for k in s["attrs"]),
        "contenido completo de los mensajes del LLM",
    )
    check(
        all(s["resource"].get("openinference.project.name") for s in ax),
        "proyecto de AX como atributo de recurso",
    )
    ax_ids = {s["spanId"] for s in ax}
    check(ax_ids <= ids, "mismos span_id que en Dynatrace (no se reescriben)")

    if poc:
        logs = load_logs(out / "dynatrace-logs.jsonl")
        check(
            any(r.get("traceId", "").lower() == trace_id for r in logs),
            "log de la prueba con el trace_id",
        )
        print(
            f"\n{'PIPELINE OK' if not failures else f'{len(failures)} comprobación(es) fallida(s)'}"
        )
        return 1 if failures else 0

    print("\nLogs")
    logs = load_logs(out / "dynatrace-logs.jsonl")
    of_trace = [r for r in logs if r.get("traceId", "").lower() == trace_id]
    app_logs = [r for r in of_trace if r["attrs"].get("log.source") != "docker"]
    docker_logs = [r for r in of_trace if r["attrs"].get("log.source") == "docker"]
    check(bool(app_logs), f"logs de aplicación (OTLP) con el trace_id ({len(app_logs)})")
    check(bool(docker_logs), f"logs de contenedor con el trace_id ({len(docker_logs)})")
    check(
        any(r["resource"].get("docker.compose.service") for r in docker_logs),
        "logs de contenedor con el servicio de compose",
    )

    print("\nMétricas")
    metrics = load_metrics(out / "dynatrace-metrics.jsonl")
    check(
        "wonder.agent.requests" in metrics and "wonder.mcp.tool.calls" in metrics,
        "métricas de la aplicación",
    )
    check(any(n.startswith("container.") for n in metrics), "métricas de contenedores")
    cumulative = sorted(n for n, v in metrics.items() if any(t == 2 and mono for _, t, mono in v))
    check(not cumulative, f"contadores e histogramas en delta {cumulative[:3] or ''}")

    print(f"\n{'PIPELINE OK' if not failures else f'{len(failures)} comprobación(es) fallida(s)'}")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
