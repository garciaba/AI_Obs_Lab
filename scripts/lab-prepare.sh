#!/usr/bin/env bash
# Prepara el estado local del lab (.lab/, fuera de Git). Idempotente.
set -euo pipefail

if [ ! -f .env ]; then
  if [ -f secrets.enc.env ] && command -v sops >/dev/null 2>&1; then
    echo "→ Descifrando secrets.enc.env en .env"
    sops --decrypt --input-type dotenv --output-type dotenv secrets.enc.env > .env
    chmod 600 .env
  else
    echo "ERROR: falta .env. Copia .env.example a .env y rellena los valores" >&2
    echo "       (o 'task secrets:decrypt' si ya tienes secrets.enc.env)." >&2
    exit 1
  fi
fi

mkdir -p .lab/gcp evals/results
ENV_FILE=.lab/bindplane.env
if [ ! -f "$ENV_FILE" ]; then
  uuid() { cat /proc/sys/kernel/random/uuid 2>/dev/null || uuidgen | tr '[:upper:]' '[:lower:]'; }
  {
    echo "# Generado por task up. No versionar."
    echo "BINDPLANE_SESSION_SECRET=$(uuid)"
    # Marcador hasta que scripts/bindplane-bootstrap.sh lea la clave real del servidor.
    echo "BINDPLANE_SECRET_KEY=pending"
  } > "$ENV_FILE"
  chmod 600 "$ENV_FILE"
fi
[ -f .lab/gcp/credentials.json ] || echo "{}" > .lab/gcp/credentials.json
