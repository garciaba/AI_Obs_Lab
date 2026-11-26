"""Dataset de regresión: las 25 peticiones de Wonder Toys + los casos de cada escenario."""

from __future__ import annotations

import json
from importlib import resources

DATASET_NAME = "wonder-toys-regression"


def _load(name: str) -> list[dict]:
    return json.loads(resources.files("wonder_evals").joinpath(f"data/{name}").read_text())


def reference_requests() -> list[dict]:
    """Las 25 peticiones sintéticas de referencia (puerta de la fase 2)."""
    return _load("reference_requests.json")


def scenario_cases(scenario: str | None = None) -> list[dict]:
    cases = _load("scenario_cases.json")
    return [c for c in cases if scenario is None or c["scenario"] == scenario]


def dataset_examples(include_scenarios: bool = True) -> list[dict]:
    """Filas del dataset de AX. `messages` va serializado: AX guarda columnas planas."""
    rows = reference_requests() + (scenario_cases() if include_scenarios else [])
    return [
        {
            "tag": r["tag"],
            "category": r["category"],
            "scenario": r.get("scenario", ""),
            "user_id": r["user_id"],
            "input": r["messages"][-1]["content"],
            "messages": json.dumps(r["messages"], ensure_ascii=False),
        }
        for r in rows
    ]
