# 8. Cambio de modelo

```bash
task scenario:model-swap
```

**Qué enseña.** Un cambio de modelo es un cambio de versión: se decide comparando calidad (Arize AX) frente a coste y latencia (Dynatrace).

## Cómo se provoca

Dos tramos sobre las mismas peticiones de referencia: supervisor en **Sonnet 5.5** y después en **Haiku 5.5** (ajuste en caliente `supervisor_model`). Con credenciales de Arize, cada tramo es un experimento sobre el dataset `wonder-toys-regression`; sin ellas, envía las 25 peticiones en cada tramo.

Definición: [`scenarios/src/wonder_scenarios/definitions/model-swap.yaml`](../../scenarios/src/wonder_scenarios/definitions/model-swap.yaml).
El runner imprime el `trace_id` de cada petición: úsalo para abrir la misma traza en los dos productos.

## Qué mirar en Arize AX

1. **Datasets → wonder-toys-regression → Experiments**: selecciona `model-swap-supervisor-sonnet-*` y `model-swap-supervisor-haiku-*` y compáralos.
2. Diferencias por evaluador: correctness, tool_selection, format_compliance, prompt_injection_safety…
3. Abre las filas donde difieren y lee las dos respuestas lado a lado.

## Qué mirar en Dynatrace

1. Dashboard → *Tokens por modelo* y *Llamadas al LLM: latencia por modelo*: dos tramos claros.
2. *Agente: latencia por petición*: cambio de p50/p90 entre tramos.
3. El atributo `wonder.supervisor_model` de la raíz permite partir cualquier consulta por modelo.

Consulta de partida (Notebooks o Dashboards):

```
fetch spans
| filter service.name == "wonder-toys-agent" and endpoint.name == "POST /chat"
| summarize requests = count(), avg_ms = avg(toLong(duration) / 1000000.0), by:{wonder.supervisor_model}
```

## Reversión

El runner borra el ajuste al terminar cada tramo; además caduca solo.
