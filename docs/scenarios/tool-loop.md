# 7. Bucle de herramientas

```bash
task scenario:tool-loop
```

**Qué enseña.** El agente no falla: gasta. Arize AX lo ve como una trayectoria que no converge; Dynatrace lo ve como coste y latencia.

## Cómo se provoca

El límite de recursión de los subagentes sube de 12 a 60 (ajuste en caliente del agente) y se envía una petición ambigua que invita a buscar sin fin («comprueba todas las categorías… hasta estar seguro»).

Definición: [`scenarios/src/wonder_scenarios/definitions/tool-loop.yaml`](../../scenarios/src/wonder_scenarios/definitions/tool-loop.yaml).
El runner imprime el `trace_id` de cada petición: úsalo para abrir la misma traza en los dos productos.

## Qué mirar en Arize AX

1. Traza larga con muchos spans **TOOL `search-products`** consecutivos y un LLM entre cada dos.
2. Eval **tool_call_count**: `excessive` (más de 5 llamadas). Eval **trajectory_convergence**: `wandering`.
3. Tokens acumulados de la traza frente a una búsqueda normal.

## Qué mirar en Dynatrace

1. **AI Observability** y el dashboard → *Tokens por modelo* y *Llamadas a herramientas por petición*: pico durante el escenario.
2. *Agente: latencia por petición*: p90 muy por encima de lo normal; puede saltar la alerta de latencia del agente.
3. Sin errores: la petición termina en 200.

Consulta de partida (Notebooks o Dashboards):

```
fetch spans
| filter service.name == "wonder-toys-agent" and isNotNull(wonder.tool_call_count)
| fields start_time, trace.id, wonder.tool_call_count, duration
| sort wonder.tool_call_count desc | limit 10
```

## Reversión

El runner borra el ajuste al terminar; además caduca solo a los 15 minutos. El límite por defecto (12) es precisamente la protección.
