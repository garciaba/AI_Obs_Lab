# Runbook

Cómo preparar las cuentas, levantar el lab fase a fase, comprobar cada puerta y resolver
los problemas habituales. Para el porqué de cada decisión: [DECISIONS.md](DECISIONS.md).

## 1. Requisitos

| Qué | Versión probada | Para qué |
| --- | --- | --- |
| Docker Engine + Compose v2 | 29.x / 5.x (mínimo 20.10 / 2.24) | todos los servicios |
| Terraform | 1.17.2 | Google Cloud, Bindplane y Dynatrace |
| gcloud CLI | reciente | credenciales de Terraform para Google Cloud |
| Task | 3.54 (mínimo 3.37, por las tareas comodín) | punto de entrada |
| sops + age | 3.13 / 1.1 | secretos cifrados |
| uv | 0.11 | `task chat`, tests y lint en el host |

Memoria: ~4 GB para Bindplane server y ~3-4 GB para el resto. Linux o macOS con Docker
Desktop; el colector lee `/var/lib/docker/containers` y `/var/run/docker.sock` del host
Docker (con Docker Desktop son los de su máquina virtual, que es lo correcto).

Cuentas: proyecto de Google Cloud con Claude habilitado en Vertex AI, tenant de Arize AX,
entorno Dynatrace SaaS y licencia de Bindplane. Terraform se autentica con
`gcloud auth application-default login`.

## 2. Secretos

```bash
cp .env.example .env        # rellena los valores
task secrets:init           # una vez: crea tu clave age y .sops.yaml
task secrets:encrypt        # .env → secrets.enc.env (este sí va a Git)
```

En otra máquina: copia tu clave age (`~/.config/sops/age/keys.txt`) y `task secrets:decrypt`.
`.env`, `.lab/` y el estado de Terraform están en `.gitignore`.

| Variable | De dónde sale |
| --- | --- |
| `GCP_PROJECT_ID`, `GCP_REGION` | Proyecto y región de Vertex AI. Terraform usa tus Application Default Credentials; necesitas poder crear cuentas de servicio, roles y claves en el proyecto. |
| `GCP_BILLING_ACCOUNT` (opcional) | Cuenta de facturación, para crear el presupuesto con alarma. |
| `ARIZE_SPACE_ID`, `ARIZE_API_KEY` | Arize AX → Settings → API Keys (la clave necesita permisos sobre el espacio). |
| `DT_ENV_URL` | `https://<id>.live.dynatrace.com`, sin `.apps.` y sin barra final. |
| `DT_INGEST_TOKEN` + `DT_AUTH_SCHEME` | **Access token** (`dt0c01.*`, `Api-Token`) con `openTelemetryTrace.ingest`, `logs.ingest`, `metrics.ingest`; o **platform token** (`dt0s16.*`, `Bearer`) con `openpipeline:traces:ingest`, `openpipeline:logs:ingest`, `openpipeline:metrics:ingest`. |
| `DT_CONFIG_TOKEN` | Platform token para Terraform. Permisos orientativos: `document:documents:read/write/delete`, `document:trash.documents:delete`, `settings:objects:read/write`, `storage:spans:read`, `storage:logs:read`, `storage:metrics:read`, `storage:buckets:read`, `davis:analyzers:execute`. |
| `BINDPLANE_LICENSE` | Licencia interna. Debe incluir fuentes y procesadores `custom`. |
| `BINDPLANE_ADMIN_USER/PASSWORD` | Los eliges tú: son el usuario inicial del servidor. |
| `DBDOT_IMAGE` | Por defecto la imagen pública de DBDOT. Ver «Cambiar la imagen del colector». |

## 3. Arranque

`task up` encadena todo en el orden que impone el diseño. Las mismas etapas, una a una:

