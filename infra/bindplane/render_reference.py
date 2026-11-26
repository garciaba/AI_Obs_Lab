#!/usr/bin/env python3
"""Genera la configuración de colector de referencia a partir de `pipeline/*.yaml`.

Los mismos fragmentos YAML que Terraform carga en Bindplane (procesadores y fuentes
`custom`) se ensamblan aquí en una configuración completa de OpenTelemetry Collector con
la topología del cookbook de Arize (conectores `forward`). Sirve para:

  - validar la lógica del pipeline sin Bindplane ni credenciales (`task test:pipeline` y el
    smoke test de CI), con exportadores a fichero en lugar de Arize AX y Dynatrace;
  - tener un plan B ejecutable si Bindplane no pudiera desplegar el pipeline
    (`--exporters real` produce la configuración con los exportadores de verdad).

Uso: render_reference.py [--exporters file|real] [--no-docker] [--decision-wait 5s] > config.yaml
Sin dependencias: solo biblioteca estándar.
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

PIPELINE = Path(__file__).parent / "pipeline"


def fragment(name: str, indent: int = 2) -> str:
    text = (PIPELINE / f"{name}.yaml").read_text()
    lines = [ln for ln in text.splitlines() if not ln.lstrip().startswith("#")]
    return "\n".join((" " * indent + ln) if ln.strip() else ln for ln in lines).rstrip()


EXPORTERS_FILE = """\
  file/arize:
    path: /out/arize-traces.jsonl
  file/dynatrace-traces:
    path: /out/dynatrace-traces.jsonl
  file/dynatrace-logs:
    path: /out/dynatrace-logs.jsonl
  file/dynatrace-metrics:
    path: /out/dynatrace-metrics.jsonl"""

EXPORTERS_REAL = """\
  otlp_grpc/arize:
    endpoint: otlp.arize.com:443
    headers:
      space_id: ${env:ARIZE_SPACE_ID}
      api_key: ${env:ARIZE_API_KEY}
  otlp_http/dynatrace:
    endpoint: ${env:DT_ENV_URL}/api/v2/otlp
    headers:
      Authorization: "${env:DT_AUTH_SCHEME:-Api-Token} ${env:DT_INGEST_TOKEN}"
  # alias: un único exportador de Dynatrace para las tres señales"""


def render(exporters: str, docker: bool, decision_wait: str, project: str) -> str:
    real = exporters == "real"
    arize = "otlp_grpc/arize" if real else "file/arize"
    dt_traces = "otlp_http/dynatrace" if real else "file/dynatrace-traces"
    dt_logs = "otlp_http/dynatrace" if real else "file/dynatrace-logs"
    dt_metrics = "otlp_http/dynatrace" if real else "file/dynatrace-metrics"
    log_receivers = "[otlp, filelog/docker]" if docker else "[otlp]"
    metric_receivers = "[otlp, docker_stats]" if docker else "[otlp]"
    docker_receivers = (
        "\n" + fragment("source-docker-logs") + "\n" + fragment("source-docker-stats")
        if docker
        else ""
    )
    config = f"""\
# GENERADO por infra/bindplane/render_reference.py — no editar a mano.
extensions:
  health_check:
    endpoint: 0.0.0.0:13133

receivers:
  otlp:
    protocols:
      grpc:
        endpoint: 0.0.0.0:4317
      http:
        endpoint: 0.0.0.0:4318{docker_receivers}

processors:
  batch: {{}}
  cumulative_to_delta:
    max_staleness: 15m
{fragment("filter-ai-spans-only")}
{fragment("tail-sampling-traces-with-ai-spans")}
{fragment("filter-non-ai-trace-root")}
{fragment("transform-root-as-chain")}
{fragment("resource-arize-project")}
{fragment("transform-redact-ai-content")}

connectors:
  forward/traces: {{}}
  forward/arize: {{}}

exporters:
{EXPORTERS_REAL if real else EXPORTERS_FILE}

service:
  extensions: [health_check]
  pipelines:
    traces/in:
      receivers: [otlp]
      processors: [batch]
      exporters: [forward/traces]
    traces/dynatrace:
      receivers: [forward/traces]
      processors: [transform/redact-ai-content]
      exporters: [{dt_traces}]
    traces/arize:
      receivers: [forward/traces]
      processors: [filter/ai-spans-only]
      exporters: [forward/arize]
    traces/arize-root:
      receivers: [forward/traces]
      processors: [tail_sampling/traces-with-ai-spans, filter/non-ai-trace-root, transform/root-as-chain]
      exporters: [forward/arize]
    traces/arize-out:
      receivers: [forward/arize]
      processors: [resource/arize-project]
      exporters: [{arize}]
    metrics:
      receivers: {metric_receivers}
      processors: [cumulative_to_delta, batch]
      exporters: [{dt_metrics}]
    logs:
      receivers: {log_receivers}
      processors: [batch]
      exporters: [{dt_logs}]
"""
    config = re.sub(r"decision_wait: \S+", f"decision_wait: {decision_wait}", config)
    project_value = "${env:ARIZE_PROJECT_NAME:-agentic-lab}" if real else project
    return config.replace("${ARIZE_PROJECT_NAME}", project_value)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--exporters", choices=["file", "real"], default="file")
    parser.add_argument("--no-docker", action="store_true", help="sin logs/métricas de Docker")
    parser.add_argument("--decision-wait", default="30s")
    parser.add_argument("--project", default="agentic-lab")
    args = parser.parse_args()
    sys.stdout.write(render(args.exporters, not args.no_docker, args.decision_wait, args.project))


if __name__ == "__main__":
    main()
