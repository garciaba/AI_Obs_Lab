"""Tests de las evals sin Arize ni Vertex AI (juez simulado)."""

import json

import pytest
from wonder_evals import experiment, online
from wonder_evals.dataset import dataset_examples, reference_requests, scenario_cases
from wonder_evals.evaluators import (
    ALL_NAMES,
    CODE_EVALUATORS,
    LLM_EVALUATORS,
    image_url_correctness,
    tool_call_count,
)
from wonder_evals.judge import FakeJudge


def test_dataset_has_the_25_reference_requests_plus_scenario_cases():
    assert len(reference_requests()) == 25
    assert {c["scenario"] for c in scenario_cases()} >= {
        "slow-tool",
        "tool-error",
        "db-down",
        "bad-args",
        "catalog-hallucination",
        "prompt-injection",
        "tool-loop",
    }
    rows = dataset_examples()
    assert len({r["tag"] for r in rows}) == len(rows)
    assert all(json.loads(r["messages"])[-1]["role"] == "user" for r in rows)


def test_eight_plus_two_evaluators_are_defined_and_renderable():
    assert {
        "correctness",
        "tool_selection",
        "tool_response_handling",
        "format_compliance",
        "image_url_correctness",
        "tool_call_count",
        "prompt_injection_safety",
        "catalog_faithfulness",
    } <= set(ALL_NAMES)
    for e in LLM_EVALUATORS:
        rendered = e.render({k: f"<{k}>" for k in e.column_mappings})
        for key in e.column_mappings:
            assert f"<{key}>" in rendered, f"{e.name}: variable {key} no usada en la plantilla"
        assert "{" + next(iter(e.column_mappings)) + "}" not in rendered
        for label in e.choices:
            assert label.lower() in e.template.lower(), f"{e.name}: etiqueta {label} ausente"


def test_code_evaluators_match_wonder_toys_behaviour():
    assert image_url_correctness("no images here")[1] == "no_images"
    assert image_url_correctness("![a](/product-images/toy-001.png)")[0] == 1.0
    assert image_url_correctness("![a](https://evil.example/x.png)")[1] == "invalid"
    assert tool_call_count("Show me dinosaur toys", 0)[1] == "too_few"
    assert tool_call_count("hello", 0)[1] == "appropriate"
    assert tool_call_count("find toys", 9)[1] == "excessive"
    assert tool_call_count("find toys", 2)[0] == 1.0


def test_ax_code_evaluators_compile_and_agree_with_local_ones():
    class EvaluationResult(dict):
        def __init__(self, **kw):
            super().__init__(**kw)

    class CodeEvaluator:
        pass

    import re

    scope = {
        "EvaluationResult": EvaluationResult,
        "CodeEvaluator": CodeEvaluator,
        "re": re,
        "json": json,
    }
    for spec in CODE_EVALUATORS:
        exec(spec.code, scope)  # noqa: S102 - código propio del repo
    image = scope["ImageUrlCorrectnessEvaluator"]().evaluate(
        output="![a](/product-images/toy-001.png)"
    )
    assert image["label"] == "valid"
    count = scope["ToolCallCountEvaluator"]().evaluate(input="find toys", tool_call_count="7")
    assert count["label"] == "excessive"


def test_local_experiment_runs_all_evaluators_and_compares(tmp_path, monkeypatch):
    monkeypatch.setattr(experiment, "RESULTS_DIR", tmp_path)
    rows = dataset_examples()[:3]

    def task(row):
        return {
            "answer": "![Toy](/product-images/toy-001.png) **Toy** — $9.99",
            "tool_calls": [
                {
                    "agent": "catalog_agent",
                    "tool": "search-products",
                    "args": {"query": "x"},
                    "output": "{}",
                }
            ],
            "tool_call_count": 1,
            "trace_id": "0" * 32,
        }

    base = experiment.run_local("a", rows=rows, judge=FakeJudge(), task=task)
    assert set(base["summary"]) == set(ALL_NAMES) - {"session_resolution"}
    assert base["summary"]["image_url_correctness"]["mean"] == 1.0
    assert (tmp_path / "a.json").exists()

    worse = experiment.run_local(
        "b",
        rows=rows,
        judge=FakeJudge(),
        task=lambda row: {**task(row), "answer": "![Toy](http://x/y.png)"},
    )
    delta = {r["evaluator"]: r["delta"] for r in experiment.compare(base, worse)}
    assert delta["image_url_correctness"] == -1.0
    assert "image_url_correctness" in experiment.format_comparison(base, worse)


def test_online_task_specs_reference_known_evaluators_and_build_sdk_types():
    specs = online.load_task_specs()
    assert {s["name"] for s in specs} == {
        "agentic-lab-span-llm-evals",
        "agentic-lab-span-code-evals",
        "agentic-lab-trajectory-evals",
        "agentic-lab-session-evals",
    }
    used = {e["name"] for s in specs for e in s["evaluators"]}
    assert used == set(ALL_NAMES)
    # dry-run construye los tipos del SDK de Arize v8 sin red: valida las formas.
    actions = online.apply(dry_run=True)
    assert len(actions) == 4 and all(a.startswith("[dry-run]") for a in actions)


def test_online_apply_rejects_unknown_evaluator(tmp_path, monkeypatch):
    (tmp_path / "x.yaml").write_text(
        "tasks:\n  - name: t\n    task_type: TEMPLATE_EVALUATION\n    evaluators:\n      - name: nope\n"
    )
    monkeypatch.setattr(online, "TASKS_DIR", tmp_path)
    with pytest.raises(SystemExit):
        online.apply(dry_run=True)
