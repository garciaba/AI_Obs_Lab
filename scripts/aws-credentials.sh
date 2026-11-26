#!/usr/bin/env bash
# Escribe .lab/aws/credentials con la clave del usuario IAM mínimo creado por infra/aws.
# Es el fichero que montan el agente y las herramientas (perfil "agentic-lab").
set -euo pipefail
out=.lab/aws/credentials
mkdir -p "$(dirname "$out")"
umask 077
{
  echo "[${LAB_AWS_PROFILE:-agentic-lab}]"
  echo "aws_access_key_id = $(terraform -chdir=infra/aws output -raw access_key_id)"
  echo "aws_secret_access_key = $(terraform -chdir=infra/aws output -raw secret_access_key)"
  echo "region = $(terraform -chdir=infra/aws output -raw aws_region)"
} > "$out"
# El contenedor corre con otro uid: el fichero debe poder leerse dentro del montaje.
chmod 644 "$out"
echo "✓ Credenciales del usuario del lab en $out ($(terraform -chdir=infra/aws output -raw lab_user_arn))"
