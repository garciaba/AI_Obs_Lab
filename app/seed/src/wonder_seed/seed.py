"""Carga inicial del lab: esquema y catálogo en Postgres, catálogo vectorial en ChromaDB.

Idempotente: se puede relanzar (`task seed`) para dejar inventario, pedidos de ejemplo e
índice vectorial en su estado inicial.

Uso: wonder-seed [--only postgres|chroma] [--keep-orders]
"""

from __future__ import annotations

import argparse
import json
import os
import time
from importlib import resources
from urllib.parse import urlparse

DATABASE_URL = os.environ.get("DATABASE_URL", "postgresql://wonder:wonder@postgres:5432/wonder")
CHROMA_URL = os.environ.get("CHROMA_URL", "http://chroma:8000")
COLLECTION = os.environ.get("CHROMA_COLLECTION", "products")
BATCH_SIZE = 50

# Pedidos de ejemplo del usuario de las evals, uno por estado, para que las peticiones de
# seguimiento y cancelación tengan datos desde el primer momento.
DEMO_USER = "eval-user-001"
DEMO_ORDERS = [
    ("DEMO0001", "processing", [("toy-003", 1)]),
    ("DEMO0002", "shipping", [("toy-012", 2)]),
    ("DEMO0003", "delivered", [("toy-025", 1), ("toy-040", 1)]),
]
DEMO_ADDRESS = {
    "name": "Eval User",
    "street": "1 Test Lane",
    "city": "Springfield",
    "state": "IL",
    "zip": "62701",
    "country": "US",
}


def load_products() -> list[dict]:
    return json.loads(resources.files("wonder_seed").joinpath("products.json").read_text())


def product_document(p: dict) -> str:
    return "\n\n".join(
        [
            p["name"],
            p["description"],
            p["marketingCopy"],
            f"Category: {p['category']}",
            f"Keywords: {', '.join(p['keywords'])}",
            f"Manufacturer: {p['manufacturer']}",
            f"Ages {p['ageRange']['min']} to {p['ageRange']['max']}",
        ]
    )


def _retry(what: str, fn, attempts: int = 30, delay: float = 2.0):
    for attempt in range(1, attempts + 1):
        try:
            return fn()
        except Exception as exc:  # noqa: BLE001 - se reintenta cualquier fallo de conexión
            if attempt == attempts:
                raise
            print(f"{what} not ready ({type(exc).__name__}); retry {attempt}/{attempts}")
            time.sleep(delay)


def seed_postgres(products: list[dict], keep_orders: bool = False) -> None:
    import psycopg
    from psycopg.types.json import Jsonb

    schema = resources.files("wonder_seed").joinpath("schema.sql").read_text()
    conn = _retry("Postgres", lambda: psycopg.connect(DATABASE_URL, connect_timeout=5))
    with conn, conn.cursor() as cur:
        cur.execute(schema)
        if not keep_orders:
            cur.execute("TRUNCATE order_items, orders")
        for p in products:
            cur.execute(
                """
                INSERT INTO products (id, name, category, price, age_min, age_max, inventory, data)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (id) DO UPDATE SET
                    name = EXCLUDED.name, category = EXCLUDED.category, price = EXCLUDED.price,
                    age_min = EXCLUDED.age_min, age_max = EXCLUDED.age_max,
                    inventory = EXCLUDED.inventory, data = EXCLUDED.data
                """,
                (
                    p["id"],
                    p["name"],
                    p["category"],
                    p["price"],
                    p["ageRange"]["min"],
                    p["ageRange"]["max"],
                    p["inventory"],
                    Jsonb({k: v for k, v in p.items() if k != "inventory"}),
                ),
            )
        if not keep_orders:
            by_id = {p["id"]: p for p in products}
            for order_id, status, lines in DEMO_ORDERS:
                total = round(sum(by_id[pid]["price"] * qty for pid, qty in lines), 2)
                cur.execute(
                    "INSERT INTO orders (id, user_id, status, total, shipping_address) "
                    "VALUES (%s, %s, %s, %s, %s)",
                    (order_id, DEMO_USER, status, total, Jsonb(DEMO_ADDRESS)),
                )
                for pid, qty in lines:
                    cur.execute(
                        "INSERT INTO order_items VALUES (%s, %s, %s, %s, %s)",
                        (order_id, pid, by_id[pid]["name"], qty, by_id[pid]["price"]),
                    )
    conn.close()
    print(f"Postgres: {len(products)} products, {len(DEMO_ORDERS)} demo orders")


def seed_chroma(products: list[dict]) -> None:
    import chromadb

    parsed = urlparse(CHROMA_URL)

    def connect():
        client = chromadb.HttpClient(
            host=parsed.hostname or "localhost",
            port=parsed.port or 8000,
            ssl=(parsed.scheme == "https"),
        )
        client.heartbeat()
        return client

    client = _retry("ChromaDB", connect)
    try:
        client.delete_collection(name=COLLECTION)
    except Exception:  # noqa: BLE001 - la colección aún no existe
        pass
    collection = client.create_collection(name=COLLECTION)
    for start in range(0, len(products), BATCH_SIZE):
        batch = products[start : start + BATCH_SIZE]
        collection.add(
            ids=[p["id"] for p in batch],
            documents=[product_document(p) for p in batch],
            metadatas=[
                {
                    "category": p["category"],
                    "ageMin": p["ageRange"]["min"],
                    "ageMax": p["ageRange"]["max"],
                    "price": p["price"],
                    "inStock": p["inventory"] > 0,
                    "manufacturer": p["manufacturer"],
                }
                for p in batch
            ],
        )
    print(f"ChromaDB: {collection.count()} products indexed in '{COLLECTION}'")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--only", choices=["postgres", "chroma"])
    parser.add_argument("--keep-orders", action="store_true")
    args = parser.parse_args()
    products = load_products()
    if args.only in (None, "postgres"):
        seed_postgres(products, keep_orders=args.keep_orders)
    if args.only in (None, "chroma"):
        seed_chroma(products)


if __name__ == "__main__":
    main()
