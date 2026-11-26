"""Acceso a datos de la tienda: productos, inventario y pedidos.

`PostgresStore` es la implementación real; `MemoryStore` se usa en los tests unitarios y
replica la semántica (control de stock, cancelaciones, búsqueda por texto).
"""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime
from typing import Any, Protocol

from psycopg.rows import dict_row
from psycopg.types.json import Jsonb
from psycopg_pool import AsyncConnectionPool


class StoreError(Exception):
    """Error de negocio que la herramienta devuelve al agente como resultado."""


class Store(Protocol):
    async def get_product(self, product_id: str) -> dict | None: ...
    async def get_products(self, product_ids: list[str]) -> list[dict]: ...
    async def filter_products(
        self,
        *,
        query: str | None,
        keywords: list[str] | None,
        min_age: int | None,
        max_age: int | None,
        category: str | None,
        limit: int,
    ) -> tuple[list[dict], int]: ...
    async def random_products(self, limit: int) -> list[dict]: ...
    async def create_order(self, user_id: str, items: list[dict], address: dict) -> dict: ...
    async def get_order(self, order_id: str) -> dict | None: ...
    async def orders_by_user(self, user_id: str) -> list[dict]: ...
    async def cancel_order(self, order_id: str, user_id: str) -> dict: ...
    async def ping(self) -> bool: ...


def new_order_id() -> str:
    return uuid.uuid4().hex[:8].upper()


def _matches(product: dict, *, query, keywords, min_age, max_age, category) -> bool:
    if query:
        q = query.lower()
        if q not in product["name"].lower() and q not in product["description"].lower():
            return False
    if keywords:
        kws = [k.lower() for k in keywords]
        if not any(kw in pk.lower() for kw in kws for pk in product["keywords"]):
            return False
    if min_age is not None and product["ageRange"]["max"] < min_age:
        return False
    if max_age is not None and product["ageRange"]["min"] > max_age:
        return False
    if category and category.lower() not in product["category"].lower():
        return False
    return True


class MemoryStore:
    """Almacén en memoria con la misma semántica que Postgres (tests)."""

    def __init__(self, products: list[dict]) -> None:
        self.products = {p["id"]: json.loads(json.dumps(p)) for p in products}
        self.orders: dict[str, dict] = {}
        self.available = True

    def _check(self) -> None:
        if not self.available:
            raise ConnectionError("database unavailable")

    async def ping(self) -> bool:
        return self.available

    async def get_product(self, product_id: str) -> dict | None:
        self._check()
        return self.products.get(product_id)

    async def get_products(self, product_ids: list[str]) -> list[dict]:
        self._check()
        return [self.products[i] for i in product_ids if i in self.products]

    async def filter_products(self, *, query, keywords, min_age, max_age, category, limit):
        self._check()
        found = [
            p
            for p in self.products.values()
            if _matches(
                p,
                query=query,
                keywords=keywords,
                min_age=min_age,
                max_age=max_age,
                category=category,
            )
        ]
        return found[:limit], len(found)

    async def random_products(self, limit: int) -> list[dict]:
        self._check()
        return list(self.products.values())[-limit:]

    async def create_order(self, user_id: str, items: list[dict], address: dict) -> dict:
        self._check()
        lines = []
        for item in items:
            product = self.products.get(item["product_id"])
            if not product:
                raise StoreError(f"Product {item['product_id']} not found")
            if product["inventory"] < item["quantity"]:
                raise StoreError(
                    f"Insufficient stock for {product['name']}. "
                    f"Only {product['inventory']} available."
                )
            lines.append(
                {
                    "productId": product["id"],
                    "productName": product["name"],
                    "quantity": item["quantity"],
                    "price": product["price"],
                }
            )
        for line in lines:
            self.products[line["productId"]]["inventory"] -= line["quantity"]
        order = {
            "id": new_order_id(),
            "userId": user_id,
            "items": lines,
            "total": round(sum(line["price"] * line["quantity"] for line in lines), 2),
            "shippingAddress": address,
            "status": "processing",
            "createdAt": datetime.now(UTC).isoformat(),
        }
        self.orders[order["id"]] = order
        return order

    async def get_order(self, order_id: str) -> dict | None:
        self._check()
        return self.orders.get(order_id.upper())

    async def orders_by_user(self, user_id: str) -> list[dict]:
        self._check()
        return [o for o in self.orders.values() if o["userId"] == user_id]

    async def cancel_order(self, order_id: str, user_id: str) -> dict:
        self._check()
        order = self.orders.get(order_id.upper())
        if not order or order["userId"] != user_id:
            return {"success": False, "error": "Order not found"}
        if order["status"] == "cancelled":
            return {"success": False, "error": "Order is already cancelled"}
        if order["status"] == "delivered":
            return {"success": False, "error": "Cannot cancel a delivered order"}
        order["status"] = "cancelled"
        for line in order["items"]:
            if line["productId"] in self.products:
                self.products[line["productId"]]["inventory"] += line["quantity"]
        return {"success": True}


