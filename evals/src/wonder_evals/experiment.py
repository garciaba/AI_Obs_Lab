"""Experimentos de regresión sobre el dataset.

La tarea de cada fila es una petición real al agente; los evaluadores son los de
`evaluators.py` con Claude Haiku 5.5 (Bedrock) como juez.

  - Con Arize AX (`run_ax`): el dataset y el experimento viven en AX
    (`client.datasets.create`, `client.experiments.run`).
  - Sin Arize (`run_local`): mismo recorrido, resultados en `results/<nombre>.json`.
    Lo usan el `--dry-run`, los tests y la comparación rápida de dos variantes.
"""

from __future__ import annotations

import json
import os
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

from wonder_evals import agent_client
from wonder_evals.dataset import DATASET_NAME, dataset_examples
from wonder_evals.evaluators import (
    CATALOG_FAITHFULNESS,
    CORRECTNESS,
    FORMAT_COMPLIANCE,
    PROMPT_INJECTION_SAFETY,
    TOOL_RESPONSE_HANDLING,
    TOOL_SELECTION,
    TRAJECTORY_CONVERGENCE,
    LlmEvaluator,
    image_url_correctness,
    tool_call_count,
)
from wonder_evals.judge import Judge, default_judge

RESULTS_DIR = Path(os.environ.get("EVAL_RESULTS_DIR", "results"))

# session_resolution es solo online (necesita la sesión completa en AX).
EXPERIMENT_LLM_EVALUATORS: tuple[LlmEvaluator, ...] = (
    CORRECTNESS,
    TOOL_SELECTION,
    TOOL_RESPONSE_HANDLING,
    FORMAT_COMPLIANCE,
    PROMPT_INJECTION_SAFETY,
    CATALOG_FAITHFULNESS,
    TRAJECTORY_CONVERGENCE,
)


def _judge_values(row: dict, output: dict) -> dict[str, str]:
    calls = output.get("tool_calls") or []
    return {
        "input": row.get("input", ""),
        "output": output.get("answer", "") or f"[agent error] {output.get('error', '')}",
        "tools_used": ", ".join(c["tool"] for c in calls) or "(none)",
        "tool_calls": "\n".join(f"{c['tool']}({json.dumps(c.get('args', {}))})" for c in calls)
        or "(none)",
        "tool_results": json.dumps(
            [{"tool": c["tool"], "args": c.get("args"), "output": c.get("output")} for c in calls],
            ensure_ascii=False,
        )[:12000]
        or "[]",
    }


def evaluate(row: dict, output: dict, judge: Judge) -> dict[str, dict]:
    """Aplica todos los evaluadores a una salida del agente."""
    values = _judge_values(row, output)
    # tool_selection juzga las llamadas con sus argumentos.
    results: dict[str, dict] = {}
    for evaluator in EXPERIMENT_LLM_EVALUATORS:
        v = dict(values)
        if evaluator.name == "tool_selection":
            v["tools_used"] = values["tool_calls"]
        try:
            label, explanation = judge.classify(evaluator, v)
            results[evaluator.name] = {
                "score": float(evaluator.choices[label]),
                "label": label,
                "explanation": explanation,
            }
        except Exception as exc:  # noqa: BLE001 - un fallo del juez no tumba el experimento
            results[evaluator.name] = {
                "score": None,
                "label": "judge_error",
                "explanation": str(exc),
            }
    score, label, explanation = image_url_correctness(output.get("answer", ""))
    results["image_url_correctness"] = {"score": score, "label": label, "explanation": explanation}
    score, label, explanation = tool_call_count(
        row.get("input", ""), int(output.get("tool_call_count") or 0)
    )
    results["tool_call_count"] = {"score": score, "label": label, "explanation": explanation}
    return results


def summarize(rows: list[dict]) -> dict[str, dict]:
    summary: dict[str, dict] = {}
    for row in rows:
        for name, result in row["evaluations"].items():
            entry = summary.setdefault(name, {"scores": [], "labels": {}})
            if result["score"] is not None:
                entry["scores"].append(result["score"])
            entry["labels"][result["label"]] = entry["labels"].get(result["label"], 0) + 1
    return {
        name: {
            "mean": round(sum(e["scores"]) / len(e["scores"]), 3) if e["scores"] else None,
            "n": len(e["scores"]),
            "labels": e["labels"],
        }
        for name, e in sorted(summary.items())
    }


