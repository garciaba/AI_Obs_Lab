import json
import os
from pathlib import Path

import pytest

os.environ.setdefault("TELEMETRY_ENABLED", "false")

PRODUCTS = json.loads(
    (Path(__file__).parents[2] / "seed/src/wonder_seed/products.json").read_text()
)


class FakeIndex:
    """Índice vectorial falso: devuelve los productos cuyo nombre contiene la consulta."""

    def __init__(self, products):
        self.products = products
        self.available = True

    def search(self, query, n_results=20, where=None):
        if not self.available:
            return None
        q = query.lower()
        hits = [p for p in self.products if any(w in p["name"].lower() for w in q.split())]
        return [
            {"id": p["id"], "document": p["name"], "score": 0.9} for p in hits[:n_results]
        ]


@pytest.fixture
def products():
    return PRODUCTS


@pytest.fixture
def fake_index(products):
    return FakeIndex(products)
