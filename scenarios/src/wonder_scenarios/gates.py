"""Puertas de las fases 0 y 1.

gate 0   una llamada a Bedrock responde con el usuario IAM del lab
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
    import boto3

    region = os.environ.get("AWS_REGION", "eu-west-1")
    identity = boto3.client("sts", region_name=region).get_caller_identity()
    arn = identity["Arn"]
    print(f"Identidad AWS: {arn}")
    expected = os.environ.get("LAB_IAM_USER", "agentic-lab-bedrock")
    is_lab_user = arn.endswith(f"user/lab/{expected}") or arn.endswith(f"user/{expected}")
    if not is_lab_user:
        print(f"  ⚠ no es el usuario del lab ({expected}); revisa .lab/aws/credentials")
    runtime = boto3.client("bedrock-runtime", region_name=region)
    failures = 0
    for role, var, default in (
        ("supervisor", "SUPERVISOR_MODEL_ID", "eu.anthropic.claude-sonnet-5-5"),
        ("subagentes/juez", "SUBAGENT_MODEL_ID", "eu.anthropic.claude-haiku-5-5"),
    ):
        model = os.environ.get(var, default)
        try:
            response = runtime.converse(
                modelId=model,
                messages=[
                    {"role": "user", "content": [{"text": "Reply with the single word: ready"}]}
                ],
                inferenceConfig={"maxTokens": 20},
            )
            text = response["output"]["message"]["content"][0]["text"].strip()
            usage = response["usage"]
            print(
                f"  ok   {role}: {model} → {text!r} "
                f"({usage['inputTokens']}+{usage['outputTokens']} tokens)"
            )
        except Exception as exc:  # noqa: BLE001 - se informa del motivo exacto
            failures += 1
            print(f"  FAIL {role}: {model} → {type(exc).__name__}: {exc}")
    if failures or not is_lab_user:
        print(
            "\nPUERTA 0 NO SUPERADA. Causas habituales: acceso al modelo sin activar en la "
            "consola de Bedrock, o credenciales que no son las del usuario del lab."
        )
        return 1
    print("\nPUERTA 0 OK: Bedrock responde con el usuario del lab.")
    return 0


def gate1() -> int:
    """Prueba de concepto del pipeline: no usa ni el agente ni Bedrock."""
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
                with tracer.start_as_current_span("ChatBedrockConverse") as llm:
                    llm.set_attributes(
                        {
                            "openinference.span.kind": "LLM",
                            "llm.model_name": "eu.anthropic.claude-haiku-5-5",
                            "gen_ai.operation.name": "chat",
                            "gen_ai.request.model": "eu.anthropic.claude-haiku-5-5",
                            "gen_ai.usage.input_tokens": 12,
                            "gen_ai.usage.output_tokens": 5,
                            "llm.input_messages.0.message.role": "user",
                            "llm.input_messages.0.message.content": secret,
                            "input.value": secret,
                            "output.value": f"answer to {secret}",
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
             · 3 spans: POST /poc (CHAIN, raíz), poc_agent (AGENT), ChatBedrockConverse (LLM)
             · el span SELECT NO aparece (no es de IA)
             · el marcador SÍ se lee en input/output (contenido completo)

  Dynatrace  Notebooks → DQL:
               fetch spans | filter trace.id == toUid("{trace_id}")
             · 4 spans, incluido SELECT wonder
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
