"""Puertas de las fases 0 y 1.

gate 0   una llamada a Vertex AI responde con la cuenta de servicio del lab
gate 1   un script mínimo con OpenInference envía una traza, un log y una métrica al
         colector; la misma traza debe verse en Arize AX (completa) y en Dynatrace
         (redactada)
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
import time


def gate0() -> int:
    import google.auth
    from langchain_google_vertexai.model_garden import ChatAnthropicVertex

    credentials, default_project = google.auth.default(
        scopes=["https://www.googleapis.com/auth/cloud-platform"]
    )
    identity = getattr(credentials, "service_account_email", None) or type(credentials).__name__
    project = os.environ.get("GCP_PROJECT_ID") or default_project
    region = os.environ.get("GCP_REGION", "europe-west1")
    print(f"Identidad de Google Cloud: {identity} (proyecto {project}, región {region})")
    expected = os.environ.get("LAB_SERVICE_ACCOUNT", "agentic-lab-vertex")
    is_lab_account = str(identity).startswith(f"{expected}@")
    if not is_lab_account:
        print(
            f"  ⚠ no es la cuenta de servicio del lab ({expected}); "
            "revisa .lab/gcp/credentials.json"
        )
    failures = 0
    for role, var, default in (
        ("supervisor", "SUPERVISOR_MODEL_ID", "claude-sonnet-5-5"),
        ("subagentes/juez", "SUBAGENT_MODEL_ID", "claude-haiku-5-5"),
    ):
        model = os.environ.get(var, default)
        try:
            llm = ChatAnthropicVertex(
                model_name=model,
                project=project,
                location=region,
                max_output_tokens=20,
                max_retries=1,
                timeout=60.0,
            )
            response = llm.invoke("Reply with the single word: ready")
            usage = response.usage_metadata or {}
            print(
                f"  ok   {role}: {model} → {str(response.content).strip()!r} "
                f"({usage.get('input_tokens', '?')}+{usage.get('output_tokens', '?')} tokens)"
            )
        except Exception as exc:  # noqa: BLE001 - se informa del motivo exacto
            failures += 1
            print(f"  FAIL {role}: {model} → {type(exc).__name__}: {str(exc)[:400]}")
    if failures or not is_lab_account:
        print(
            "\nPUERTA 0 NO SUPERADA. Causas habituales: el modelo no está habilitado en "
            "Vertex AI Model Garden para el proyecto, no está disponible en esa región, o "
            "las credenciales no son las de la cuenta de servicio del lab."
        )
        return 1
    print("\nPUERTA 0 OK: Vertex AI responde con la cuenta de servicio del lab.")
    return 0


def gate1() -> int:
    """Prueba de concepto del pipeline: no usa ni el agente ni Vertex AI."""
    os.environ.setdefault("OTEL_SERVICE_NAME", "wonder-toys-pipeline-poc")
    from labcommon.telemetry import setup_telemetry
    from openinference.instrumentation import using_attributes
    from opentelemetry import metrics, trace
    from opentelemetry.trace import SpanKind

    telemetry = setup_telemetry("wonder-toys-pipeline-poc")
    tracer = trace.get_tracer("pipeline-poc")
    counter = metrics.get_meter("pipeline-poc").create_counter("wonder.poc.requests")
    logger = logging.getLogger("pipeline-poc")
    secret = f"SECRET-PROMPT-{int(time.time())}"

    # Misma forma que una petición real: raíz HTTP (sin tipo de IA) + spans de IA debajo.
    with tracer.start_as_current_span("POST /poc", kind=SpanKind.SERVER) as root:
        trace_id = format(root.get_span_context().trace_id, "032x")
        root.set_attributes(
            {
                "http.request.method": "POST",
                "http.route": "/poc",
                "input.value": secret,
                "session.id": f"poc-{trace_id[:8]}",
            }
        )
        with using_attributes(session_id=f"poc-{trace_id[:8]}"):
            with tracer.start_as_current_span("poc_agent") as agent:
                agent.set_attributes({"openinference.span.kind": "AGENT", "input.value": secret})
                with tracer.start_as_current_span("ChatAnthropicVertex") as llm:
                    llm.set_attributes(
                        {
                            "openinference.span.kind": "LLM",
                            "llm.model_name": "claude-haiku-5-5",
                            "gen_ai.operation.name": "chat",
                            "gen_ai.request.model": "claude-haiku-5-5",
                            "gen_ai.usage.input_tokens": 12,
                            "gen_ai.usage.output_tokens": 5,
                            "llm.input_messages.0.message.role": "user",
                            "llm.input_messages.0.message.content": secret,
                            "input.value": secret,
                            "output.value": f"answer to {secret}",
                        }
                    )
                # Como el span que emite el SDK de Anthropic por cada intento HTTP a Vertex.
                sdk = trace.get_tracer("com.anthropic.sdk.python.vertex")
                with sdk.start_as_current_span(
                    "anthropic.messages.create", kind=SpanKind.CLIENT
                ) as http:
                    http.set_attributes(
                        {
                            "server.address": "europe-west1-aiplatform.googleapis.com",
                            "http.response.status_code": 200,
                            "gen_ai.operation.name": "chat",
                            "gen_ai.request.model": "claude-haiku-5-5",
                            "gen_ai.usage.input_tokens": 12,
                            "gen_ai.usage.output_tokens": 5,
                        }
                    )
                with tracer.start_as_current_span("SELECT wonder", kind=SpanKind.CLIENT) as db:
                    db.set_attributes({"db.system.name": "postgresql", "db.query.text": "SELECT 1"})
                logger.info("pipeline proof of concept: log line correlated with the trace")
                counter.add(1, {"gate": "1"})
        root.set_attribute("output.value", f"answer to {secret}")
    telemetry.force_flush()
    telemetry.shutdown()

    project = os.environ.get("ARIZE_PROJECT_NAME", "agentic-lab")
    print(f"""Traza de prueba enviada al colector ({os.environ.get("OTEL_EXPORTER_OTLP_ENDPOINT")}).

  trace_id: {trace_id}
  marcador: {secret}

Comprueba (la raíz tarda ~30 s más en llegar a Arize: espera del tail sampling):

  Arize AX   proyecto "{project}" → Traces → {trace_id}
             · 3 spans: POST /poc (CHAIN, raíz), poc_agent (AGENT), ChatAnthropicVertex (LLM)
             · los spans SELECT y anthropic.messages.create NO aparecen
             · el marcador SÍ se lee en input/output (contenido completo)

  Dynatrace  Notebooks → DQL:
               fetch spans | filter trace.id == toUid("{trace_id}")
             · 5 spans, incluidos SELECT wonder y anthropic.messages.create (sin gen_ai.usage.*)
             · el marcador NO aparece en ningún atributo (redactado)
               fetch logs | filter trace_id == "{trace_id}"
               timeseries sum(wonder.poc.requests)

PUERTA 1: superada cuando las dos comprobaciones coinciden con lo descrito.""")
    return 0


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="gate", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("gate", choices=["0", "1"])
    args = parser.parse_args()
    sys.exit(gate0() if args.gate == "0" else gate1())


if __name__ == "__main__":
    main()
