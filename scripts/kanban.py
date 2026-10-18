#!/usr/bin/env python3
"""Leitura e renderização compartilhadas do Kanban de um graph.json escolhido."""
import argparse
import html
import json
import os
import re
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit


COLUMNS = [
    {"id": "queue", "name": "Fila", "color": "wait", "activity": None},
    {"id": "research", "name": "Pesquisa", "color": "wait", "activity": "research"},
    {"id": "planning", "name": "Planejamento", "color": "wait", "activity": "planning"},
    {"id": "plan_review", "name": "Revisão do plano", "color": "rev", "states": ["PLANEJADA"]},
    {"id": "plan_ready", "name": "Plano pronto", "color": "rev", "activity": "plan_ready", "states": ["PLANO_APROVADO"]},
    {"id": "implementation", "name": "Implementação", "color": "run", "states": ["EM_IMPLEMENTACAO"]},
    {"id": "review", "name": "Revisão / correção", "color": "rev", "states": ["EM_AVALIACAO", "EM_CORRECAO"]},
    {"id": "gate", "name": "Gate", "color": "gate", "states": ["APROVADA", "PR_ABERTO", "CONFLITO"]},
    {"id": "done", "name": "Feito", "color": "ok", "states": ["MERGEADA"]},
    {"id": "blocked", "name": "Bloqueado / pausado", "color": "bad", "states": ["BLOQUEADA"]},
    {"id": "unknown", "name": "Status desconhecido", "color": "unknown", "states": []},
]
STATE_COLUMN = {state: col["id"] for col in COLUMNS for state in col.get("states", [])}


def display(value):
    if value is None:
        return ""
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False, sort_keys=True)
    return str(value)


def _items(value):
    if value is None:
        return []
    return value if isinstance(value, list) else [value]


def _number(value, default=0.0):
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _column_for(slice_data):
    state = display(slice_data.get("state", ""))
    title = display(slice_data.get("title", ""))
    if "PAUSADA" in title.upper():
        return "blocked"
    if state == "PRONTA":
        activity = display(slice_data.get("activity", "")).strip().lower()
        return {"research": "research", "planning": "planning",
                "plan_ready": "plan_ready"}.get(activity, "queue")
    return STATE_COLUMN.get(state, "unknown")


def safe_github_repo(config):
    """Retorna URL HTTPS de um repo GitHub configurado, sem aceitar hosts/rotas extras."""
    if not isinstance(config, dict):
        return None
    raw = config.get("tracker_url") or config.get("tracker")
    if not isinstance(raw, str):
        return None
    try:
        parsed = urlsplit(raw.strip())
        parts = [part for part in parsed.path.strip("/").split("/") if part]
        if (parsed.scheme != "https" or parsed.hostname != "github.com" or
                parsed.username or parsed.password or parsed.port is not None or
                parsed.query or parsed.fragment or len(parts) != 2):
            return None
        if any(not re.fullmatch(r"[A-Za-z0-9_.-]+", part) or part in {".", ".."}
               for part in parts):
            return None
        repo = parts[1][:-4] if parts[1].endswith(".git") else parts[1]
        if not repo:
            return None
        return f"https://github.com/{parts[0]}/{repo}"
    except ValueError:
        return None


def _valid_pr(value):
    if isinstance(value, bool):
        return None
    if isinstance(value, int) and value > 0:
        return value
    if isinstance(value, str) and re.fullmatch(r"[0-9]+", value.strip()):
        number = int(value.strip())
        return number if number > 0 else None
    return None


def _card(sid, source, column_id, repo_url):
    history = source.get("history")
    latest = history[-1] if isinstance(history, list) and history and isinstance(history[-1], dict) else {}
    state = display(source.get("state", "")) or "(sem estado)"
    event_from, event_to = display(latest.get("from")), display(latest.get("to"))
    event = f"{event_from} → {event_to}" if event_from or event_to else display(
        latest.get("event") or latest.get("action") or state)
    pr_number = _valid_pr(source.get("pr"))
    return {
        "id": display(sid), "title": display(source.get("title", "")),
        "state": state, "activity": display(source.get("activity", "")),
        "agent": display(source.get("agent_id") or source.get("agent", "")),
        "role": display(source.get("role") or source.get("agent_role", "")),
        "model": display(source.get("model", "")),
        "reasoning_effort": display(source.get("thinking") or source.get("reasoning_effort", "")),
        "deps": [display(item) for item in _items(source.get("deps"))],
        "note": display(latest.get("note") or source.get("block_reason", "")),
        "last_event": event, "last_time_utc": display(latest.get("t") or source.get("entered", "")),
        "pr_number": pr_number,
        "pr_url": f"{repo_url}/pull/{pr_number}" if repo_url and pr_number else None,
        "pr_invalid": source.get("pr") is not None and pr_number is None,
        "score": display((source.get("scores") or [])[-1]) if isinstance(source.get("scores"), list) and source.get("scores") else "",
        "priority": _number(source.get("priority"), 9),
        "entered_ts": _number(source.get("entered_ts"), 0),
        "column_id": column_id,
    }


