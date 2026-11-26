"""Fábrica de modelos: Claude en Amazon Bedrock (Converse) o un modelo simulado.

`LLM_PROVIDER=bedrock` (por defecto) usa `ChatBedrockConverse` con los perfiles de
inferencia EU. `LLM_PROVIDER=fake` usa un modelo determinista sin red, para los tests
unitarios y el smoke test de CI (Bedrock simulado).
"""

from __future__ import annotations

from functools import lru_cache

from langchain_core.language_models.chat_models import BaseChatModel

from wonder_agent.config import Settings


@lru_cache(maxsize=8)
def _bedrock(model_id: str, region: str, max_tokens: int, max_attempts: int, read_timeout: int):
    from botocore.config import Config
    from langchain_aws import ChatBedrockConverse

    return ChatBedrockConverse(
        model=model_id,
        region_name=region,
        max_tokens=max_tokens,
        # Reintentos "standard" de botocore: backoff exponencial ante ThrottlingException.
        # Cada intento es una llamada HTTP visible en Dynatrace (escenario de throttling).
        config=Config(
            retries={"max_attempts": max_attempts, "mode": "standard"},
            read_timeout=read_timeout,
            connect_timeout=10,
        ),
    )


def get_model(settings: Settings, model_id: str, role: str) -> BaseChatModel:
    if settings.llm_provider == "fake":
        from wonder_agent.fake_llm import FakeToyStoreModel

        return FakeToyStoreModel(role=role, model_id=model_id)
    return _bedrock(
        model_id,
        settings.aws_region,
        settings.max_tokens,
        settings.bedrock_max_attempts,
        settings.bedrock_read_timeout_s,
    )
