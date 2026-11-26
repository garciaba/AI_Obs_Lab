# 4. Argumentos erróneos

```bash
task scenario:bad-args
```

**Qué enseña.** La lección central del lab: **todo devuelve HTTP 200** y la infraestructura está sana. El fallo solo existe en el plano semántico, y solo Arize AX lo ve.

## Cómo se provoca

Tres peticiones que empujan al agente a cancelar un pedido que no existe o que no es suyo, con prisa y sin dar un identificador válido. No hay caos: el fallo es del propio agente.

Definición: [`scenarios/src/wonder_scenarios/definitions/bad-args.yaml`](../../scenarios/src/wonder_scenarios/definitions/bad-args.yaml).
El runner imprime el `trace_id` de cada petición: úsalo para abrir la misma traza en los dos productos.

## Qué mirar en Arize AX

1. Span **TOOL `cancel-order`**: mira `input.value`. ¿El `order_id` lo dio el usuario o lo devolvió antes `check-order-status`, o se lo inventó el modelo?
2. Eval **tool_selection**: `incorrect` cuando se llama a `cancel-order` con un identificador inventado en vez de consultar primero los pedidos.
3. Eval **trajectory_convergence** y el orden de las llamadas: la trayectoria correcta es `check-order-status` → (preguntar) → `cancel-order`.
4. Compara supervisor Sonnet frente a Haiku con `task scenario:model-swap` sobre estos mismos casos del dataset.

## Qué mirar en Dynatrace

1. **Nada anómalo**: latencia normal, cero errores, todo 200 OK. Compruébalo en el dashboard durante el escenario.
2. El span del servidor MCP termina bien: `cancel-order` devuelve `{success: false, error: "Order not found"}` como resultado normal de negocio.

Consulta de partida (Notebooks o Dashboards):

```
fetch spans
| filter service.name == "wonder-toys-mcp" and gen_ai.tool.name == "cancel-order"
| summarize calls = count(), errors = countIf(span.status_code == "error")
```

## Reversión

No cambia nada en el sistema; no hay nada que revertir.