def run_local(
    name: str,
    *,
    rows: list[dict] | None = None,
    judge: Judge | None = None,
    metadata: dict[str, str] | None = None,
    concurrency: int = 3,
    task=None,
) -> dict:
    """Ejecuta el experimento sin Arize y guarda `results/<name>.json`."""
    judge = judge or default_judge()
    rows = rows if rows is not None else dataset_examples()
    task = task or (
        lambda row: agent_client.chat(row, metadata={"experiment": name, **(metadata or {})})
    )

    def one(row: dict) -> dict:
        started = time.perf_counter()
        output = task(row)
        return {
            "tag": row["tag"],
            "category": row.get("category"),
            "input": row["input"],
            "answer": output.get("answer"),
            "error": output.get("error"),
            "trace_id": output.get("trace_id"),
            "tool_calls": [c["tool"] for c in output.get("tool_calls") or []],
            "latency_s": round(time.perf_counter() - started, 2),
            "input_tokens": output.get("input_tokens"),
            "output_tokens": output.get("output_tokens"),
            "evaluations": evaluate(row, output, judge),
        }

    with ThreadPoolExecutor(max_workers=concurrency) as pool:
        results = list(pool.map(one, rows))
    report = {
        "name": name,
        "metadata": metadata or {},
        "judge": getattr(judge, "model_id", "unknown"),
        "rows": results,
        "summary": summarize(results),
    }
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    (RESULTS_DIR / f"{name}.json").write_text(json.dumps(report, indent=2, ensure_ascii=False))
    return report


def compare(baseline: dict, candidate: dict) -> list[dict]:
    """Diferencia de la media de cada evaluador entre dos experimentos."""
    rows = []
    for name in sorted(set(baseline["summary"]) | set(candidate["summary"])):
        a = baseline["summary"].get(name, {}).get("mean")
        b = candidate["summary"].get(name, {}).get("mean")
        delta = round(b - a, 3) if a is not None and b is not None else None
        rows.append({"evaluator": name, "baseline": a, "candidate": b, "delta": delta})
    return rows


def format_comparison(baseline: dict, candidate: dict) -> str:
    lines = [
        f"{'evaluator':28} {baseline['name'][:18]:>18} {candidate['name'][:18]:>18} {'delta':>8}"
    ]
    for r in compare(baseline, candidate):
        fmt = lambda v: "-" if v is None else f"{v:.3f}"  # noqa: E731
        mark = "" if not r["delta"] else ("  ▲" if r["delta"] > 0 else "  ▼")
        lines.append(
            f"{r['evaluator']:28} {fmt(r['baseline']):>18} {fmt(r['candidate']):>18} "
            f"{fmt(r['delta']):>8}{mark}"
        )
    return "\n".join(lines)


# ─────────────────────────────── Arize AX ───────────────────────────────


def ax_client():
    from arize import ArizeClient

    kwargs: dict[str, Any] = {"api_key": os.environ["ARIZE_API_KEY"]}
    if os.environ.get("ARIZE_REGION"):
        kwargs["region"] = os.environ["ARIZE_REGION"]
    return ArizeClient(**kwargs)


def ensure_dataset(client, space: str, name: str = DATASET_NAME, recreate: bool = False):
    """Devuelve el dataset de regresión en AX, creándolo si no existe."""
    existing = None
    try:
        existing = client.datasets.get(dataset=name, space=space)
    except Exception:  # noqa: BLE001 - no existe todavía
        existing = None
    if existing is not None and recreate:
        client.datasets.delete(dataset=name, space=space)
        existing = None
    if existing is None:
        existing = client.datasets.create(name=name, space=space, examples=dataset_examples())
    return existing


def run_ax(
    name: str,
    *,
    judge: Judge | None = None,
    metadata: dict[str, str] | None = None,
    concurrency: int = 3,
    dry_run: bool = False,
):
    """Lanza el experimento en Arize AX con `client.experiments.run()`."""
    from arize.experiments import EvaluationResult

    judge = judge or default_judge()
    space = os.environ["ARIZE_SPACE_ID"]
    client = ax_client()
    ensure_dataset(client, space)
    meta = {"experiment": name, **(metadata or {})}

    def task(dataset_row) -> dict:
        output = agent_client.chat(dict(dataset_row), metadata=meta)
        # Solo lo que los evaluadores y el análisis en AX necesitan.
        return {
            "answer": output.get("answer", ""),
            "error": output.get("error"),
            "trace_id": output.get("trace_id"),
            "tool_calls": output.get("tool_calls") or [],
            "tool_call_count": output.get("tool_call_count", 0),
            "supervisor_model": output.get("supervisor_model"),
        }

    cache: dict[int, dict] = {}

    def make(evaluator_name: str):
        def evaluator(experiment_output, dataset_row) -> EvaluationResult:
            key = id(experiment_output)
            if key not in cache:
                cache[key] = evaluate(dict(dataset_row), experiment_output or {}, judge)
            r = cache[key][evaluator_name]
            return EvaluationResult(
                score=r["score"], label=r["label"], explanation=r["explanation"]
            )

        evaluator.__name__ = evaluator_name
        return evaluator

    names = [e.name for e in EXPERIMENT_LLM_EVALUATORS] + [
        "image_url_correctness",
        "tool_call_count",
    ]
    return client.experiments.run(
        name=name,
        dataset=DATASET_NAME,
        space=space,
        task=task,
        evaluators={n: make(n) for n in names},
        concurrency=concurrency,
        dry_run=dry_run,
        metadata=meta,
        timeout=600,
    )