def build_board(graph, source_path, generated_at_utc=None):
    """Cria a visão única consumida pelo painel HTTP e pelo Markdown."""
    slices = graph.get("slices") if isinstance(graph, dict) else None
    if not isinstance(slices, dict):
        raise ValueError("o grafo precisa conter um objeto 'slices'")
    repo_url = safe_github_repo(graph.get("config"))
    columns = [{key: value for key, value in col.items() if key not in {"states", "activity"}}
               for col in COLUMNS]
    for column in columns:
        column["cards"] = []
    by_id = {col["id"]: col for col in columns}
    for sid, item in slices.items():
        if not isinstance(item, dict):
            item = {"state": "(registro inválido)"}
        column_id = _column_for(item)
        by_id[column_id]["cards"].append(_card(sid, item, column_id, repo_url))
    for col in columns:
        if col["id"] in {"queue", "research", "planning", "plan_ready"}:
            col["cards"].sort(key=lambda card: (card["priority"], card["id"]))
        else:
            col["cards"].sort(key=lambda card: (-card["entered_ts"], card["id"]))
    return {
        "source": str(Path(source_path).resolve()),
        "generated_at_utc": generated_at_utc or datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z"),
        "columns": columns,
    }


def read_graph(path):
    graph_path = Path(path)
    if not graph_path.is_absolute():
        raise ValueError("--graph precisa ser um caminho absoluto")
    with graph_path.open(encoding="utf-8") as stream:
        graph = json.load(stream)
    if not isinstance(graph, dict) or not isinstance(graph.get("slices"), dict):
        raise ValueError("o grafo precisa conter um objeto 'slices'")
    return graph


def _md(value):
    text = " ".join(display(value).split())
    text = html.escape(text, quote=False)
    for character in "\\`*_{}[]()!#|":
        text = text.replace(character, "\\" + character)
    return text


def render_markdown(board):
    lines = ["# Kanban", "", f"Fonte: {_md(board['source'])} · gerado em {_md(board['generated_at_utc'])} UTC", ""]
    for column in board["columns"]:
        cards = column["cards"]
        lines.extend([f"## {column['name']} ({len(cards)})", ""])
        for card in cards:
            lines.append(f"- **#{_md(card['id'])}** [{_md(card['state'])}] {_md(card['title'])}")
            details = [f"agente: {card['agent'] or '—'}", f"role: {card['role'] or '—'}",
                       f"modelo: {card['model'] or '—'}", f"reasoning_effort: {card['reasoning_effort'] or '—'}"]
            if card["deps"]:
                details.append("deps: " + ", ".join(card["deps"]))
            if card["score"]:
                details.append("nota: " + card["score"])
            pr_link = None
            if card["pr_number"]:
                if card["pr_url"]:
                    pr_link = f"[PR #{card['pr_number']}]({card['pr_url']})"
                else:
                    details.append(f"PR #{card['pr_number']}")
            elif card["pr_invalid"]:
                details.append("PR inválido")
            details.append(f"último evento: {card['last_event'] or '—'} · {card['last_time_utc'] or 'horário não informado'} UTC")
            if card["note"]:
                details.append("nota: " + card["note"])
            suffix = f"; {pr_link}" if pr_link else ""
            lines.append("  - " + "; ".join(_md(detail) for detail in details) + suffix)
        lines.append("")
    return "\n".join(lines)


def write_markdown(output_path, contents, graph_path):
    output = Path(output_path)
    graph = Path(graph_path).resolve()
    if not output.is_absolute():
        raise ValueError("--output precisa ser um caminho absoluto")
    if output.resolve() == graph:
        raise ValueError("--output não pode substituir o grafo escolhido")
    output.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=f".{output.name}.", suffix=".tmp", dir=output.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            stream.write(contents)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, output)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--graph", required=True, help="caminho absoluto do graph.json de origem")
    parser.add_argument("--output", required=True, help="caminho absoluto do kanban.md")
    args = parser.parse_args(argv)
    try:
        graph = read_graph(args.graph)
        board = build_board(graph, args.graph)
        write_markdown(args.output, render_markdown(board), args.graph)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(f"Erro ao gerar Kanban: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
