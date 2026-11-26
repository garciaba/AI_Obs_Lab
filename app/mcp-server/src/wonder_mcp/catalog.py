"""Búsqueda vectorial del catálogo en ChromaDB (RAG).

Los embeddings se calculan en este proceso con el modelo por defecto de Chroma
(all-MiniLM-L6-v2, ONNX); el servidor de Chroma solo almacena y busca vectores.
"""

from __future__ import annotations

import logging
from typing import Protocol
from urllib.parse import urlparse

logger = logging.getLogger(__name__)


class VectorIndex(Protocol):
    def search(self, query: str, n_results: int, where: dict | None) -> list[dict] | None:
        """Devuelve [{id, document, score}] o None si el índice no está disponible."""


class ChromaIndex:
    def __init__(self, chroma_url: str, collection: str) -> None:
        self._url = chroma_url
        self._collection_name = collection
        self._client = None

    def _collection(self):
        import chromadb

        if self._client is None:
            parsed = urlparse(self._url)
            self._client = chromadb.HttpClient(
                host=parsed.hostname or "localhost",
                port=parsed.port or 8000,
                ssl=(parsed.scheme == "https"),
            )
        return self._client.get_collection(name=self._collection_name)

    def search(self, query: str, n_results: int = 20, where: dict | None = None):
        try:
            kwargs: dict = {
                "query_texts": [query],
                "n_results": n_results,
                "include": ["documents", "distances"],
            }
            if where:
                kwargs["where"] = where
            res = self._collection().query(**kwargs)
        except Exception:
            logger.exception("ChromaDB query failed; falling back to keyword search")
            self._client = None
            return None
        ids = res["ids"][0] if res.get("ids") else []
        docs = (res.get("documents") or [[]])[0]
        dists = (res.get("distances") or [[]])[0]
        return [
            {
                "id": pid,
                "document": docs[i] if i < len(docs) else "",
                # Chroma devuelve distancia (menor = mejor); se expone como score 1 - d.
                "score": round(1.0 - float(dists[i]), 4) if i < len(dists) else None,
            }
            for i, pid in enumerate(ids)
        ]


class NullIndex:
    """Índice ausente: fuerza el camino de búsqueda por palabras clave."""

    def search(self, query: str, n_results: int = 20, where: dict | None = None):
        return None
