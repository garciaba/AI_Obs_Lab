-- Esquema de la tienda Wonder Toys (pedidos e inventario).
-- Las tablas del checkpointer de LangGraph las crea el agente al arrancar.
CREATE TABLE IF NOT EXISTS products (
    id         TEXT PRIMARY KEY,
    name       TEXT NOT NULL,
    category   TEXT NOT NULL,
    price      NUMERIC(10, 2) NOT NULL,
    age_min    INTEGER NOT NULL,
    age_max    INTEGER NOT NULL,
    inventory  INTEGER NOT NULL CHECK (inventory >= 0),
    data       JSONB NOT NULL
);

CREATE TABLE IF NOT EXISTS orders (
    id               TEXT PRIMARY KEY,
    user_id          TEXT NOT NULL,
    status           TEXT NOT NULL CHECK (status IN ('processing', 'shipping', 'delivered', 'cancelled')),
    total            NUMERIC(10, 2) NOT NULL,
    shipping_address JSONB NOT NULL,
    created_at       TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS orders_user_idx ON orders (user_id, created_at DESC);

CREATE TABLE IF NOT EXISTS order_items (
    order_id     TEXT NOT NULL REFERENCES orders (id) ON DELETE CASCADE,
    product_id   TEXT NOT NULL REFERENCES products (id),
    product_name TEXT NOT NULL,
    quantity     INTEGER NOT NULL CHECK (quantity > 0),
    price        NUMERIC(10, 2) NOT NULL,
    PRIMARY KEY (order_id, product_id)
);
