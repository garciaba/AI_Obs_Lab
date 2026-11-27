#!/usr/bin/env bash
# Ejecuta Terraform en un módulo de infra/ pasando los valores de .env como variables.
#   scripts/tf.sh <gcp|bindplane|dynatrace> <apply|destroy|plan|output ...>
# Los secretos viajan como TF_VAR_* (nunca en ficheros .tfvars ni en la línea de comandos).
set -euo pipefail
module="${1:?módulo: gcp | bindplane | dynatrace}"; shift

set -a
[ -f .env ] && . ./.env
[ -f .lab/bindplane.env ] && . ./.lab/bindplane.env
set +a

export TF_IN_AUTOMATION=1
case "$module" in
  gcp)
    export TF_VAR_project_id="${GCP_PROJECT_ID:?}"
    export TF_VAR_region="${GCP_REGION:-europe-west1}"
    [ -n "${GCP_BILLING_ACCOUNT:-}" ] && export TF_VAR_billing_account="$GCP_BILLING_ACCOUNT"
    [ -n "${LAB_BUDGET_AMOUNT:-}" ] && export TF_VAR_budget_limit_usd="$LAB_BUDGET_AMOUNT"
    [ -n "${LAB_BUDGET_CURRENCY:-}" ] && export TF_VAR_budget_currency="$LAB_BUDGET_CURRENCY"
    # El proveedor usa tus Application Default Credentials (gcloud auth application-default login).
    ;;
  bindplane)
    export TF_VAR_bindplane_username="${BINDPLANE_ADMIN_USER:?}"
    export TF_VAR_bindplane_password="${BINDPLANE_ADMIN_PASSWORD:?}"
    export TF_VAR_arize_space_id="${ARIZE_SPACE_ID:?}"
    export TF_VAR_arize_api_key="${ARIZE_API_KEY:?}"
    export TF_VAR_arize_project_name="${ARIZE_PROJECT_NAME:-agentic-lab}"
    [ -n "${ARIZE_OTLP_HOST:-}" ] && export TF_VAR_arize_otlp_host="$ARIZE_OTLP_HOST"
    export TF_VAR_dt_env_url="${DT_ENV_URL:?}"
    export TF_VAR_dt_ingest_token="${DT_INGEST_TOKEN:?}"
    export TF_VAR_dt_auth_scheme="${DT_AUTH_SCHEME:-Api-Token}"
    ;;
  dynatrace)
    export TF_VAR_dt_env_url="${DT_ENV_URL:?}"
    export TF_VAR_dt_config_token="${DT_CONFIG_TOKEN:?}"
    [ -n "${DT_ALERT_ACTOR:-}" ] && export TF_VAR_alert_actor="$DT_ALERT_ACTOR"
    ;;
  *) echo "módulo desconocido: $module" >&2; exit 2 ;;
esac

dir="infra/$module"
case "${1:-}" in
  apply|destroy)
    terraform -chdir="$dir" init -input=false >/dev/null
    exec terraform -chdir="$dir" "$1" -auto-approve -input=false "${@:2}"
    ;;
  *) exec terraform -chdir="$dir" "$@" ;;
esac