def _product_row(row: dict[str, Any]) -> dict:
    product = dict(row["data"])
    product["inventory"] = row["inventory"]
    return product


class PostgresStore:
    def __init__(self, database_url: str, timeout_s: float = 3.0) -> None:
        self._pool = AsyncConnectionPool(
            database_url,
            min_size=1,
            max_size=8,
            open=False,
            timeout=timeout_s,
            kwargs={"row_factory": dict_row, "connect_timeout": max(1, int(timeout_s))},
            name="wonder-mcp",
        )

    async def open(self) -> None:
        # wait=False: el servidor arranca aunque Postgres no esté; los errores aparecen
        # (y se trazan) en cada llamada a herramienta, que es lo que el lab quiere mostrar.
        await self._pool.open(wait=False)

    async def close(self) -> None:
        await self._pool.close()

    async def ping(self) -> bool:
        try:
            async with self._pool.connection() as conn:
                await conn.execute("SELECT 1")
            return True
        except Exception:
            return False

    async def get_product(self, product_id: str) -> dict | None:
        async with self._pool.connection() as conn:
            cur = await conn.execute(
                "SELECT data, inventory FROM products WHERE id = %s", (product_id,)
            )
            row = await cur.fetchone()
        return _product_row(row) if row else None

    async def get_products(self, product_ids: list[str]) -> list[dict]:
        if not product_ids:
            return []
        async with self._pool.connection() as conn:
            cur = await conn.execute(
                "SELECT data, inventory FROM products WHERE id = ANY(%s)", (product_ids,)
            )
            rows = await cur.fetchall()
        by_id = {r["data"]["id"]: _product_row(r) for r in rows}
        return [by_id[i] for i in product_ids if i in by_id]

    async def filter_products(self, *, query, keywords, min_age, max_age, category, limit):
        clauses, params = ["TRUE"], []
        if query:
            clauses.append("(name ILIKE %s OR data->>'description' ILIKE %s)")
            params += [f"%{query}%", f"%{query}%"]
        if keywords:
            clauses.append(
                "EXISTS (SELECT 1 FROM jsonb_array_elements_text(data->'keywords') kw "
                "WHERE kw ILIKE ANY(%s))"
            )
            params.append([f"%{k}%" for k in keywords])
        if min_age is not None:
            clauses.append("age_max >= %s")
            params.append(min_age)
        if max_age is not None:
            clauses.append("age_min <= %s")
            params.append(max_age)
        if category:
            clauses.append("category ILIKE %s")
            params.append(f"%{category}%")
        where = " AND ".join(clauses)
        async with self._pool.connection() as conn:
            cur = await conn.execute(
                f"SELECT data, inventory, count(*) OVER () AS total FROM products "
                f"WHERE {where} ORDER BY id LIMIT %s",
                (*params, limit),
            )
            rows = await cur.fetchall()
        total = rows[0]["total"] if rows else 0
        return [_product_row(r) for r in rows], total

    async def random_products(self, limit: int) -> list[dict]:
        async with self._pool.connection() as conn:
            cur = await conn.execute(
                "SELECT data, inventory FROM products ORDER BY random() LIMIT %s", (limit,)
            )
            rows = await cur.fetchall()
        return [_product_row(r) for r in rows]

    async def create_order(self, user_id: str, items: list[dict], address: dict) -> dict:
        order_id = new_order_id()
        lines: list[dict] = []
        async with self._pool.connection() as conn, conn.transaction():
            for item in items:
                cur = await conn.execute(
                    "SELECT id, name, price, inventory FROM products WHERE id = %s FOR UPDATE",
                    (item["product_id"],),
                )
                product = await cur.fetchone()
                if not product:
                    raise StoreError(f"Product {item['product_id']} not found")
                if product["inventory"] < item["quantity"]:
                    raise StoreError(
                        f"Insufficient stock for {product['name']}. "
                        f"Only {product['inventory']} available."
                    )
                lines.append(
                    {
                        "productId": product["id"],
                        "productName": product["name"],
                        "quantity": item["quantity"],
                        "price": float(product["price"]),
                    }
                )
            total = round(sum(line["price"] * line["quantity"] for line in lines), 2)
            cur = await conn.execute(
                "INSERT INTO orders (id, user_id, status, total, shipping_address) "
                "VALUES (%s, %s, 'processing', %s, %s) RETURNING created_at",
                (order_id, user_id, total, Jsonb(address)),
            )
            created_at = (await cur.fetchone())["created_at"]
            for line in lines:
                await conn.execute(
                    "INSERT INTO order_items (order_id, product_id, product_name, quantity, price)"
                    " VALUES (%s, %s, %s, %s, %s)",
                    (
                        order_id,
                        line["productId"],
                        line["productName"],
                        line["quantity"],
                        line["price"],
                    ),
                )
                await conn.execute(
                    "UPDATE products SET inventory = inventory - %s WHERE id = %s",
                    (line["quantity"], line["productId"]),
                )
        return {
            "id": order_id,
            "userId": user_id,
            "items": lines,
            "total": total,
            "shippingAddress": address,
            "status": "processing",
            "createdAt": created_at.isoformat(),
        }

    _ORDER_SQL = """
        SELECT o.id, o.user_id, o.status, o.total, o.shipping_address, o.created_at,
               COALESCE(json_agg(json_build_object(
                   'productId', i.product_id, 'productName', i.product_name,
                   'quantity', i.quantity, 'price', i.price)
                   ORDER BY i.product_id) FILTER (WHERE i.order_id IS NOT NULL), '[]') AS items
        FROM orders o LEFT JOIN order_items i ON i.order_id = o.id
        WHERE {where}
        GROUP BY o.id ORDER BY o.created_at DESC
    """

    @staticmethod
    def _order_row(row: dict) -> dict:
        return {
            "id": row["id"],
            "userId": row["user_id"],
            "items": [{**i, "price": float(i["price"])} for i in row["items"]],
            "total": float(row["total"]),
            "shippingAddress": row["shipping_address"],
            "status": row["status"],
            "createdAt": row["created_at"].isoformat(),
        }

    async def get_order(self, order_id: str) -> dict | None:
        async with self._pool.connection() as conn:
            cur = await conn.execute(
                self._ORDER_SQL.format(where="o.id = %s"), (order_id.upper(),)
            )
            row = await cur.fetchone()
        return self._order_row(row) if row else None

    async def orders_by_user(self, user_id: str) -> list[dict]:
        async with self._pool.connection() as conn:
            cur = await conn.execute(self._ORDER_SQL.format(where="o.user_id = %s"), (user_id,))
            rows = await cur.fetchall()
        return [self._order_row(r) for r in rows]

    async def cancel_order(self, order_id: str, user_id: str) -> dict:
        async with self._pool.connection() as conn, conn.transaction():
            cur = await conn.execute(
                "SELECT status FROM orders WHERE id = %s AND user_id = %s FOR UPDATE",
                (order_id.upper(), user_id),
            )
            order = await cur.fetchone()
            if not order:
                return {"success": False, "error": "Order not found"}
            if order["status"] == "cancelled":
                return {"success": False, "error": "Order is already cancelled"}
            if order["status"] == "delivered":
                return {"success": False, "error": "Cannot cancel a delivered order"}
            await conn.execute(
                "UPDATE orders SET status = 'cancelled' WHERE id = %s", (order_id.upper(),)
            )
            await conn.execute(
                "UPDATE products p SET inventory = p.inventory + i.quantity "
                "FROM order_items i WHERE i.order_id = %s AND i.product_id = p.id",
                (order_id.upper(),),
            )
        return {"success": True}
