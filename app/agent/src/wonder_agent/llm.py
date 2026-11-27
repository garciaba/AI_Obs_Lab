"""Fábrica de modelos: Claude en Vertex AI (Google Cloud) o un modelo simulado.

`LLM_PROVIDER=vertex` (por defecto) usa `ChatAnthropicVertex`: los modelos de Anthropic
servidos por Google en Vertex AI Model Garden, con las credenciales de la cuenta de
servicio del lab (GOOGLE_APPLICATION_CREDENTIALS). `LLM_PROVIDER=fake` usa un modelo
determinista sin red, para los tests unitarios y el smoke test de CI (LLM simulado).
"""

from __future__ import annotations

from functools import lru_cache

from langchain_core.language_models.chat_models import BaseChatModel

from wonder_agent.config import Settings


@lru_cache(maxsize=8)
def _vertex(
    model_id: str, project: str, region: str, max_tokens: int, max_retries: int, timeout: int
):
    from langchain_google_vertexai.model_garden import ChatAnthropicVertex

    return ChatAnthropicVertex(
        model_name=model_id,
        project=project or None,  # sin valor, el del fichero de credenciales
        location=region,
        max_output_tokens=max_tokens,
        # Reintentos con backoff ante 429 (cuota agotada) y 5xx. Cada intento es una
        # llamada HTTP visible en Dynatrace (escenario de throttling).
        max_retries=max_retries,
        timeout=float(timeout),
    )


def get_model(settings: Settings, model_id: str, role: str) -> BaseChatModel:
    if settings.llm_provider == "fake":
        from wonder_agent.fake_llm import FakeToyStoreModel

        return FakeToyStoreModel(role=role, model_id=model_id)
    return _vertex(
        model_id,
        settings.gcp_project,
        settings.gcp_region,
        settings.max_tokens,
        settings.llm_max_retries,
        settings.llm_timeout_s,
    )
