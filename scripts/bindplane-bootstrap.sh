#!/usr/bin/env bash
# Deja listo un Bindplane server recién arrancado para que Terraform pueda configurarlo:
#   1. espera a que responda /health
#   2. crea la organización/proyecto inicial si no existe (sin ella la API devuelve 403)
#   3. espera a que el servidor termine de cargar los tipos de fuente/procesador/destino
#   4. lee la secret key del proyecto y la guarda en .lab/bindplane.env (BINDPLANE_SECRET_KEY),
#      que es con la que el colector se registra por OpAMP
# Idempotente. Variables: BINDPLANE_ADMIN_USER, BINDPLANE_ADMIN_PASSWORD (de .env).
set -euo pipefail

URL="${BINDPLANE_URL:-http://localhost:3001}"
ENV_FILE="${LAB_BINDPLANE_ENV:-.lab/bindplane.env}"
COMPOSE=(docker compose --env-file .env --env-file "$ENV_FILE")
AUTH="${BINDPLANE_ADMIN_USER:?}:${BINDPLANE_ADMIN_PASSWORD:?}"
ORG="${BINDPLANE_ORGANIZATION:-agentic-lab}"

bp() {
  "${COMPOSE[@]}" exec -T bindplane /bindplane --remote-url http://localhost:3001 \
    --username "$BINDPLANE_ADMIN_USER" --password "$BINDPLANE_ADMIN_PASSWORD" "$@"
}

wait_for() { # descripción, segundos, comando...
  local what="$1" tries=$(( $2 / 3 )); shift 2
  for _ in $(seq 1 "$tries"); do
    if "$@" >/dev/null 2>&1; then return 0; fi
    sleep 3
  done
  echo "ERROR: tiempo agotado esperando: $what" >&2
  "${COMPOSE[@]}" logs --tail 30 bindplane >&2 || true
  exit 1
}

echo "→ Esperando a Bindplane en $URL"
wait_for "Bindplane /health" 180 curl -fsS "$URL/health"

if ! curl -fsS -u "$AUTH" "$URL/v1/source-types" >/dev/null 2>&1; then
  echo "→ Creando la organización inicial '$ORG'"
  curl -fsS -u "$AUTH" -X POST "$URL/v1/organizations" \
    -H 'Content-Type: application/json' \
    -d "{\"organizationName\": \"$ORG\", \"accountName\": \"$ORG\", \"eulaAccepted\": true}" >/dev/null \
    || echo "  (la organización ya existía o el servidor la rechazó; se continúa)"
fi

echo "→ Esperando a que el servidor cargue sus tipos de recurso"
has_types() { curl -fsS -u "$AUTH" "$URL/v1/source-types" | grep -q '"otlp"'; }
wait_for "tipos de recurso (licencia válida y organización creada)" 180 has_types

echo "→ Leyendo la secret key del proyecto"
# El formato de salida varía entre versiones: se toma el primer ULID o UUID que aparezca.
KEY="$( { bp secret get 2>/dev/null || bp get projects 2>/dev/null || true; } \
  | grep -Eio '[0-9A-HJKMNP-TV-Z]{26}|[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}' \
  | head -n 1)"
if [ -z "$KEY" ]; then
  cat >&2 <<MSG
ERROR: no se pudo leer la secret key con el CLI de Bindplane.
Cópiala de la consola ($URL → Agents → Install Agent) y ponla a mano:
  BINDPLANE_SECRET_KEY=<clave>   en $ENV_FILE
Después vuelve a lanzar: task up
MSG
  exit 1
fi

tmp="$(mktemp)"
grep -v '^BINDPLANE_SECRET_KEY=' "$ENV_FILE" > "$tmp" || true
echo "BINDPLANE_SECRET_KEY=$KEY" >> "$tmp"
mv "$tmp" "$ENV_FILE"
chmod 600 "$ENV_FILE"
echo "✓ Bindplane listo; secret key guardada en $ENV_FILE"
