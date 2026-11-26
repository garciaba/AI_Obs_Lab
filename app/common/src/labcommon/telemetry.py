"""Telemetría común del lab: trazas, logs y métricas por OTLP/HTTP hacia el colector.

Un solo `TracerProvider`, un `LoggerProvider` y un `MeterProvider` por proceso, todos
apuntando al colector (`OTEL_EXPORTER_OTLP_ENDPOINT`). La aplicación no conoce ni Arize AX
ni Dynatrace: el reparto, el filtrado y la redacción ocurren en el colector.

Debe llamarse a `setup_telemetry()` antes de importar LangChain / FastMCP para que los
instrumentadores puedan parchear las librerías.
"""

from __future__ import annotations

import json
import logging
import os
import sys
from dataclasses import dataclass

from opentelemetry import metrics, trace
from opentelemetry._logs import set_logger_provider
from opentelemetry.sdk._logs import LoggerProvider, LoggingHandler
from opentelemetry.sdk._logs.export import BatchLogRecordProcessor
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import PeriodicExportingMetricReader
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor, SpanExporter


@dataclass
class Telemetry:
    tracer_provider: TracerProvider
    meter_provider: MeterProvider | None
    logger_provider: LoggerProvider | None

    def shutdown(self) -> None:
        self.tracer_provider.shutdown()
        if self.meter_provider:
            self.meter_provider.shutdown()
        if self.logger_provider:
            self.logger_provider.shutdown()

    def force_flush(self) -> None:
        self.tracer_provider.force_flush()
        if self.meter_provider:
            self.meter_provider.force_flush()
        if self.logger_provider:
            self.logger_provider.force_flush()


class _JsonStdoutFormatter(logging.Formatter):
    """Una línea JSON por registro, con trace_id/span_id del span activo.

    Es lo que el colector lee de los ficheros de log de Docker; el trace_id permite
    saltar del log del contenedor a la traza en Dynatrace.
    """

    def format(self, record: logging.LogRecord) -> str:
        ctx = trace.get_current_span().get_span_context()
        payload = {
            "ts": self.formatTime(record, "%Y-%m-%dT%H:%M:%S%z"),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        if ctx.is_valid:
            payload["trace_id"] = format(ctx.trace_id, "032x")
            payload["span_id"] = format(ctx.span_id, "016x")
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        return json.dumps(payload, ensure_ascii=False)


def _telemetry_enabled() -> bool:
    return os.environ.get("TELEMETRY_ENABLED", "true").lower() not in {"0", "false", "no"}


def setup_logging_stdout(level: str | None = None) -> None:
    root = logging.getLogger()
    root.setLevel((level or os.environ.get("LOG_LEVEL", "INFO")).upper())
    if not any(getattr(h, "_lab_stdout", False) for h in root.handlers):
        handler = logging.StreamHandler(sys.stdout)
        handler.setFormatter(_JsonStdoutFormatter())
        handler._lab_stdout = True  # type: ignore[attr-defined]
        root.addHandler(handler)
    # Ruido habitual de librerías
    for noisy in ("httpx", "httpcore", "botocore", "urllib3", "chromadb.telemetry"):
        logging.getLogger(noisy).setLevel(logging.WARNING)


def setup_telemetry(
    service_name: str,
    service_version: str = "0.1.0",
    *,
    span_exporter: SpanExporter | None = None,
) -> Telemetry:
    """Configura los tres proveedores globales de OpenTelemetry.

    `span_exporter` permite inyectar un exportador en memoria en los tests.
    Con `TELEMETRY_ENABLED=false` solo se configuran las trazas en local (sin exportar),
    de modo que la app sigue funcionando sin colector.
    """
    resource = Resource.create(
        {
            "service.name": os.environ.get("OTEL_SERVICE_NAME", service_name),
            "service.version": service_version,
            "service.namespace": "wonder-toys",
            "deployment.environment.name": os.environ.get("LAB_ENVIRONMENT", "agentic-lab"),
        }
    )
    setup_logging_stdout()

    tracer_provider = TracerProvider(resource=resource)
    meter_provider: MeterProvider | None = None
    logger_provider: LoggerProvider | None = None

    if span_exporter is not None:
        from opentelemetry.sdk.trace.export import SimpleSpanProcessor

        tracer_provider.add_span_processor(SimpleSpanProcessor(span_exporter))
    elif _telemetry_enabled():
        from opentelemetry.exporter.otlp.proto.http._log_exporter import OTLPLogExporter
        from opentelemetry.exporter.otlp.proto.http.metric_exporter import OTLPMetricExporter
        from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter

        tracer_provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter()))

        reader = PeriodicExportingMetricReader(
            OTLPMetricExporter(),
            export_interval_millis=int(os.environ.get("OTEL_METRIC_EXPORT_INTERVAL", "15000")),
        )
        meter_provider = MeterProvider(resource=resource, metric_readers=[reader])
        metrics.set_meter_provider(meter_provider)

        logger_provider = LoggerProvider(resource=resource)
        logger_provider.add_log_record_processor(BatchLogRecordProcessor(OTLPLogExporter()))
        set_logger_provider(logger_provider)
        # El SDK añade trace_id y span_id del contexto activo a cada registro; no se
        # añade ningún atributo trace_id a mano (Dynatrace lo guardaría como lista).
        logging.getLogger().addHandler(
            LoggingHandler(level=logging.INFO, logger_provider=logger_provider)
        )

    trace.set_tracer_provider(tracer_provider)
    return Telemetry(tracer_provider, meter_provider, logger_provider)


def current_trace_id() -> str | None:
    ctx = trace.get_current_span().get_span_context()
    return format(ctx.trace_id, "032x") if ctx.is_valid else None
