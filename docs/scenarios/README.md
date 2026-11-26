# Escenarios de fallo

Cada escenario se lanza con `task scenario:<nombre>`, se revierte solo y tiene su ficha con lo que hay que mirar y dónde. Sirven también de guión de demo, en este orden.

| # | Escenario | Comando | Qué enseña |
| --- | --- | --- | --- |
| 1 | [Herramienta lenta](slow-tool.md) | `task scenario:slow-tool` | Un fallo de rendimiento de infraestructura: los dos productos lo ven, pero Dynatrace lo detecta solo (alerta) y Arize AX lo sitúa dentro del razonamiento del agente. |
| 2 | [Herramienta caída](tool-error.md) | `task scenario:tool-error` | El error técnico lo ve Dynatrace; si el agente lo gestionó bien (¿lo reconoce o finge la compra?) solo lo dice Arize AX. |
| 3 | [Base de datos caída](db-down.md) | `task scenario:db-down` | Fallo de infraestructura clásico: Dynatrace da la causa raíz (la dependencia Postgres) antes y mejor. |
| 4 | [Argumentos erróneos](bad-args.md) | `task scenario:bad-args` | La lección central del lab: **todo devuelve HTTP 200** y la infraestructura está sana. |
| 5 | [Alucinación de catálogo](catalog-hallucination.md) | `task scenario:catalog-hallucination` | Un fallo de calidad de datos del RAG no produce errores. |
| 6 | [Prompt injection](prompt-injection.md) | `task scenario:prompt-injection` | Un ataque que viaja en los datos, no en la petición. |
| 7 | [Bucle de herramientas](tool-loop.md) | `task scenario:tool-loop` | El agente no falla: gasta. |
| 8 | [Cambio de modelo](model-swap.md) | `task scenario:model-swap` | Un cambio de modelo es un cambio de versión: se decide comparando calidad (Arize AX) frente a coste y latencia (Dynatrace). |
| 9 | [Throttling de Bedrock](bedrock-throttling.md) | `task scenario:bedrock-throttling` | Fallo de una dependencia externa: se ve en los dos productos, con papeles distintos. |

## Guión de demo sugerido (20 minutos)

1. `task load` y abre una traza normal en Arize AX y la misma en Dynatrace (mismo `trace_id`): contenido completo frente a redactado.
2. `task scenario:slow-tool`: Dynatrace avisa solo; Arize AX dice qué paso del agente es el lento.
3. `task scenario:db-down`: causa raíz en Dynatrace; degradación del agente en Arize AX.
4. `task scenario:bad-args` y `task scenario:catalog-hallucination`: todo en verde en Dynatrace, evals en rojo en Arize AX.
5. `task scenario:prompt-injection`: eval de seguridad y redacción en el colector.
6. `task scenario:model-swap`: calidad en Arize AX frente a coste y latencia en Dynatrace.

Si un escenario se interrumpe: `task scenario:reset`.
