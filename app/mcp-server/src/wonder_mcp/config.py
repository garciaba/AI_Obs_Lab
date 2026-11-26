"""Configuración del servidor MCP (variables de entorno)."""

from __future__ import annotations

import os
from dataclasses import dataclass, field


@dataclass(frozen=True)
class Settings:
    database_url: str = field(
        default_factory=lambda: os.environ.get(
            "DATABASE_URL", "postgresql://wonder:wonder@postgres:5432/wonder"
        )
    )
    chroma_url: str = field(
        default_factory=lambda: os.environ.get("CHROMA_URL", "http://chroma:8000")
    )
    chroma_collection: str = field(
        default_factory=lambda: os.environ.get("CHROMA_COLLECTION", "products")
    )
    host: str = field(default_factory=lambda: os.environ.get("MCP_HOST", "0.0.0.0"))
    port: int = field(default_factory=lambda: int(os.environ.get("MCP_PORT", "8000")))
    db_timeout_s: float = field(
        default_factory=lambda: float(os.environ.get("DB_TIMEOUT_SECONDS", "3"))
    )
    chaos_default_ttl_s: int = field(
        default_factory=lambda: int(os.environ.get("CHAOS_DEFAULT_TTL_SECONDS", "600"))
    )
