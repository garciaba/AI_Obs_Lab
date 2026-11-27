# Lab de IA agéntica: Arize AX + Bindplane + Dynatrace

Un comando levanta en Docker una aplicación agéntica realista (la tienda de juguetes
Wonder Toys) y su observabilidad completa: cada paso del agente se ve en **Arize AX**, la
aplicación y su infraestructura en **Dynatrace**, y toda la telemetría pasa por un colector
gestionado por **Bindplane**. Otro comando lo destruye sin dejar rastro.

```
task up        # levanta todo en orden
task gate:1    # comprueba el pipeline; después gate:2, evals, gate:3
task scenario:slow-tool    # provoca un fallo y lo revierte
task destroy   # lo borra todo
```

Diseño: [docs/design.md](docs/design.md) · Decisiones y estado de validación:
[docs/DECISIONS.md](docs/DECISIONS.md) · Operación: [docs/runbook.md](docs/runbook.md) ·
Escenarios: [docs/scenarios/](docs/scenarios/README.md)

## Qué hay dentro

```
Docker Compose en tu máquina                                   SaaS
┌───────────────────────────────────────────────────────┐
│  Escenarios y carga ──► Agente (LangGraph, FastAPI) ──┼──► Vertex AI (Google Cloud)
│                          supervisor  (Sonnet 5.5)     │    Claude en Model Garden
│                          ├ catalog_agent (Haiku 5.5)  │
│                          └ orders_agent  (Haiku 5.5)  │
│                               │ MCP (streamable HTTP) │
│                          Servidor MCP (FastMCP)       │
│                          5 herramientas de la tienda  │
│                           │              │            │
│                       ChromaDB        Postgres        │
│                       catálogo (RAG)  pedidos y sesiones
│                               │ OTLP/HTTP             │
│  Bindplane server ─OpAMP─► Colector DBDOT ────────────┼──► Arize AX   solo spans de IA, completos
│  (config por Terraform)   reparte, redacta, muestrea ─┼──► Dynatrace  todo, prompts redactados
└───────────────────────────────────────────────────────┘
```

| Carpeta | Contenido |
| --- | --- |
| `app/agent` | Supervisor + 2 subagentes en LangGraph, API FastAPI y CLI |
| `app/mcp-server` | Servidor FastMCP con las 5 herramientas y el control de caos |
| `app/seed` | Catálogo de 200 productos, esquema de Postgres y carga de ChromaDB |
| `app/common` | Telemetría compartida (trazas, logs y métricas por OTLP) |
| `scenarios` | Los 9 escenarios de fallo (un YAML cada uno), su runner y las puertas 0-2 |
| `evals` | Dataset, 10 evaluadores, experimentos y tareas online de Arize AX |
| `infra/gcp` | Cuenta de servicio mínima para invocar Claude en Vertex AI y presupuesto |
| `infra/bindplane` | Fuentes, procesadores, destinos y configuración del colector |
| `infra/dynatrace` | Dashboard, alertas y OpenPipeline (opcional) |
| `docs` | Diseño, decisiones, runbook y una ficha por escenario |
| `.github/workflows` | CI sin credenciales y experimento de regresión |

## Requisitos

