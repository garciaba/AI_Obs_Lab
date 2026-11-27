# Lab de IA agéntica: Arize AX + Bindplane + Dynatrace — Diseño

> Copia del documento de diseño (fuente de verdad del lab). Los dos diagramas del original se reproducen aquí como texto.
>
> **Cambio posterior al diseño:** el LLM ya no es Claude en Amazon Bedrock sino **Claude en Vertex AI (Google Cloud)**. Donde este documento dice Bedrock, AWS, perfiles de inferencia EU, `infra/aws` o «throttling de Bedrock», el repositorio usa Vertex AI, una cuenta de servicio de GCP, `infra/gcp` y el escenario `vertex-throttling`. El resto del diseño no cambia. Detalle en [DECISIONS.md](DECISIONS.md#google-cloud-fase-0).

2026-11-26 · @Miguel

## Objetivo y alcance

Un solo comando levanta en Docker una aplicación agéntica realista; cada paso del agente se ve en Arize AX y la aplicación y su infraestructura en Dynatrace, con toda la telemetría pasando por Bindplane. Otro comando lo destruye sin dejar rastro.

Qué debe enseñar:

- Cómo se compone una arquitectura agéntica: orquestador, LLM, herramientas vía MCP, memoria y RAG, subagentes.
- Qué resuelve Arize AX (trazas de agente, evaluaciones, datasets, experimentos) y qué resuelve Dynatrace (servicios, logs, métricas, correlación, detección de problemas).
- Cómo un pipeline de telemetría reparte, filtra y redacta datos antes de llegar a cada backend.
- Cómo se manifiestan los fallos típicos de un agente en cada herramienta.

Fuera de alcance en esta versión: Kubernetes, alta disponibilidad, volumen de producción, modelos locales (Ollama), Phoenix y dt-evals.

## Decisiones tomadas

Se eligió lo más estándar en cada capa y lo más ligero para una sola máquina.

| Capa | Elección | Motivo |
| --- | --- | --- |
| Framework de agentes | LangGraph (Python) | El más adoptado en 2026, MCP nativo, instrumentado por OpenInference |
| LLM | Claude en Amazon Bedrock | Sin GPU ni RAM local; añade el ángulo AWS |
| Instrumentación | OpenInference con atributos gen_ai.* activados | Una sola instrumentación válida para Arize AX y Dynatrace |
| Plataforma de agentes | Arize AX (tenant de pago) | Evals online, datasets, experimentos y monitores |
| Pipeline de telemetría | Bindplane self-hosted (licencia interna) + colector DBDOT | Plano de control y colector dentro del mismo compose |
| Observabilidad full-stack | Dynatrace SaaS vía OTLP/HTTP | Servicios, logs, métricas y problemas |
| Ejecución | Docker Compose | Ligero y reproducible |
| Como código | Terraform + Taskfile + Git | Todo se crea y destruye por código |
| Descartado | Ollama, Phoenix, dt-evals, Kubernetes | Máquina, solape con AX o fuera de alcance |

## Arquitectura

Todo corre en un único Docker Compose salvo el LLM y los dos backends de observabilidad, que son SaaS.

```
Docker Compose en tu máquina                                   Servicios externos
┌───────────────────────────────────────────────────────┐
│ Aplicación agéntica                                   │
│  Escenarios y carga ──► Agente (LangGraph) ───────────┼──► Amazon Bedrock
│  (25 peticiones+fallos)  supervisor + 2 subagentes    │    (Claude Sonnet y Haiku)
│                               │                       │
│                          Servidor MCP                 │
│                          5 herramientas               │
│                           │        │                  │
│                       ChromaDB   Postgres             │
│                       (RAG)      (pedidos, sesiones)  │
│                               │ OTLP/HTTP             │
│ Pipeline de telemetría        ▼                       │
│  Bindplane server ──► Colector DBDOT ─────────────────┼──► Arize AX (solo spans de IA, evals)
│  (config vía OpAMP)   reparte, redacta, muestrea ─────┼──► Dynatrace (todo, prompts redactados)
└───────────────────────────────────────────────────────┘
```

El agente llama a Bedrock y al servidor MCP, que accede a ChromaDB y Postgres; todos los servicios envían su telemetría al colector, que Bindplane configura y que reparte hacia Arize AX y Dynatrace.

## Flujos de telemetría

Los servicios envían todo por OTLP/HTTP a un único colector. Dynatrace recibe todas las señales con el contenido de los prompts redactado; Arize AX recibe solo los spans de IA, completos. La base es el [ejemplo oficial de doble exportación de Arize](https://arize.com/docs/ax/cookbooks/instrument/send-telemetry-to-arize-and-dynatrace), adaptado al colector gestionado por Bindplane.

| Señal | Origen | Procesado en el colector | Destino |
| --- | --- | --- | --- |
| Spans de IA (agente, LLM, herramienta, retriever) | OpenInference en el agente y en el servidor MCP | Filtro de spans de IA, raíz marcada como CHAIN, proyecto de AX | Arize AX |
| Todas las trazas | Lo anterior + auto-instrumentación HTTP y base de datos | Redacción de entradas, salidas y mensajes | Dynatrace |
| Logs de aplicación | logging de Python vía OTLP, con trace_id | Batch | Dynatrace |
| Logs de contenedores | Ficheros de log de Docker leídos por el colector | Parseo y atributos del contenedor | Dynatrace |
| Métricas | Métricas OTel de la app y de los contenedores | cumulative_to_delta | Dynatrace |

Detalles que condicionan el diseño:

- Dynatrace solo acepta OTLP por HTTP/protobuf en `https://<entorno>.live.dynatrace.com/api/v2/otlp`; el token necesita los scopes de ingesta de trazas, logs y métricas.
- Arize AX recibe por gRPC en `otlp.arize.com:443` con las cabeceras `space_id` y `api_key`; si se usa HTTP, sin compresión gzip.
- El colector reparte con un conector `forward` en dos ramas y usa tail sampling (espera de 30 s) para colgar la raíz de cada traza en AX; por eso necesita ver todos los spans de una traza.
- `openinference-instrumentation-mcp` solo propaga el contexto; el servidor MCP necesita su propia instrumentación para que la traza llegue unida.
- `OPENINFERENCE_ENABLE_GENAI_SEMCONV=true` en ambos servicios, para que la app AI Observability de Dynatrace entienda los spans.
- La configuración del colector vive en Bindplane y llega al colector por OpAMP; nunca se edita a mano dentro del contenedor.

## Diseño del agente

El agente parte de Wonder Toys, la tienda de juguetes de referencia de Arize en [Project Rosetta Stone](https://github.com/Arize-ai/project-rosetta-stone), en su versión LangGraph para AX. Se amplía para que tenga piezas reales que observar y romper.

| Pieza | Referencia de Arize | En el lab |
| --- | --- | --- |
| Dominio | Agente de compra: buscar, ver detalle, comprar, seguir y cancelar pedidos | Igual |
| Orquestación | Un agente LangGraph | Supervisor que deriva a un subagente de catálogo y otro de pedidos |
| Herramientas | 5 herramientas dentro del proceso | Las mismas 5 en un servidor MCP aparte (FastMCP), con el contexto de traza propagado |
| RAG | ChromaDB, embeddings all-MiniLM-L6-v2, 200 productos | Igual |
| Pedidos | En memoria | Postgres, para tener una dependencia real visible en Dynatrace |
| Memoria de conversación | No | Checkpointer de LangGraph en Postgres; cada sesión lleva `session.id` para las evals de sesión en AX |
| LLM | Anthropic Claude directo | Claude en Bedrock vía Converse (langchain-aws): Sonnet 5.5 en el supervisor, Haiku 5.5 en los subagentes |
| Evals | 25 peticiones sintéticas y 6 evaluadores | Igual, más los de los escenarios de fallo |

Notas:

- Bedrock: Sonnet 5.5 solo se invoca mediante perfil de inferencia ([ficha del modelo](https://docs.aws.amazon.com/bedrock/latest/userguide/model-card-anthropic-claude-sonnet-5-5.html)). Se usarán los perfiles EU `eu.anthropic.claude-sonnet-5-5` y `eu.anthropic.claude-haiku-5-5` ([ficha de Haiku 5.5](https://docs.aws.amazon.com/bedrock/latest/userguide/model-card-anthropic-claude-haiku-5-5.html)).
- Interfaz: API HTTP (FastAPI) y un cliente de línea de comandos; sin frontend en la primera versión.
- Tener dos modelos permite comparar coste y calidad, y provocar el escenario de cambio de modelo.

## Todo como código

Un único repositorio Git contiene la aplicación, la infraestructura y la configuración de los tres productos; nada se configura a mano en una consola.

```
agentic-lab/
├── Taskfile.yml           # punto de entrada: up, down, scenario, evals
├── compose.yaml           # todos los servicios, imágenes con versión fija
├── .env.example           # variables sin secretos
├── secrets.enc.env        # secretos cifrados con sops + age
├── app/
│   ├── agent/             # LangGraph (supervisor + subagentes) y FastAPI
│   ├── mcp-server/        # FastMCP con las 5 herramientas
│   └── seed/              # carga de ChromaDB y esquema de Postgres
├── scenarios/             # un fichero por escenario de fallo + runner
├── evals/                 # dataset, evaluadores y experimentos de AX
├── infra/
│   ├── aws/               # IAM mínima para invocar los perfiles de Bedrock
│   ├── bindplane/         # fuentes, procesadores, destinos y configuración del colector
│   └── dynatrace/         # tokens, dashboards, alertas y OpenPipeline
├── docs/                  # diseño, runbook y qué mirar en cada escenario
└── .github/workflows/     # lint, build, terraform validate y smoke test
```

| Pieza | Herramienta | Qué gestiona |
| --- | --- | --- |
| Servicios locales | Docker Compose | Agente, MCP, ChromaDB, Postgres, Bindplane server, colector |
| Bindplane | Terraform, proveedor de Bindplane | Fuentes, procesadores, destinos y despliegue de la configuración al colector |
| Dynatrace | Terraform, proveedor de Dynatrace | Tokens de ingesta, dashboards, alertas, reglas de OpenPipeline |
| AWS | Terraform, proveedor de AWS | Usuario o rol IAM limitado a los dos perfiles de inferencia |
| Arize AX | Python (SDK de AX) dentro de `evals/` | Datasets, experimentos y evaluadores (ver riesgos) |
| Orquestación | Taskfile | Orden de arranque, carga de datos, escenarios, destrucción |
| Secretos | sops + age | Claves de AWS, Dynatrace, Arize y la licencia de Bindplane |

Orden de arranque que impone el diseño: primero Bindplane server, luego `terraform apply` sobre Bindplane (el proveedor habla con el servidor), después colector y aplicación. `task up` lo encadena; `task destroy` lo deshace en orden inverso.

## Escenarios de fallo

Cada escenario se lanza con `task scenario:<nombre>` y se revierte solo. La lección central: varios fallos de agente dan HTTP 200 y solo aparecen en Arize AX, y los fallos de infraestructura se diagnostican antes en Dynatrace.

| Escenario | Cómo se provoca | Qué se ve en Arize AX | Qué se ve en Dynatrace |
| --- | --- | --- | --- |
| Herramienta lenta | El MCP añade latencia al seguimiento de pedidos | Span TOOL largo dentro de la traza del agente | Latencia del servicio MCP; alerta definida en Terraform |
| Herramienta caída | El MCP devuelve error en la compra | Cómo reacciona el agente (eval Tool Response Handling) | Tasa de errores y logs con trace_id |
| Base de datos caída | Se para el contenedor de Postgres | Fallo de herramienta y respuesta del agente | Dependencia caída y causa raíz |
| Argumentos erróneos | Peticiones que inducen a cancelar un pedido inexistente | Eval Tool Selection y argumentos del span | Nada anómalo: todo 200 OK |
| Alucinación de catálogo | RAG vacío o con productos irrelevantes | Correctness baja; documentos recuperados en el span RETRIEVER | Nada anómalo |
| Prompt injection | Ficha de producto con instrucciones ocultas | Eval de seguridad sobre la respuesta | Nada anómalo |
| Bucle de herramientas | Límite de recursión alto y petición ambigua | Eval Tool Call Count disparada | Tokens, coste y latencia en AI Observability |
| Cambio de modelo | Supervisor pasa de Sonnet 5.5 a Haiku 5.5 | Experimento que compara las evals de ambas versiones | Cambio de latencia y coste por petición |
| Throttling de Bedrock | Carga concurrente por encima de la cuota | Spans LLM con error y reintentos | Errores de la dependencia externa y latencia |

Cada escenario tendrá en `docs/` una ficha con lo que hay que mirar y dónde, útil también como guión de demo.

## Evaluaciones y experimentos en Arize AX

Las evaluaciones viven en `evals/` y se aplican por código. El [SDK de Python v8](https://arize.com/docs/api-clients/python/version-8/overview.md) crea los datasets y lanza los experimentos; las evals online sobre trazas son tareas que se crean con [ax tasks create](https://arize.com/docs/api-clients/cli/tasks) o con la [API de tareas](https://arize.com/docs/api-reference/tasks/create-task.md), hoy en beta.

| Nivel | Qué evalúa | Cuándo corre | Cómo se crea |
| --- | --- | --- | --- |
| Span | Tool Selection, Tool Response Handling, Correctness, Format Compliance | Online, sobre un porcentaje de las trazas del proyecto | Tarea de AX desde `evals/tasks/` |
| Trayectoria | Tool Call Count y si el agente converge | Online | Tarea de AX |
| Sesión | Si la conversación completa resolvió la petición | Online, sobre sesiones | Tarea de AX |
| Regresión | Dataset de 25 peticiones de Wonder Toys + casos de cada escenario | En cada cambio de prompt o modelo | `client.experiments.run()` en GitHub Actions |

Los seis evaluadores de Wonder Toys se reutilizan tal cual; se añaden uno de seguridad (prompt injection) y uno de fidelidad al catálogo (alucinación). El juez será Claude Haiku 5.5.

## Requisitos, costes y seguridad

El lab debería caber en un portátil de 16 GB de RAM; el único coste variable son los tokens de Bedrock.

| Recurso | Necesidad | Nota |
| --- | --- | --- |
| Bindplane server | 2 CPU y 4 GB de RAM | Mínimo que pide la [guía de Docker Compose](https://bindplane.com/docs/advanced-setup/docker-installation/server/install); incluye su propio Postgres |
| Resto de servicios | Unos 3-4 GB de RAM | Aproximado: agente, MCP, ChromaDB con embeddings pequeños, Postgres, colector |
| Software | Docker Engine 20.10+, Compose 2.0+, Terraform, Task, sops, uv | Versiones fijadas en el repo |
| Cuentas | Dynatrace SaaS, tenant de Arize AX, cuenta AWS con acceso a Claude en Bedrock, licencia interna de Bindplane | Haiku 5.5 puede requerir activar el acceso al modelo |
| Coste Bedrock | Proporcional a los tokens de escenarios, carga y evals | Presupuesto con alarma en AWS Budgets, definido en Terraform |

Seguridad:

- Secretos solo en `secrets.enc.env` (sops + age); el `.env` en claro queda fuera de Git.
- IAM mínima: solo invocar los dos perfiles de inferencia EU y los modelos a los que apuntan.
- Dynatrace recibe los prompts y respuestas redactados en el colector; Arize AX los recibe completos.
- Datos sintéticos: catálogo y pedidos de Wonder Toys, sin datos personales reales.
- Solo se exponen en localhost la API del agente y la consola de Bindplane (puerto 3001).

## Plan de construcción

Se construye en cinco fases, y no se pasa a la siguiente sin cumplir su puerta. La fase 1 va antes que el agente porque valida el mayor riesgo: que Bindplane pueda repartir la telemetría como pide Arize.

| Fase | Contenido | Puerta |
| --- | --- | --- |
| 0 · Cuentas y accesos | Acceso a Sonnet 5.5 y Haiku 5.5 en Bedrock (EU) e IAM mínima en Terraform. Tenant de Arize AX, token de Dynatrace, licencia de Bindplane, repo y sops | Una llamada a Bedrock responde con el usuario del lab |
| 1 · Pipeline de telemetría (prueba de concepto) | Bindplane server y colector DBDOT en Compose, configuración con Terraform. Un script mínimo con OpenInference envía trazas, logs y métricas | La misma traza aparece en Arize AX (completa) y en Dynatrace (redactada) |
| 2 · Agente | Wonder Toys en LangGraph sobre Bedrock: supervisor, 2 subagentes y servidor MCP. ChromaDB con el catálogo, Postgres para pedidos y sesiones | Las 25 peticiones de referencia producen trazas unidas de extremo a extremo |
| 3 · Evaluaciones | Dataset y experimentos con el SDK de AX, tareas online con el CLI ax. Experimento de regresión en GitHub Actions | Un cambio de prompt muestra su diferencia en un experimento |
| 4 · Escenarios y demo | 9 escenarios de fallo con task; dashboards y alertas de Dynatrace en Terraform. Una ficha por escenario: qué mirar en Arize AX y en Dynatrace | — |

Cada fase termina con código en el repo y un `task` que la reproduce desde cero.

## Riesgos y preguntas abiertas

El riesgo principal es que Bindplane no pueda expresar todo el pipeline de doble exportación de Arize; se valida en la fase 1 antes de construir nada más.

| Riesgo | Impacto | Mitigación |
| --- | --- | --- |
| El modelo de fuentes, procesadores y destinos de Bindplane no cubre el conector `forward`, el tail sampling o varios pipelines encadenados | Habría que simplificar el reparto a AX | Prueba en la fase 1; alternativa: procesadores personalizados de Bindplane o un reparto más simple sin raíz CHAIN |
| El ejemplo de Arize menciona un procesador propio que no viene en la distribución de Dynatrace ni en `otelcol-contrib` | Puede requerir un colector compilado a medida | Confirmar si el pipeline lo necesita; evitarlo si es posible |
| Imagen y versión del colector DBDOT | La normalización de OpenInference en Dynatrace pide DBDOT 1.1.2 o superior | Confirmar registro e imagen internamente |
| En AX, Wonder Toys configura los evaluadores en la consola | Rompería el "todo como código" | Probar a crearlos con el CLI `ax` o la API; si no, documentar ese único paso manual |
| Las convenciones GenAI de OpenTelemetry siguen en estado Development | Los nombres de atributos pueden cambiar | Versiones fijadas y lockfile |
| Acceso y cuotas de Claude en Bedrock | Haiku 5.5 sin acceso o throttling temprano | Activar el acceso en la fase 0; el throttling es además un escenario |
| Cobertura del proveedor de Terraform de Bindplane | Algún recurso podría no estar soportado | Alternativa: recursos YAML aplicados con el CLI de Bindplane |

Preguntas resueltas:

- Dynatrace: entorno propio. El repo usa marcadores (`DT_ENV_URL`, `DT_INGEST_TOKEN`, `DT_AUTH_SCHEME`, `DT_CONFIG_TOKEN`) y admite tanto token platform (`Bearer`) como access (`Api-Token`).
- AWS: eu-west-1 con los perfiles de inferencia EU.
- Repo: GitHub personal; la construcción se hace desde Claude Code con el repo seleccionado.
- Kubernetes con Dynatrace Operator: descartado.

## Fuentes

- [Arize AX: Send telemetry to Arize AX and Dynatrace](https://arize.com/docs/ax/cookbooks/instrument/send-telemetry-to-arize-and-dynatrace)
- [Arize: Project Rosetta Stone](https://arize.com/blog/project-rosetta-stone-instrumenting-agents-any-framework/)
- [Arize AX: MCP tracing](https://arize.com/docs/ax/integrations/python-agent-frameworks/model-context-protocol/mcp-tracing)
- [Arize AX: CLI de tareas](https://arize.com/docs/api-clients/cli/tasks)
- [Dynatrace: Bindplane in Dynatrace](https://docs.dynatrace.com/docs/ingest-from/bindplane)
- [Dynatrace: Get started with OpenInference](https://docs.dynatrace.com/docs/observe/dynatrace-for-ai-observability/get-started/openinference)
- [Bindplane: destino Dynatrace](https://docs.bindplane.com/integrations/destinations/dynatrace)
- [Bindplane: Terraform](https://docs.bindplane.com/configuration/bindplane/terraform)
- [Bindplane: instalación en Docker Compose](https://docs.bindplane.com/deployment/docker/server/install-bindplane-in-docker-compose)
- [AWS: ficha de Claude Haiku 5.5 en Bedrock](https://docs.aws.amazon.com/bedrock/latest/userguide/model-card-anthropic-claude-haiku-5-5.html)