| Paso | Tarea | Qué hace |
| --- | --- | --- |
| 0 | `task gcp:apply` | Activa la API de Vertex AI, crea la cuenta de servicio `agentic-lab-vertex` (solo predicción), su clave en `.lab/gcp/credentials.json` y el presupuesto. |
| 0 | `task gate:0` | Llama a Sonnet 5.5 y Haiku 5.5 con esa cuenta de servicio. |
| 1 | `task build` | Imágenes del agente, servidor MCP (con el modelo de embeddings) y herramientas. |
| 1 | `task bindplane:up` | Bindplane server + su Postgres, Prometheus y transform agent; crea la organización inicial y guarda la secret key del proyecto en `.lab/bindplane.env`. |
| 1 | `task bindplane:apply` | Fuentes, procesadores, destinos y configuración `agentic-lab` (Terraform). |
| 1 | `task gate:1` | Arranca el colector y envía una traza de prueba con un marcador. |
| 2 | `task app:up` | Postgres, ChromaDB, seed, servidor MCP y agente. |
| 2 | `task gate:2` | Las 25 peticiones de referencia. |
| 3 | `task evals` | Dataset y experimento de regresión en AX; tareas online. |
| 3 | `task gate:3` | Dos experimentos (prompt `v1` y `v2-plain`) y su diferencia. |
| 4 | `task dynatrace:apply` | Dashboard y 4 alertas. |
| 4 | `task scenario:<nombre>` / `task gate:4` | Escenarios de fallo. |

### Qué comprobar en cada puerta

- **Puerta 0.** `task gate:0` imprime `agentic-lab-vertex@<proyecto>.iam.gserviceaccount.com`
  y una respuesta de cada modelo. Si falla con 403 o 404, habilita los modelos de Anthropic en
  Vertex AI → Model Garden (hay que aceptar sus condiciones una vez por proyecto) y comprueba
  que están disponibles en `GCP_REGION`.
