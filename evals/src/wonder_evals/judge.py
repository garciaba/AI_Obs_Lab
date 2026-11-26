"""Juez LLM de los experimentos: Claude Haiku 5.5 en Bedrock (perfil de inferencia EU)."""

from __future__ import annotations

import os
from functools import lru_cache
from typing import Protocol

from wonder_evals.evaluators import LlmEvaluator


class Judge(Protocol):
    def classify(self, evaluator: LlmEvaluator, values: dict[str, str]) -> tuple[str, str]:
        """Devuelve (label, explanation)."""


class BedrockJudge:
    def __init__(self, model_id: str | None = None, region: str | None = None) -> None:
        from botocore.config import Config
        from langchain_aws import ChatBedrockConverse

        self.model_id = model_id or os.environ.get(
            "JUDGE_MODEL_ID", "eu.anthropic.claude-haiku-5-5"
        )
        self._llm = ChatBedrockConverse(
            model=self.model_id,
            region_name=region or os.environ.get("AWS_REGION", "eu-west-1"),
            max_tokens=600,
            config=Config(retries={"max_attempts": 8, "mode": "adaptive"}, read_timeout=120),
        )

    def classify(self, evaluator: LlmEvaluator, values: dict[str, str]) -> tuple[str, str]:
        labels = list(evaluator.choices)
        schema = {
            "title": "verdict",
            "description": "Evaluation verdict",
            "type": "object",
            "properties": {
                "explanation": {
                    "type": "string",
                    "description": "One or two sentences justifying the label",
                },
                "label": {"type": "string", "enum": labels},
            },
            "required": ["explanation", "label"],
        }
        result = self._llm.with_structured_output(schema).invoke(evaluator.render(values))
        label = str(result.get("label", "")).strip().lower()
        if label not in evaluator.choices:
            raise ValueError(f"judge returned an unknown label {label!r} for {evaluator.name}")
        return label, str(result.get("explanation", ""))


class FakeJudge:
    """Juez determinista para tests y `--dry-run` sin Bedrock: siempre la primera etiqueta."""

    model_id = "fake-judge"

    def classify(self, evaluator: LlmEvaluator, values: dict[str, str]) -> tuple[str, str]:
        return next(iter(evaluator.choices)), "fake judge"


@lru_cache(maxsize=1)
def default_judge() -> Judge:
    if os.environ.get("JUDGE_PROVIDER", "bedrock") == "fake":
        return FakeJudge()
    return BedrockJudge()
