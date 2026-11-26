"""Configuración del agente y ajustes en caliente (con caducidad) para los escenarios."""

from __future__ import annotations

import os
import time
from dataclasses import dataclass, field
from typing import Any


def _env(name: str, default: str) -> str:
    return os.environ.get(name, default)


@dataclass(frozen=True)
class Settings:
    llm_provider: str = field(default_factory=lambda: _env("LLM_PROVIDER", "bedrock"))
    aws_region: str = field(default_factory=lambda: _env("AWS_REGION", "eu-west-1"))
    supervisor_model: str = field(
        default_factory=lambda: _env("SUPERVISOR_MODEL_ID", "eu.anthropic.claude-sonnet-5-5")
    )
    subagent_model: str = field(
        default_factory=lambda: _env("SUBAGENT_MODEL_ID", "eu.anthropic.claude-haiku-5-5")
    )
    max_tokens: int = field(default_factory=lambda: int(_env("LLM_MAX_TOKENS", "2048")))
    bedrock_max_attempts: int = field(
        default_factory=lambda: int(_env("BEDROCK_MAX_ATTEMPTS", "4"))
    )
    bedrock_read_timeout_s: int = field(
        default_factory=lambda: int(_env("BEDROCK_READ_TIMEOUT_SECONDS", "120"))
    )
    mcp_url: str = field(default_factory=lambda: _env("MCP_URL", "http://mcp-server:8000/mcp"))
    database_url: str = field(
        default_factory=lambda: _env(
            "DATABASE_URL", "postgresql://wonder:wonder@postgres:5432/wonder"
        )
    )
    supervisor_recursion_limit: int = field(
        default_factory=lambda: int(_env("SUPERVISOR_RECURSION_LIMIT", "12"))
    )
    subagent_recursion_limit: int = field(
        default_factory=lambda: int(_env("SUBAGENT_RECURSION_LIMIT", "12"))
    )
    overrides_default_ttl_s: int = field(
        default_factory=lambda: int(_env("OVERRIDES_DEFAULT_TTL_SECONDS", "900"))
    )


_ALLOWED = {"supervisor_model", "subagent_recursion_limit", "supervisor_recursion_limit"}


class RuntimeOverrides:
    """Ajustes temporales que usan los escenarios `model-swap` y `tool-loop`.

    Caducan solos (TTL): si el runner del escenario se interrumpe, el agente vuelve a su
    configuración de arranque sin intervención.
    """

    def __init__(self, default_ttl_s: int = 900) -> None:
        self._default_ttl_s = default_ttl_s
        self._values: dict[str, Any] = {}
        self._expires_at: float | None = None

    def _current(self) -> dict[str, Any]:
        if self._expires_at is not None and time.monotonic() >= self._expires_at:
            self.reset()
        return self._values

    def get(self, key: str, default: Any) -> Any:
        return self._current().get(key, default)

    def apply(self, payload: dict[str, Any]) -> dict[str, Any]:
        values = {k: v for k, v in payload.items() if k != "ttl_seconds" and v is not None}
        unknown = set(values) - _ALLOWED
        if unknown:
            raise ValueError(f"unknown override(s): {', '.join(sorted(unknown))}")
        for key in ("subagent_recursion_limit", "supervisor_recursion_limit"):
            if key in values:
                values[key] = int(values[key])
                if not 2 <= values[key] <= 200:
                    raise ValueError(f"{key} must be between 2 and 200")
        ttl = int(payload.get("ttl_seconds", self._default_ttl_s))
        if not 0 < ttl <= 3600:
            raise ValueError("ttl_seconds must be between 1 and 3600")
        self._values = values
        self._expires_at = time.monotonic() + ttl
        return self.to_dict()

    def reset(self) -> None:
        self._values = {}
        self._expires_at = None

    def to_dict(self) -> dict[str, Any]:
        values = dict(self._current())
        remaining = None
        if self._expires_at is not None:
            remaining = max(0, round(self._expires_at - time.monotonic()))
        return {"overrides": values, "expires_in_seconds": remaining}
