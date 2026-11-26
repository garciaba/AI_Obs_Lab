"""Evaluadores y tareas online de Arize AX, creados por código.

Lee `evals/tasks/*.yaml`, crea (o reutiliza) en el espacio de AX los evaluadores de
`evaluators.py` y crea las tareas que los ejecutan de forma continua sobre el proyecto.
Usa el SDK de Python v8 (`client.evaluators`, `client.tasks`), que envuelve la API de
tareas; el CLI `ax` no puede expresar hoy las tareas de traza y sesión (`query_filters`).

Requisito: una integración de IA en AX para el juez (Bedrock con Claude Haiku 5.5). Se
indica con ARIZE_AI_INTEGRATION_ID o, si no existe, se crea con ARIZE_BEDROCK_ROLE_ARN
(rol IAM que Arize puede asumir). Ver docs/runbook.md.
"""

from __future__ import annotations

import os
from pathlib import Path

import yaml

from wonder_evals.evaluators import AX_CODE_IMPORTS, CODE_EVALUATORS, LLM_EVALUATORS
from wonder_evals.experiment import ax_client

def _tasks_dir() -> Path:
    """evals/tasks en el repo, o la copia empaquetada dentro de la imagen de herramientas."""
    if os.environ.get("EVAL_TASKS_DIR"):
        return Path(os.environ["EVAL_TASKS_DIR"])
    packaged = Path(__file__).parent / "tasks"
    return packaged if packaged.is_dir() else Path(__file__).parents[2] / "tasks"


TASKS_DIR = _tasks_dir()
PREFIX = "agentic-lab"
COMMIT_MESSAGE = "Managed by agentic-lab evals/ (wonder_evals.online)"


def load_task_specs(directory: Path | None = None) -> list[dict]:
    specs: list[dict] = []
    for path in sorted((directory or TASKS_DIR).glob("*.yaml")):
        specs.extend(yaml.safe_load(path.read_text()).get("tasks", []))
    return specs


def _id(obj) -> str:
    for attr in ("id", "evaluator_id", "task_id"):
        value = getattr(obj, attr, None)
        if value:
            return value
    inner = getattr(obj, "evaluator", None)
    if inner is not None:
        return _id(inner)
    raise AttributeError(f"cannot find an id on {type(obj).__name__}")


def _items(response) -> list:
    for attr in ("data", "evaluators", "tasks", "items", "ai_integrations"):
        value = getattr(response, attr, None)
        if isinstance(value, list):
            return value
    return list(response or [])


def template_config(evaluator, integration_id: str, model_name: str, region: str):
    from arize.evaluators.types import TemplateConfigInput

    return TemplateConfigInput.from_dict(
        {
            "name": evaluator.name,
            "template": evaluator.template,
            "include_explanations": True,
            "use_function_calling": True,
            "classification_choices": evaluator.choices,
            "direction": "MAXIMIZE",
            "data_granularity": evaluator.granularity,
            "llm_config": {
                "ai_integration_id": integration_id,
                "model_name": model_name,
                "invocation_parameters": {"temperature": 0},
                "provider_parameters": {
                    "region": region,
                    "bedrock_options": {"use_converse_endpoint": True},
                },
            },
        }
    )


def code_config(spec):
    from arize.evaluators.types import CustomCodeConfigRequest

    return CustomCodeConfigRequest.from_dict(
        {
            "type": "CUSTOM",
            "name": spec.name,
            "code": spec.code,
            "imports": AX_CODE_IMPORTS,
            "variables": list(spec.variables),
            "data_granularity": "SPAN",
        }
    )


def task_evaluator_input(evaluator_id: str, entry: dict, default_mappings: dict[str, str]):
    """Forma de span (`column_mappings`) o de traza/sesión (`query_mappings`)."""
    from arize.tasks.types import TaskEvaluatorInput

    if "query_mappings" in entry:
        return TaskEvaluatorInput.from_dict(
            {"evaluator_id": evaluator_id, "query_mappings": entry["query_mappings"]}
        )
    return TaskEvaluatorInput.from_dict(
        {
            "evaluator_id": evaluator_id,
            "column_mappings": entry.get("column_mappings") or default_mappings,
        }
    )


def ensure_integration(client, space: str) -> str:
    if os.environ.get("ARIZE_AI_INTEGRATION_ID"):
        return os.environ["ARIZE_AI_INTEGRATION_ID"]
    name = os.environ.get("ARIZE_AI_INTEGRATION_NAME", f"{PREFIX}-bedrock")
    for item in _items(client.ai_integrations.list(name=name, space=space)):
        if getattr(item, "name", None) == name:
            return _id(item)
    role_arn = os.environ.get("ARIZE_BEDROCK_ROLE_ARN")
    if not role_arn:
        raise SystemExit(
            "No hay integración de IA para el juez. Define ARIZE_AI_INTEGRATION_ID (una "
            "integración de Bedrock ya creada en AX) o ARIZE_BEDROCK_ROLE_ARN para crearla. "
            "Ver docs/runbook.md, sección 'Juez de las evals online'."
        )
    from arize._generated.api_client.models.aws_provider_metadata_request import (
        AwsProviderMetadataRequest,
    )

    created = client.ai_integrations.create(
        name=name,
        provider="AWS_BEDROCK",
        model_names=[os.environ.get("JUDGE_MODEL_ID", "eu.anthropic.claude-haiku-5-5")],
        provider_metadata=AwsProviderMetadataRequest(kind="AWS", role_arn=role_arn),
    )
    return _id(created)


