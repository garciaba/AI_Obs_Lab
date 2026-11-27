# 9. Throttling de Vertex AI

```bash
task scenario:vertex-throttling
```

**Qué enseña.** Fallo de una dependencia externa: se ve en los dos productos, con papeles distintos. Si tu cuota es alta puede no producirse; sube `concurrency` en `scenarios/src/wonder_scenarios/definitions/vertex-throttling.yaml`.

## Cómo se provoca

60 peticiones de referencia con 30 en paralelo. Según la cuota de tu proyecto, Vertex AI responde HTTP 429 (`RESOURCE_EXHAUSTED`) y el cliente reintenta con backoff (`LLM_MAX_RETRIES`, 3 por defecto).

Definición: [`scenarios/src/wonder_scenarios/definitions/vertex-throttling.yaml`](../../scenarios/src/wonder_scenarios/definitions/vertex-throttling.yaml).
El runner imprime el `trace_id` de cada petición: úsalo para abrir la misma traza en los dos productos.

## Qué mirar en Arize AX

1. Spans **LLM** con error 429 (`RateLimitError`), o mucho más largos de lo normal por los reintentos.
2. Peticiones que terminan en error (el agente devuelve 502) frente a las que se recuperan tras reintentar.
3. Las evals solo existen para las que respondieron: mira el hueco.

## Qué mirar en Dynatrace

1. Dashboard → *Vertex AI: respuestas HTTP (cuota agotada = 429)*: barras de 429 de `europe-west1-aiplatform.googleapis.com`.
2. **Distributed Tracing**: varios spans `anthropic.messages.create` (uno por intento HTTP) dentro de una misma llamada al modelo; los logs del agente registran cada reintento con su `trace_id`.
3. Alertas de latencia y de errores del agente; la causa es la dependencia externa, no el servicio.

Consulta de partida (Notebooks o Dashboards):

```
fetch spans
| filter service.name == "wonder-toys-agent" and span.kind == "client" and contains(server.address, "aiplatform.googleapis.com")
| makeTimeseries calls = count(), by:{http.response.status_code}, interval:1m
```

## Reversión

No hay nada que revertir: la carga termina sola.