- **Puerta 1.** `task gate:1` imprime un `trace_id` y un marcador `SECRET-PROMPT-…`. En
  Arize AX la traza tiene 3 spans (raíz `POST /poc` como CHAIN) y el marcador se lee; en
  Dynatrace (`fetch spans | filter trace.id == toUid("<id>")`) tiene 5 spans y el marcador
  no aparece. En la consola de Bindplane (http://localhost:3001) el colector
  `agentic-lab-collector` debe estar conectado y con la configuración `agentic-lab`.
- **Puerta 2.** `task gate:2` exige 25/25 respuestas con `trace_id`. Abre una en ambos
  productos: raíz `POST /chat` → `wonder_toys_supervisor` → `catalog_agent`/`orders_agent`
  → TOOL → (servidor MCP) `tools/call …` → `catalog.vector_search` / consultas SQL.
- **Puerta 3.** `task gate:3` debe terminar con `PUERTA 3 OK` (cae `format_compliance` con
  `v2-plain`). Los dos experimentos quedan en AX → Datasets → `wonder-toys-regression`.
- **Puerta 4.** Cada `task scenario:<nombre>` imprime sus `trace_id` y qué mirar; las fichas
  están en [scenarios/](scenarios/README.md).

## 4. Juez de las evals online (único paso manual)

Los experimentos usan el juez en local (Haiku 5.5 con la cuenta de servicio del lab). Las
**tareas online** las ejecuta Arize AX, que necesita su propia integración de IA con
Vertex AI: hay que dar acceso a tu proyecto de GCP a Arize (Arize AX → Settings → AI
Integrations → Vertex AI muestra a quién conceder acceso y la etiqueta de acceso del
proyecto). Después, una de dos:

```bash
ARIZE_AI_INTEGRATION_ID=<id de la integración creada en AX>   # en .env
# o bien, para que la cree el lab:
ARIZE_GCP_PROJECT_ACCESS_LABEL=<etiqueta que indica AX>
task evals:online
```

`task evals:online` es idempotente. Para ver qué crearía sin tocar nada:
`docker compose … run --rm tools wonder-evals online apply --dry-run`.

## 5. Operación

```bash
task chat -- "Show me some dinosaur toys"   # una pregunta (imprime trace_id)
task chat                                   # conversación con memoria de sesión
task load -- --repeat 3                     # tráfico normal
task logs -- agent                          # logs de un servicio
task seed                                   # restaura inventario, pedidos e índice vectorial
task scenario:reset                         # quita caos y ajustes; arranca Postgres
task down / task up                         # parar y volver a levantar (conserva datos)
task destroy                                # borrarlo todo
```

### Cambiar la imagen del colector

`DBDOT_IMAGE` en `.env`. Por defecto `ghcr.io/dynatrace/dynatrace-bindplane-otel-collector:1.4.2`
(pública). Con un registro interno, pon ahí su ruta. Si necesitas el colector BDOT de
Bindplane: `DBDOT_IMAGE=ghcr.io/observiq/bindplane-agent:1.113.1`; tiene los mismos
componentes que usa el pipeline (`tail_sampling`, `filelog`, `docker_stats`,
`cumulative_to_delta`). Después: `docker compose … up -d collector`.

### Cambiar el pipeline

Edita `infra/bindplane/pipeline/*.yaml` o los `.tf`, valida en local con
`task test:smoke` (usa los mismos fragmentos con un colector de referencia) y aplica con
`task bindplane:apply`: Bindplane despliega la versión nueva al colector por OpAMP. Nunca
se edita la configuración dentro del contenedor.

### Construir detrás de un proxy TLS corporativo

```bash
docker compose build --secret id=extra_ca,src=/ruta/a/ca-corporativa.pem
```

## 6. Problemas habituales

| Síntoma | Causa y arreglo |
| --- | --- |
| `task bindplane:up` se queda en «Esperando a que el servidor cargue sus tipos» | Licencia inválida o caducada (el servidor responde 452 «no license for organization»). Revisa `BINDPLANE_LICENSE` y `task logs -- bindplane`. |
| «no se pudo leer la secret key» | El formato del CLI cambió. Cópiala de la consola (Agents → Install Agent) a `BINDPLANE_SECRET_KEY` en `.lab/bindplane.env` y repite `task up`. |
| El colector no aparece en Bindplane | Secret key incorrecta (401 en `task logs -- collector`) o `BINDPLANE_REMOTE_URL` distinto de `http://bindplane:3001`. Tras cambiar la clave: `docker compose … up -d --force-recreate collector`. |
| `terraform apply` de Bindplane: `unknown ProcessorType custom` o error de *feature gate* | La licencia no incluye componentes `custom`. Plan B en DECISIONS.md («Si Bindplane no puede desplegar el pipeline»). |
| Trazas en Dynatrace pero no en Arize | Cabeceras o host de Arize: `ARIZE_SPACE_ID`, `ARIZE_API_KEY`, y `ARIZE_OTLP_HOST` si tu tenant es EU/CA. Mira el destino en la consola de Bindplane. |
| Trazas en Arize sin raíz | La raíz llega 30 s después (tail sampling). Si no llega, el colector se reinició a mitad de traza. |
| Dynatrace devuelve 401/403 al colector | `DT_AUTH_SCHEME` no corresponde al tipo de token, o faltan scopes de ingesta. |
| Métricas rechazadas en Dynatrace | Deben ser delta: comprueba que el procesador `cumulative-to-delta` sigue en la ruta de métricas. |
| `gate:0` falla con 404 sobre el modelo | El identificador no existe o no está en esa región. Míralo en Vertex AI → Model Garden (algunos llevan sufijo de versión, `<modelo>@<fecha>`) y ajusta `SUPERVISOR_MODEL_ID` / `SUBAGENT_MODEL_ID` o `GCP_REGION`. |
| `task gcp:apply` falla al crear el presupuesto | Tu usuario necesita permisos sobre la cuenta de facturación, o la moneda no coincide (`LAB_BUDGET_CURRENCY`). Deja `GCP_BILLING_ACCOUNT` vacío para omitirlo. |
| `task gcp:apply`: la organización prohíbe crear claves de cuenta de servicio | Política `iam.disableServiceAccountKeyCreation`. Alternativa en DECISIONS.md («Google Cloud»). |
| El agente responde 502 | Fallo de Vertex AI (credenciales, modelo no habilitado o cuota). `task logs -- agent`. |
| Tras `scenario:db-down` las primeras peticiones fallan | El pool tarda unos segundos en reconectar; es parte de lo que se ve en Dynatrace. |