def apply(dry_run: bool = False) -> list[str]:
    """Crea evaluadores y tareas que falten. Devuelve lo que ha hecho (o haría)."""
    specs = load_task_specs()
    space = os.environ.get("ARIZE_SPACE_ID", "<ARIZE_SPACE_ID>")
    project = os.environ.get("ARIZE_PROJECT_NAME", "agentic-lab")
    model = os.environ.get("JUDGE_MODEL_ID", "eu.anthropic.claude-haiku-5-5")
    region = os.environ.get("AWS_REGION", "eu-west-1")
    llm = {e.name: e for e in LLM_EVALUATORS}
    code = {e.name: e for e in CODE_EVALUATORS}
    actions: list[str] = []

    # Valida en local las definiciones (también en dry-run): tipos del SDK incluidos.
    for spec in specs:
        for entry in spec["evaluators"]:
            name = entry["name"]
            if name not in llm and name not in code:
                raise SystemExit(f"tarea {spec['name']}: evaluador desconocido {name!r}")
            if name in llm:
                template_config(llm[name], "dry-run", model, region)
                task_evaluator_input("dry-run", entry, llm[name].column_mappings)
            else:
                code_config(code[name])
                task_evaluator_input("dry-run", entry, code[name].column_mappings)

    if dry_run:
        for spec in specs:
            names = ", ".join(e["name"] for e in spec["evaluators"])
            actions.append(
                f"[dry-run] tarea {spec['name']} ({spec['task_type']}, "
                f"muestreo {spec.get('sampling_rate', 1.0)}) → {names}"
            )
        return actions

    client = ax_client()
    integration_id = ensure_integration(client, space)
    existing = {
        getattr(e, "name", ""): _id(e)
        for e in _items(client.evaluators.list(name=PREFIX, space=space, limit=100))
    }
    evaluator_ids: dict[str, str] = {}
    for spec in specs:
        for entry in spec["evaluators"]:
            name = entry["name"]
            ax_name = f"{PREFIX}-{name}"
            if name in evaluator_ids:
                continue
            if ax_name in existing:
                evaluator_ids[name] = existing[ax_name]
                actions.append(f"evaluador {ax_name}: ya existe")
            elif name in llm:
                created = client.evaluators.create_template_evaluator(
                    name=ax_name,
                    space=space,
                    commit_message=COMMIT_MESSAGE,
                    template_config=template_config(llm[name], integration_id, model, region),
                    description=llm[name].description,
                )
                evaluator_ids[name] = _id(created)
                actions.append(f"evaluador {ax_name}: creado (LLM, {llm[name].granularity})")
            else:
                created = client.evaluators.create_code_evaluator(
                    name=ax_name,
                    space=space,
                    commit_message=COMMIT_MESSAGE,
                    code_config=code_config(code[name]),
                    description=code[name].description,
                )
                evaluator_ids[name] = _id(created)
                actions.append(f"evaluador {ax_name}: creado (código)")

    tasks = {
        getattr(t, "name", ""): t
        for t in _items(client.tasks.list(project=project, space=space, limit=100))
    }
    for spec in specs:
        if spec["name"] in tasks:
            actions.append(f"tarea {spec['name']}: ya existe")
            continue
        inputs = [
            task_evaluator_input(
                evaluator_ids[e["name"]],
                e,
                (llm.get(e["name"]) or code[e["name"]]).column_mappings,
            )
            for e in spec["evaluators"]
        ]
        kwargs: dict = {
            "name": spec["name"],
            "task_type": spec["task_type"],
            "evaluators": inputs,
            "project": project,
            "space": space,
            "sampling_rate": spec.get("sampling_rate", 1.0),
            "is_continuous": spec.get("is_continuous", True),
        }
        if "query_filters" in spec:
            from arize.tasks.types import TaskQueryFilters

            kwargs["query_filters"] = TaskQueryFilters.from_dict(spec["query_filters"])
        elif spec.get("query_filter"):
            kwargs["query_filter"] = spec["query_filter"]
        client.tasks.create_evaluation_task(**kwargs)
        actions.append(f"tarea {spec['name']}: creada")
    return actions


def destroy() -> list[str]:
    """Borra las tareas y evaluadores del lab en AX (`task destroy`)."""
    client = ax_client()
    space = os.environ["ARIZE_SPACE_ID"]
    project = os.environ.get("ARIZE_PROJECT_NAME", "agentic-lab")
    actions: list[str] = []
    names = {s["name"] for s in load_task_specs()}
    try:
        for t in _items(client.tasks.list(project=project, space=space, limit=100)):
            if getattr(t, "name", "") in names:
                client.tasks.delete(task=_id(t), space=space)
                actions.append(f"tarea {t.name}: borrada")
    except Exception as exc:  # noqa: BLE001 - el proyecto puede no existir ya
        actions.append(f"tareas: no se pudieron listar ({exc})")
    for e in _items(client.evaluators.list(name=PREFIX, space=space, limit=100)):
        if getattr(e, "name", "").startswith(f"{PREFIX}-"):
            client.evaluators.delete(evaluator=_id(e), space=space)
            actions.append(f"evaluador {e.name}: borrado")
    return actions
