#!/usr/bin/env bash
# Escribe .lab/gcp/credentials.json con la clave de la cuenta de servicio mínima creada
# por infra/gcp. Es el fichero que montan el agente y las herramientas
# (GOOGLE_APPLICATION_CREDENTIALS).
set -euo pipefail
out=.lab/gcp/credentials.json
mkdir -p "$(dirname "$out")"
umask 077
terraform -chdir=infra/gcp output -raw credentials_json > "$out"
# El contenedor corre con otro uid: el fichero debe poder leerse dentro del montaje.
chmod 644 "$out"
echo "✓ Clave de la cuenta de servicio del lab en $out ($(terraform -chdir=infra/gcp output -raw service_account_email))"
