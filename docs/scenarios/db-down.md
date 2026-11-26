# 3. Base de datos caída

```bash
task scenario:db-down
```

**Qué enseña.** Fallo de infraestructura clásico: Dynatrace da la causa raíz (la dependencia Postgres) antes y mejor. Arize AX enseña cómo se degradó el agente.

## Cómo se provoca

`task scenario:db-down` para el contenedor `postgres`, lanza 9 peticiones (pedidos, detalle de producto, compra) y vuelve a arrancarlo con un `defer`, aunque el runner falle.

Definición: [`scenarios/src/wonder_scenarios/definitions/db-down.yaml`](../../scenarios/src/wonder_scenarios/definitions/db-down.yaml).
El runner imprime el `trace_id` de cada petición: úsalo para abrir la misma traza en los dos productos.

## Qué mirar en Arize AX

1. Los spans **TOOL** fallan con «temporarily unavailable»; el span RETRIEVER de la búsqueda sí funciona (ChromaDB sigue arriba) pero el detalle de producto no.
2. Respuesta del agente: debe explicar que no puede consultar ahora, sin inventar productos ni estados de pedido (**catalog_faithfulness**, **tool_response_handling**).
3. Las trazas llevan `wonder.degraded = true` en la raíz: el agente siguió atendiendo sin memoria de sesión persistente.

## Qué mirar en Dynatrace

1. **Distributed Tracing**: spans de base de datos con error en `wonder-toys-mcp` y en `wonder-toys-agent` (checkpointer), todos contra el mismo host `postgres`.
2. **Dashboard** → *Postgres: consultas y errores*: las consultas caen a cero y suben los errores de conexión.
3. **Logs de contenedores**: `docker.compose.service == "postgres"` muestra la parada y el arranque; el agente registra «session store (Postgres) unavailable».
4. **Problems**: alertas de errores del MCP y del agente; la causa común es la dependencia.

Consulta de partida (Notebooks o Dashboards):

```
fetch logs
| filter docker.compose.service == "postgres" or contains(content, "unavailable")
| fields timestamp, docker.compose.service, service.name, content
| sort timestamp desc
```

## Reversión

`defer` del Taskfile: `docker compose start postgres`. Si algo queda raro: `task scenario:reset`.