- Docker Engine con Compose v2, Terraform ≥ 1.9, [Task](https://taskfile.dev) ≥ 3.37,
  sops + age y [uv](https://docs.astral.sh/uv/). Unos 8 GB de RAM libres.
- Proyecto de Google Cloud con Claude Sonnet 5.5 y Haiku 5.5 habilitados en Vertex AI
  Model Garden, y `gcloud` autenticado (`gcloud auth application-default login`).
- Tenant de Arize AX, entorno Dynatrace SaaS y licencia de Bindplane.

## Secretos

```bash
cp .env.example .env
```

Rellena `.env` (cada variable está explicada en el propio fichero y en el
[runbook](docs/runbook.md#2-secretos)):

| Variable | Valor |
| --- | --- |
| `GCP_PROJECT_ID`, `GCP_REGION` | Proyecto y región de Vertex AI (`europe-west1`) |
| `ARIZE_SPACE_ID`, `ARIZE_API_KEY`, `ARIZE_PROJECT_NAME` | Espacio, clave y proyecto de AX (`agentic-lab`) |
| `DT_ENV_URL` | `https://<id-entorno>.live.dynatrace.com` |
| `DT_INGEST_TOKEN`, `DT_AUTH_SCHEME` | Token de ingesta OTLP; `Bearer` (platform) o `Api-Token` (access) |
| `DT_CONFIG_TOKEN` | Platform token para Terraform |
| `BINDPLANE_LICENSE`, `BINDPLANE_ADMIN_USER`, `BINDPLANE_ADMIN_PASSWORD` | Licencia y usuario inicial del servidor |
| `DBDOT_IMAGE` | Imagen del colector (por defecto, la pública de DBDOT) |

`.env` no va a Git. Para versionar los secretos cifrados con sops + age:

```bash
task secrets:init      # una vez: tu clave age y .sops.yaml
task secrets:encrypt   # .env → secrets.enc.env
```

## Orden de arranque

`task up` lo encadena; cada paso es también una tarea suelta.

1. **Google Cloud** (`task gcp:apply`): cuenta de servicio mínima y presupuesto. → `task gate:0`
2. **Imágenes** (`task build`).
3. **Bindplane server** (`task bindplane:up`): arranca el servidor y lee su secret key.
4. **Pipeline** (`task bindplane:apply`): Terraform habla con el servidor y crea la
   configuración del colector. → `task gate:1`
5. **Colector y aplicación** (`task app:up`): el colector recibe su configuración por
   OpAMP; arrancan Postgres, ChromaDB, seed, servidor MCP y agente. → `task gate:2`
6. **Dynatrace** (`task dynatrace:apply`): dashboard y alertas.

Después, las evaluaciones (`task evals`, `task gate:3`) y los escenarios.

| Puerta | Comando | Se supera cuando |
| --- | --- | --- |
| 0 | `task gate:0` | Vertex AI responde con la cuenta de servicio del lab |
| 1 | `task gate:1` | La misma traza aparece en Arize AX (completa) y en Dynatrace (redactada) |
| 2 | `task gate:2` | Las 25 peticiones de referencia producen trazas unidas de extremo a extremo |
| 3 | `task gate:3` | Un cambio de prompt muestra su diferencia en un experimento |
| 4 | `task gate:4` | Los 9 escenarios se lanzan y se revierten; dashboard y alertas desplegados |

## Uso

```bash
task chat -- "Show me some dinosaur toys"    # una pregunta; imprime el trace_id
task chat                                    # conversación con memoria de sesión
task load                                    # las 25 peticiones de referencia
task seed                                    # restaura catálogo, inventario y pedidos
task evals                                   # dataset + experimento + tareas online en AX
task scenario:list
task down                                    # para; `task up` vuelve a levantar
task destroy                                 # borra todo: AX, Dynatrace, Bindplane, GCP y local
```

Escenarios (`task scenario:<nombre>`, se revierten solos):

| Nombre | Fallo | Dónde se ve |
| --- | --- | --- |
| `slow-tool` | El MCP añade latencia al seguimiento de pedidos | Ambos; alerta en Dynatrace |
| `tool-error` | El MCP devuelve error en la compra | Dynatrace el error; AX la reacción del agente |
| `db-down` | Postgres parado | Dynatrace la causa raíz |
| `bad-args` | Cancelar un pedido inexistente | Solo AX (todo 200 OK) |
| `catalog-hallucination` | RAG vacío o irrelevante | Solo AX |
| `prompt-injection` | Instrucciones ocultas en la ficha de producto | Solo AX |
| `tool-loop` | Recursión alta y petición ambigua | AX la trayectoria; Dynatrace tokens y latencia |
| `model-swap` | Supervisor de Sonnet a Haiku | AX el experimento; Dynatrace coste y latencia |
| `vertex-throttling` | Carga por encima de la cuota de Vertex AI | Ambos |

## Validar sin credenciales

```bash
task check      # lint, tests, terraform validate, compose config y smoke test
```

El smoke test (`task test:smoke`, también en CI) levanta la aplicación con un modelo
simulado y un colector de referencia que usa **los mismos fragmentos de pipeline** que
Terraform carga en Bindplane, con salida a ficheros, y comprueba el reparto, la redacción
y la traza unida.

## Estado

Construido y validado sin credenciales: nada se ha aplicado todavía contra Google Cloud, Arize AX,
Dynatrace ni un Bindplane con licencia. Lo que falta por confirmar está en
[docs/DECISIONS.md](docs/DECISIONS.md#qué-se-validó-y-qué-no).
