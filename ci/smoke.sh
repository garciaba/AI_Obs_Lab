#!/usr/bin/env bash
# Smoke test de extremo a extremo SIN credenciales (CI y `task test:smoke`).
#
# Levanta la aplicación real (agente, servidor MCP, ChromaDB, Postgres) con el LLM
# simulado y un colector otelcol-contrib con la configuración de referencia generada desde
# infra/bindplane/pipeline/ (los mismos fragmentos que Terraform carga en Bindplane), con
# exportadores a fichero. Después comprueba el reparto, la redacción y la traza unida.
set -euo pipefail
cd "$(dirname "$0")/.."

DC=(docker compose --env-file ci/smoke.env -f compose.yaml -f compose.smoke.yaml)
OUT=.lab/smoke/out
KEEP="${SMOKE_KEEP:-0}"

cleanup() {
  status=$?
  if [ $status -ne 0 ]; then "${DC[@]}" logs --tail 40 agent mcp-server collector || true; fi
  if [ "$KEEP" != "1" ]; then "${DC[@]}" --profile tools down --volumes --remove-orphans >/dev/null 2>&1 || true; fi
  exit $status
}
trap cleanup EXIT

mkdir -p "$OUT" evals/results
rm -f "$OUT"/*.jsonl
chmod 777 "$OUT" evals/results
python3 infra/bindplane/render_reference.py --decision-wait 5s > .lab/smoke/collector.yaml

echo "→ Construyendo imágenes"
if [ "${SMOKE_SKIP_BUILD:-0}" != "1" ]; then "${DC[@]}" --profile tools build -q; fi
echo "→ Arrancando la pila (LLM simulado, colector de referencia)"
"${DC[@]}" up -d --no-build --wait collector postgres chroma mcp-server agent

echo "→ Puerta 2 con el LLM simulado: 25 peticiones de referencia"
"${DC[@]}" --profile tools run --rm --no-deps -T tools scenario gate2

echo "→ Un escenario completo (herramienta caída) y su reversión"
"${DC[@]}" --profile tools run --rm --no-deps -T tools scenario run tool-error

echo "→ Verificando el pipeline con una traza nueva"
trace_id="$(curl -fsS --noproxy '*' localhost:8080/chat -H 'content-type: application/json' \
  -d '{"message":"Show me some dinosaur toys","user_id":"eval-user-001"}' \
  | python3 -c 'import json,sys; print(json.load(sys.stdin)["trace_id"])')"
# Espera del tail sampling (5 s en el smoke) + batch + exportación periódica de métricas.
sleep 40
python3 ci/verify_pipeline.py "$OUT" "$trace_id"
echo "SMOKE OK"
