"""Cliente de línea de comandos del agente.

wonder-chat "Show me some dinosaur toys"        una pregunta
wonder-chat                                     conversación interactiva (misma sesión)
wonder-chat --json "Where's my order?"          respuesta completa en JSON
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import uuid

import httpx


def ask(client: httpx.Client, message: str, session_id: str, user_id: str) -> dict:
    response = client.post(
        "/chat", json={"message": message, "session_id": session_id, "user_id": user_id}
    )
    response.raise_for_status()
    return response.json()


def _print(data: dict, as_json: bool) -> None:
    if as_json:
        print(json.dumps(data, indent=2, ensure_ascii=False))
        return
    print(f"\n{data['answer']}\n")
    tools = ", ".join(f"{c['agent']}:{c['tool']}" for c in data["tool_calls"]) or "none"
    print(
        f"[trace_id={data['trace_id']} session={data['session_id']} tools={tools} "
        f"model={data['supervisor_model']}]",
        file=sys.stderr,
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="Wonder Toys agent CLI")
    parser.add_argument("message", nargs="?", help="mensaje; sin él, modo interactivo")
    parser.add_argument("--url", default=os.environ.get("AGENT_URL", "http://localhost:8080"))
    parser.add_argument("--session", default=None, help="id de sesión (por defecto, uno nuevo)")
    parser.add_argument("--user", default=os.environ.get("WONDER_USER", "cli-user"))
    parser.add_argument("--json", action="store_true", help="imprime la respuesta JSON completa")
    args = parser.parse_args()

    session_id = args.session or uuid.uuid4().hex
    with httpx.Client(base_url=args.url, timeout=300) as client:
        if args.message:
            _print(ask(client, args.message, session_id, args.user), args.json)
            return
        print(f"Wonder Toys (session {session_id}). Ctrl-D o 'exit' para salir.")
        while True:
            try:
                line = input("> ").strip()
            except EOFError:
                break
            if line in {"exit", "quit"}:
                break
            if line:
                _print(ask(client, line, session_id, args.user), args.json)


if __name__ == "__main__":
    main()
