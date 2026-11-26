# Decisiones

Decisiones tomadas al construir el lab sin preguntar: qué se decidió, por qué y cómo
cambiarlo. La fuente de verdad del alcance es [design.md](design.md).

**Estado de validación.** Se construyó sin credenciales. La sección
[Qué se validó y qué no](#qué-se-validó-y-qué-no) dice qué está probado y qué queda por
confirmar contra tus cuentas. Lo no validado está marcado con ⚠ en cada decisión.

## Punto de partida

| Decisión | Por qué | Cómo cambiarlo |
| --- | --- | --- |
| `docs/design.md` es una copia del documento de diseño; los dos diagramas se pasaron a texto | El repositorio estaba vacío y el diseño vivía en un documento externo | Sustituye el fichero |
| Un solo workspace de `uv` con un `uv.lock` para agente, servidor MCP, seed, evals y escenarios; Python 3.12 | Una única resolución de dependencias fijada (las convenciones GenAI siguen cambiando) | `uv lock --upgrade` |
| Una imagen parametrizada (`Dockerfile`, argumento `PACKAGES`) para los tres servicios Python | Menos ficheros que mantener; todas las versiones salen del lockfile | Separa Dockerfiles si divergen |
| Créditos en `NOTICE.md` | Catálogo, prompt, lógica de herramientas, 25 peticiones y plantillas de evaluadores vienen de Project Rosetta Stone (MIT) | — |

## Pipeline de telemetría (fase 1)

| Decisión | Por qué | Cómo cambiarlo |
| --- | --- | --- |
| Colector: **imagen pública de DBDOT** `ghcr.io/dynatrace/dynatrace-bindplane-otel-collector:1.4.2` | Existe y es pública; incluye todo lo que usa el pipeline. No hizo falta recurrir a BDOT | `DBDOT_IMAGE` en `.env` (runbook, «Cambiar la imagen del colector») |
| Bindplane 1.105.0 con las tres imágenes de GHCR (`bindplane-ee`, `bindplane-prometheus`, `bindplane-transform-agent`) y Postgres 16 | Es lo que usan los ejemplos oficiales de despliegue; el servidor exige Prometheus y transform agent remotos | Etiquetas en `compose.yaml` |
| El reparto se expresa con **rutas y grupos de procesadores** de `bindplane_configuration_v2`, no con el conector `forward` | Bindplane no tiene tipo de conector `forward`; una ruta con varios componentes copia la señal a cada rama, que es lo que hace `forward` en el cookbook de Arize | `infra/bindplane/configuration.tf` |
| `tail_sampling`, los filtros y las transformaciones de trazas son procesadores **`custom`** de Bindplane | Bindplane no tiene tipo para tail sampling. Los filtros y transformaciones sí tienen tipo nativo, pero con `custom` el YAML desplegado es exactamente el del cookbook y el que valida el smoke test | Cambia el `type` en `processors.tf` por `filter-by-condition` / `transform` |
| La lógica vive en `infra/bindplane/pipeline/*.yaml`; Terraform y el colector de referencia leen los mismos ficheros | Una sola fuente: lo que se prueba sin credenciales es lo que se despliega | Edita el fragmento y `task test:smoke` |
| No se usa el procesador propio de Arize (`filter-remap-processor`) | El cookbook ya lo evita con `tail_sampling` + filtro + `transform`; necesitaría un colector compilado a medida | — |
| Destino Arize: `otlp_grpc` genérico a `otlp.arize.com:443` con cabeceras `space_id` y `api_key` | Es la decisión fija del encargo. Bindplane trae además un destino `arize_ax`, que usa las cabeceras nuevas `authorization` / `arize-space-id` | En `destinations.tf`, `type = "arize_ax"` con parámetros `api_key`, `space_id`, `project_name` |
| Destino Dynatrace según `DT_AUTH_SCHEME`: `dynatrace_otlp` (nativo) con `Api-Token`; `otlp_grpc` en modo HTTP con cabecera `Authorization: Bearer …` con platform token | El destino nativo solo autentica con el esquema `Api-Token` | Variable `dt_auth_scheme` |
| `cumulative_to_delta` y `batch` con los tipos nativos de Bindplane | Existen como tipo y no tienen lógica propia del lab | `processors.tf` |
| Logs y métricas de contenedores con fuentes `custom` (`filelog` y `docker_stats`) | Bindplane no tiene tipo de fuente para Docker | `pipeline/source-docker-*.yaml` |
| El colector corre como **root** y monta `/var/lib/docker/containers` y el socket de Docker en solo lectura | La imagen usa el uid 10005, que no puede leer ni los logs ni el socket | Quita `user` y los volúmenes si no quieres logs/métricas de contenedores |
| Cada contenedor etiqueta sus logs con el servicio de compose (`log-opt labels`) | El fichero json-file no lleva el nombre del contenedor; así sale `docker.compose.service` sin consultar la API de Docker | `x-logging` en `compose.yaml` |
| Los logs de la app llegan dos veces a Dynatrace: por OTLP y como log de contenedor (`log.source = docker`) | El diseño pide ambas señales; las dos llevan `trace_id` | Quita la fuente de logs de Docker o el `LoggingHandler` |
| No se usa `gen_ai_normalizer` en el colector | `OPENINFERENCE_ENABLE_GENAI_SEMCONV=true` ya hace que la instrumentación emita `gen_ai.*`; se comprobó en las trazas locales | Añade un procesador `custom` con `gen_ai_normalizer` |
| La redacción añade al cookbook: `llm.tools`, contenido de `retrieval.documents.*` y `wonder.tool_results` | El lab tiene spans RETRIEVER y un atributo propio con resultados de herramientas | `pipeline/transform-redact-ai-content.yaml` |
| Organización inicial y secret key con `scripts/bindplane-bootstrap.sh`, no con Terraform ⚠ | El proveedor no tiene recursos para organización ni clave. No se pudo ejecutar contra un servidor con licencia: el script tolera varios formatos de salida y, si falla, dice cómo poner la clave a mano | El script |
| Los secretos de los destinos van como variables sensibles de Terraform y quedan en el estado local y en Bindplane | Es el camino documentado del proveedor. El estado está en `.gitignore` | Backend remoto cifrado si se comparte |
| Ficheros `.terraform.lock.hcl` **no** incluidos | Aquí los proveedores se compilaron desde el código fuente (el registro no era accesible), así que sus hashes no valdrían. Las versiones están fijadas con `=` | Tras tu primer `terraform init`, súbelos |

### Si Bindplane no puede desplegar el pipeline

Si tu licencia no incluye componentes `custom` o el despliegue falla, hay un plan B ya
escrito: `python3 infra/bindplane/render_reference.py --exporters real` genera la
configuración completa de colector (la del cookbook, con los mismos fragmentos). Se puede
montar en el colector con `--config`, perdiendo la gestión por OpAMP. No es el diseño, es
la alternativa más simple que funciona.

## Agente (fase 2)

| Decisión | Por qué | Cómo cambiarlo |
| --- | --- | --- |
| Supervisor = nodo de un `StateGraph` con dos «herramientas de traspaso»; cada llamada se envía con `Send` al nodo `catalog_agent` u `orders_agent`, que devuelve su resultado al supervisor | Es el patrón supervisor de LangGraph, con el grafo visible en la traza y delegaciones en paralelo. El supervisor (Sonnet) redacta la respuesta: así el escenario de cambio de modelo tiene efecto | `app/agent/src/wonder_agent/graph.py` |
| Los subagentes usan `langgraph.prebuilt.create_react_agent` | Funciona con las versiones fijadas; LangGraph lo marca como obsoleto en favor de `langchain.agents.create_agent` | Migrar al subir a LangGraph 2 |
| Herramientas con nombres `search-products`, `get-product`… y argumentos en `snake_case` (`user_id`, `order_id`) | Los nombres son los del original; los argumentos del original usaban alias camelCase que FastMCP no expresa en parámetros de función | `server.py`. La plantilla de `tool_selection` usa estos nombres |
| Catálogo e inventario en Postgres, además de los pedidos | El detalle de producto y el stock pasan por la base de datos: más dependencia real que observar y que romper | `store.py` |
| 3 pedidos de ejemplo para `eval-user-001` (uno por estado) | Las peticiones de seguimiento y cancelación tienen datos desde el primer momento | `seed.py` |
| El modelo de embeddings (all-MiniLM-L6-v2, ONNX) va dentro de la imagen del servidor MCP | El contenedor no necesita red para buscar ni para el seed | `PRELOAD_EMBEDDINGS` |
| El span del servidor MCP (`tools/call …`, emitido por FastMCP) se marca como **CHAIN**, no TOOL | El span TOOL ya lo crea LangChain en el agente; dos TOOL por llamada duplicarían los recuentos | `run_tool` en `server.py` |
| Un interceptor ejecuta cada llamada MCP con el span TOOL como contexto activo | El instrumentador de LangChain no deja sus spans como contexto de OpenTelemetry; sin esto el lado servidor colgaba de la raíz | `_trace_under_tool_span` en `service.py` |
| La app pone entrada, salida y `session.id` en el span HTTP raíz; el colector le pone el tipo CHAIN | El diseño pide que la raíz la marque el colector; sin entrada y salida, las evals sobre la raíz no tendrían qué evaluar | — |
| No se instrumenta botocore; las llamadas a Bedrock se ven como spans HTTP de `urllib3` | La instrumentación de botocore emite spans `gen_ai.*` propios y Arize mostraría cada llamada al modelo dos veces | Añade `opentelemetry-instrumentation-botocore` si lo prefieres en Dynatrace |
| Los spans HTTP de Bedrock cuelgan del span raíz, no del span LLM | Misma limitación de contexto; para el LLM no hay un punto de enganche como el interceptor | — |
| API sin streaming: `POST /chat` devuelve JSON con respuesta, `trace_id` y llamadas a herramientas | Más simple de probar y de usar desde escenarios y evals | `api.py` |
| Con Postgres caído, el agente sigue en modo degradado (memoria de sesión en RAM, `degraded: true`) | El diseño espera ver en AX «fallo de herramienta y respuesta del agente», no un 500 por el checkpointer | `AgentService.graphs` |
| `LLM_PROVIDER=fake`: modelo determinista sin red | Es el «Bedrock simulado» de los tests y del smoke test | — |
| Endpoints `/admin/overrides` (agente) y `/chaos` (servidor MCP) sin autenticación | El agente solo escucha en localhost y el servidor MCP solo en la red de compose; todo ajuste caduca solo | Añade un token si lo expones |

## AWS (fase 0)

| Decisión | Por qué | Cómo cambiarlo |
| --- | --- | --- |
| `AWS_PROFILE` es tu perfil de administración y solo lo usa Terraform; el lab corre con un **usuario IAM** propio cuya clave se escribe en `.lab/aws/credentials` | La puerta 0 pide que Bedrock responda «con el usuario del lab», y así los contenedores no ven tus credenciales | Para usar tu perfil: `LAB_AWS_DIR=$HOME/.aws` y `LAB_AWS_PROFILE=<perfil>` en `.env` |
| La política sale del data source `aws_bedrock_inference_profile` ⚠ | El propio perfil dice a qué modelos y regiones enruta; no hay regiones escritas a mano | `infra/aws/main.tf` |
| La clave de acceso queda en el estado local de Terraform | Inevitable con `aws_iam_access_key`; el estado está fuera de Git | Rol asumible + SSO |
| Presupuesto filtrado por `Service = "Amazon Bedrock"` ⚠ | Según la cuenta, los cargos de Claude figuran con otro nombre de servicio | Variable `budget_services` (lista vacía = toda la cuenta) |

## Evaluaciones (fase 3)

| Decisión | Por qué | Cómo cambiarlo |
| --- | --- | --- |
| Los 6 evaluadores de Wonder Toys, más `prompt_injection_safety` y `catalog_faithfulness`, más `trajectory_convergence` y `session_resolution` | Los dos últimos cubren los niveles trayectoria y sesión de la tabla del diseño | `evals/src/wonder_evals/evaluators.py` |
| Cambios en las plantillas originales: nombres de herramientas del lab; `tool_selection` recibe las llamadas con argumentos; `tool_response_handling` recibe los resultados y trata los errores | Sin argumentos ni resultados, el juez no puede detectar un `order_id` inventado ni una compra fingida | — |
| En los experimentos el juez corre **en local** (Haiku 5.5 en Bedrock con el usuario del lab) | No depende de la integración de IA de AX y cuesta lo mismo | `judge.py`, `JUDGE_MODEL_ID` |
| Las tareas online se crean con el **SDK de Python** (`client.tasks`, `client.evaluators`), no con el CLI `ax` | El SDK envuelve la misma API de tareas; el CLI 0.40 no tiene `query_filters`, que hacen falta para las tareas de traza y sesión | `online.py` |
| **Único paso manual**: la integración de IA de Bedrock en AX para el juez online | Arize necesita asumir un rol IAM de tu cuenta; su relación de confianza solo la muestra la consola de AX | Runbook, sección 4 |
| Las evals online se aplican al span raíz `POST /chat`, con `wonder.tools_used`, `wonder.tool_results` y `wonder.tool_call_count` como contexto ⚠ | Wonder Toys las configura a nivel de traza sobre entrada y salida; la raíz es el span que las tiene | `evals/tasks/online.yaml` |
| Sintaxis de `query_filter` y forma de las tareas de traza y sesión ⚠ | Construidas con los tipos del SDK 8.66 (validan en local) pero sin probar contra un tenant; la variable `{conversation}` de sesión es la más incierta | `evals/tasks/online.yaml` |
| Puerta 3 con una variante de prompt `v2-plain` (sin reglas de formato) que se activa en caliente | Demuestra el cambio sin reiniciar; debe caer `format_compliance` | `prompts.py` |
| `task destroy` borra también el dataset, los experimentos y el proyecto de AX | «Sin dejar rastro» | `online.destroy` |

## Escenarios y Dynatrace (fase 4)

| Decisión | Por qué | Cómo cambiarlo |
| --- | --- | --- |
| Los escenarios corren en el contenedor `tools`, dentro de la red de compose | El diseño solo expone en localhost el agente y Bindplane; el control de caos del servidor MCP no se publica | — |
| Triple reversión: `finally` del runner, TTL en servidor MCP y agente, y `defer` del Taskfile | «Autorreversible» aunque el runner muera | `ttl_seconds` en cada YAML |
| `db-down` lo ejecuta el Taskfile (`docker compose stop/start postgres`) | El contenedor de herramientas no controla Docker | — |
| `bedrock-throttling` depende de tu cuota | No se puede forzar un 429 real sin superar la cuota | `concurrency` en su YAML |
| `DT_CONFIG_TOKEN` es un **platform token** ⚠ | Dashboards (documentos), detectores de Davis y OpenPipeline son APIs de plataforma | `infra/dynatrace/versions.tf` |
| Terraform de Dynatrace **no crea tokens** | El token de ingesta lo aportas tú (`DT_INGEST_TOKEN`); `dynatrace_api_token` exigiría además un access token clásico | Añade el recurso si usas access tokens |
| Alertas con detectores de anomalías de Davis sobre consultas DQL de spans ⚠ | Funcionan con servicios ingeridos solo por OTLP; no dependen de que existan las métricas `dt.service.*` | `alerts.tf`; umbrales en `variables.tf` |
| OpenPipeline incluido pero **desactivado** (`enable_openpipeline = false`) | El recurso de rutas sustituye toda la tabla de rutas de spans del entorno | Actívalo solo si Terraform es el único que gestiona esas rutas |
| DQL del dashboard y de las alertas ⚠ | Escrito con los nombres de campo documentados, sin ejecutar contra un entorno | Ajusta en `dashboard.tf` / `alerts.tf` |

## Qué se validó y qué no

**Validado en esta construcción**

- 34 tests unitarios (agente con Bedrock simulado contra el servidor MCP real en memoria,
  evaluadores, tareas online en `--dry-run`, runner de escenarios) y lint con ruff.
- Build de las tres imágenes y `docker compose config` de las tres combinaciones.
- `terraform fmt` y `terraform validate` de los tres módulos, con los proveedores
  Bindplane 1.11.0, Dynatrace 1.109.0 y AWS 6.74.0.
- La lógica del pipeline con un colector real (`otelcol-contrib` 0.165.0): `task test:smoke`
  levanta agente, servidor MCP, ChromaDB y Postgres, lanza las 25 peticiones y un
  escenario, y comprueba 23 puntos: Dynatrace recibe todo y sin contenido de prompts; Arize
  recibe solo spans de IA, completos, con la raíz como CHAIN; traza unida entre agente y
  servidor MCP; logs de app y de contenedor con `trace_id`; métricas en delta.
- Nombres y parámetros de los tipos de Bindplane, contra las definiciones de la imagen
  `bindplane-ee:1.105.0`.

**No validado (sin credenciales ni licencia)**

- Ningún `terraform apply`: nada se creó en AWS, Dynatrace, Arize ni Bindplane.
- Que Bindplane genere y despliegue el pipeline tal como está descrito. El servidor
  arrancó aquí pero sin licencia responde 452. Es el mayor riesgo del diseño: `task gate:1`.
- La imagen de DBDOT y las de GHCR no se pudieron descargar aquí (registro bloqueado en
  este entorno); las etiquetas se comprobaron por la API del registro.
- El arranque de Bindplane (`scripts/bindplane-bootstrap.sh`).
- Las llamadas reales a Bedrock, los identificadores de perfil de inferencia y la política
  IAM.
- La creación de evaluadores y tareas en AX y la sintaxis de sus filtros.
- El DQL del dashboard y de las alertas, y los permisos exactos de `DT_CONFIG_TOKEN`.
- La puerta 3 con modelos reales (con el modelo simulado no hay diferencia entre prompts,
  como es de esperar).
