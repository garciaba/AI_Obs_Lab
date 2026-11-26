# 9. Throttling de Bedrock

```bash
task scenario:bedrock-throttling
```

**Qué enseña.** Fallo de una dependencia externa: se ve en los dos productos, con papeles distintos. Si tu cuota es alta puede no producirse; sube `concurrency` en `scenarios/src/wonder_scenarios/definitions/bedrock-throttling.yaml`.

## Cómo se provoca

60 peticiones de referencia con 30 en paralelo. Según la cuota de tu cuenta, Bedrock responde `ThrottlingException` (HTTP 429) y botocore reintenta con backoff (`BEDROCK_MAX_ATTEMPTS`, 4 por defecto).

Definición: [`scenarios/src/wonder_scenarios/definitions/bedrock-throttling.yaml`](../../scenarios/src/wonder_scenarios/definitions/bedrock-throttling.yaml).
El runner imprime el `trace_id` de cada petición: úsalo para abrir la misma traza en los dos productos.

## Qué mirar en Arize AX

1. Spans **LLM** con error `ThrottlingException`, o mucho más largos de lo normal por los reintentos.
2. Peticiones que terminan en error (el agente devuelve 502) frente a las que se recuperan tras reintentar.
3. Las evals solo existen para las que respondieron: mira el hueco.

## Qué mirar en Dynatrace

1. Dashboard → *Bedrock: respuestas HTTP (throttling = 429)*: barras de 429 de `bedrock-runtime.eu-west-1.amazonaws.com`.
2. **Distributed Tracing**: varios spans HTTP cliente a Bedrock dentro de una misma llamada al modelo (los reintentos).
3. Alertas de latencia y de errores del agente; la causa es la dependencia externa, no el servicio.

Consulta de partida (Notebooks o Dashboards):

```
fetch spans
| filter service.name == "wonder-toys-agent" and span.kind == "client" and contains(server.address, "bedrock")
| makeTimeseries calls = count(), by:{http.response.status_code}, interval:1m
```

## Reversión

No hay nada que revertir: la carga termina sola.
