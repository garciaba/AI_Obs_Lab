# 1. Herramienta lenta

```bash
task scenario:slow-tool
```

**Qué enseña.** Un fallo de rendimiento de infraestructura: los dos productos lo ven, pero Dynatrace lo detecta solo (alerta) y Arize AX lo sitúa dentro del razonamiento del agente.

## Cómo se provoca

El servidor MCP añade 8 s de latencia a `check-order-status` (`PUT /chaos` con `latency_ms`). El runner envía 8 peticiones de seguimiento de pedidos.

Definición: [`scenarios/src/wonder_scenarios/definitions/slow-tool.yaml`](../../scenarios/src/wonder_scenarios/definitions/slow-tool.yaml).
El runner imprime el `trace_id` de cada petición: úsalo para abrir la misma traza en los dos productos.

## Qué mirar en Arize AX

1. Proyecto → **Traces**, ordena por latencia: las trazas del escenario rondan los 8 s (metadato `scenario = slow-tool`).
2. Abre una: el span **TOOL `check-order-status`** ocupa casi toda la duración; los spans LLM de alrededor son normales.
3. Debajo del TOOL cuelga el span del servidor `tools/call check-order-status` con el atributo `wonder.chaos.latency_ms = 8000`.
4. Las evals de calidad (Correctness, Tool Selection) siguen bien: el agente responde correctamente, solo que tarde.

## Qué mirar en Dynatrace

1. **Dashboard del lab** → tile *Servidor MCP: latencia por herramienta*: `check-order-status` sube a ~8000 ms.
2. **Problems**: salta la alerta *Agentic lab: latencia alta del servidor MCP* (detector de Davis definido en `infra/dynatrace/alerts.tf`) tras ~2 minutos.
3. **Distributed Tracing**: en la traza, el tiempo está en el servicio `wonder-toys-mcp`, no en el agente ni en Postgres (la consulta SQL es rápida).
4. Logs: `fetch logs | filter service.name == "wonder-toys-mcp" and contains(content, "chaos: delaying")`.

Consulta de partida (Notebooks o Dashboards):

```
fetch spans
| filter service.name == "wonder-toys-mcp" and gen_ai.tool.name == "check-order-status"
| makeTimeseries avg_ms = avg(toLong(duration) / 1000000.0), interval:1m
```

## Reversión

El runner borra el caos al terminar; además caduca solo a los 15 minutos.
