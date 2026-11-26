import json
import os
from pathlib import Path

import pytest

os.environ["TELEMETRY_ENABLED"] = "false"
os.environ["LLM_PROVIDER"] = "fake"
os.environ["DATABASE_URL"] = "memory"

PRODUCTS = json.loads(
    (Path(__file__).parents[2] / "seed/src/wonder_seed/products.json").read_text()
)


@pytest.fixture
def products():
    return PRODUCTS
