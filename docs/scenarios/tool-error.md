# 2. Herramienta caída

```bash
task scenario:tool-error
```

**Qué enseña.** El error técnico lo ve Dynatrace; si el agente lo gestionó bien (¿lo reconoce o finge la compra?) solo lo dice Arize AX. La API responde 200 en ambos casos.

## Cómo se provoca

El servidor MCP devuelve error en `purchase-product` («Payment gateway unavailable»). El runner envía 6 peticiones de compra con todos los datos.

Definición: [`scenarios/src/wonder_scenarios/definitions/tool-error.yaml`](../../scenarios/src/wonder_scenarios/definitions/tool-error.yaml).
El runner imprime el `trace_id` de cada petición: úsalo para abrir la misma traza en los dos productos.

## Qué mirar en Arize AX

1. Traza del escenario: el span **TOOL `purchase-product`** aparece con estado de error y el mensaje de la pasarela.
2. Lee la **respuesta final** del supervisor: debe reconocer que la compra no se hizo y no inventar un número de pedido.
3. Eval **tool_response_handling**: `incorrect` si el agente afirma que el pedido se realizó. Compárala con **correctness**.
4. Eval **catalog_faithfulness**: `hallucinated` si aparece un `orderId` que ninguna herramienta devolvió.

## Qué mirar en Dynatrace

1. **Dashboard** → *Errores por servicio*: barras de `wonder-toys-mcp`.
2. **Problems**: alerta *Agentic lab: errores en el servidor MCP*.
3. Logs de error con `trace_id`: del log se salta a la traza con un clic.
4. La petición HTTP al agente es **200**: sin el span de la herramienta no se vería el fallo.

Consulta de partida (Notebooks o Dashboards):

```
fetch logs
| filter service.name == "wonder-toys-mcp" and loglevel == "ERROR"
| fields timestamp, content, trace_id
| sort timestamp desc
```

## Reversión

El runner borra el caos al terminar; además caduca solo.
