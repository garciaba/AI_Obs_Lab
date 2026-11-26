#!/usr/bin/env bash
# Ejecuta Terraform en un módulo de infra/ pasando los valores de .env como variables.
#   scripts/tf.sh <aws|bindplane|dynatrace> <apply|destroy|plan|output ...>
# Los secretos viajan como TF_VAR_* (nunca en ficheros .tfvars ni en la línea de comandos).
set -euo pipefail
module="${1:?módulo: aws | bindplane | dynatrace}"; shift

set -a
[ -f .env ] && . ./.env
[ -f .lab/bindplane.env ] && . ./.lab/bindplane.env
set +a

export TF_IN_AUTOMATION=1
case "$module" in
  aws)
    export TF_VAR_aws_region="${AWS_REGION:-eu-west-1}"
    export TF_VAR_supervisor_inference_profile="${SUPERVISOR_MODEL_ID:-eu.anthropic.claude-sonnet-5-5}"
    export TF_VAR_subagent_inference_profile="${SUBAGENT_MODEL_ID:-eu.anthropic.claude-haiku-5-5}"
    [ -n "${LAB_BUDGET_USD:-}" ] && export TF_VAR_budget_limit_usd="$LAB_BUDGET_USD"
    [ -n "${LAB_BUDGET_EMAIL:-}" ] && export TF_VAR_budget_alert_emails="[\"$LAB_BUDGET_EMAIL\"]"
    # AWS_PROFILE (tu perfil de administración) lo usa el proveedor directamente.
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
