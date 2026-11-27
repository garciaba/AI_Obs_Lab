"""CLI de las evals del lab.

wonder-evals dataset push [--recreate]         crea el dataset de regresión en AX
wonder-evals experiment run --name N [...]     lanza un experimento (AX o --local)
wonder-evals compare A B                       compara dos experimentos locales
wonder-evals online apply [--dry-run]          crea evaluadores y tareas online en AX
wonder-evals online destroy                    borra evaluadores y tareas del lab en AX
wonder-evals gate3 [--local]                   puerta de la fase 3 (cambio de prompt)
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time

from wonder_evals import agent_client, experiment, online
from wonder_evals.dataset import DATASET_NAME, dataset_examples


def _require(*names: str) -> None:
    missing = [n for n in names if not os.environ.get(n)]
    if missing:
        sys.exit(f"Faltan variables de entorno: {', '.join(missing)} (ver .env.example)")


def _overrides(pairs: list[str]) -> dict:
    return dict(p.split("=", 1) for p in pairs or [])


def _print_summary(report: dict) -> None:
    print(f"\n{report['name']}  (juez: {report['judge']}, {len(report['rows'])} filas)")
    for name, s in report["summary"].items():
        mean = "-" if s["mean"] is None else f"{s['mean']:.3f}"
        print(f"  {name:28} {mean:>6}   {s['labels']}")


def _run(name: str, args, overrides: dict) -> dict:
    metadata = {k: str(v) for k, v in overrides.items()}
    with agent_client.overrides(overrides):
        state = agent_client.health()
        metadata.setdefault("supervisor_model", state["supervisor_model"])
        metadata.setdefault("prompt_version", state.get("prompt_version", ""))
        if args.local:
            rows = dataset_examples(include_scenarios=not args.reference_only)
            report = experiment.run_local(
                name,
                rows=rows[: args.limit] if args.limit else rows,
                metadata=metadata,
                concurrency=args.concurrency,
            )
            _print_summary(report)
            return report
        _require("ARIZE_API_KEY", "ARIZE_SPACE_ID")
        exp, frame = experiment.run_ax(
            name, metadata=metadata, concurrency=args.concurrency, dry_run=args.dry_run
        )
        # Resumen a partir de las columnas de puntuación que devuelve el SDK.
        summary = {}
        for column in frame.columns:
            if str(column).endswith(".score") or str(column).endswith("_score"):
                evaluator = (
                    str(column).removeprefix("eval.").rsplit(".", 1)[0].removesuffix("_score")
                )
                values = [
                    v for v in frame[column].tolist() if isinstance(v, (int, float)) and v == v
                ]
                mean = round(sum(values) / len(values), 3) if values else None
                summary[evaluator] = {"mean": mean, "n": len(values), "labels": {}}
        report = {
            "name": name,
            "judge": "Vertex AI (JUDGE_MODEL_ID)",
            "rows": [None] * len(frame),
            "summary": summary,
        }
        print(
            f"Experimento {name!r} en Arize AX sobre el dataset {DATASET_NAME!r}"
            f"{' (dry-run, no subido)' if exp is None else ''}"
        )
        _print_summary(report)
        return report


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="wonder-evals",
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    sub = parser.add_subparsers(dest="command", required=True)

    ds = sub.add_parser("dataset").add_subparsers(dest="action", required=True)
    push = ds.add_parser("push")
    push.add_argument("--recreate", action="store_true")
    ds.add_parser("show")

    ex = sub.add_parser("experiment").add_subparsers(dest="action", required=True)
    run = ex.add_parser("run")
    gate = sub.add_parser("gate3")
    for p in (run, gate):
        p.add_argument("--local", action="store_true", help="sin Arize: resultados en results/")
        p.add_argument("--dry-run", action="store_true", help="AX: ejecuta sin subir resultados")
        p.add_argument("--concurrency", type=int, default=3)
        p.add_argument("--limit", type=int, default=0, help="solo las N primeras filas (--local)")
        p.add_argument(
            "--reference-only",
            action="store_true",
            help="solo las 25 peticiones de referencia (--local)",
        )
    run.add_argument("--name", default=None)
    run.add_argument(
        "--override",
        action="append",
        default=[],
        metavar="CLAVE=VALOR",
        help="ajuste en caliente del agente: supervisor_model=..., prompt_version=...",
    )

    cmp_ = sub.add_parser("compare")
    cmp_.add_argument("baseline")
    cmp_.add_argument("candidate")

    on = sub.add_parser("online").add_subparsers(dest="action", required=True)
    on.add_parser("apply").add_argument("--dry-run", action="store_true")
    on.add_parser("destroy")

    args = parser.parse_args()
    stamp = time.strftime("%Y%m%d-%H%M%S")

    if args.command == "dataset":
        if args.action == "show":
            print(json.dumps(dataset_examples(), indent=2, ensure_ascii=False))
            return
        _require("ARIZE_API_KEY", "ARIZE_SPACE_ID")
        dataset = experiment.ensure_dataset(
            experiment.ax_client(), os.environ["ARIZE_SPACE_ID"], recreate=args.recreate
        )
        print(
            f"Dataset {DATASET_NAME!r} listo en AX ({len(dataset_examples())} ejemplos): "
            f"{getattr(dataset, 'id', '')}"
        )
    elif args.command == "experiment":
        overrides = _overrides(args.override)
        label = "-".join(str(v).split(".")[-1] for v in overrides.values()) or "baseline"
        _run(args.name or f"regression-{label}-{stamp}", args, overrides)
    elif args.command == "gate3":
        # Un cambio de prompt debe mostrar su diferencia en un experimento.
        base = _run(f"gate3-prompt-v1-{stamp}", args, {"prompt_version": "v1"})
        cand = _run(f"gate3-prompt-v2-plain-{stamp}", args, {"prompt_version": "v2-plain"})
        print("\n" + experiment.format_comparison(base, cand))
        diffs = [r for r in experiment.compare(base, cand) if r["delta"]]
        if not args.local:
            print(
                "\nEn Arize AX: dataset "
                f"{DATASET_NAME!r} → Experiments → compara los dos gate3-prompt-*."
            )
        if not diffs:
            sys.exit("PUERTA 3 NO SUPERADA: el cambio de prompt no movió ningún evaluador")
        print(f"\nPUERTA 3 OK: {len(diffs)} evaluador(es) cambian con el prompt")
    elif args.command == "compare":

        def load(name: str) -> dict:
            path = name if name.endswith(".json") else experiment.RESULTS_DIR / f"{name}.json"
            return json.loads(open(path).read())

        print(experiment.format_comparison(load(args.baseline), load(args.candidate)))
    elif args.command == "online":
        if args.action == "apply":
            if not args.dry_run:
                _require("ARIZE_API_KEY", "ARIZE_SPACE_ID")
            for line in online.apply(dry_run=args.dry_run):
                print(line)
        else:
            _require("ARIZE_API_KEY", "ARIZE_SPACE_ID")
            for line in online.destroy():
                print(line)


if __name__ == "__main__":
    main()
