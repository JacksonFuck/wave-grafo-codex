"""Commands and reservation helpers for grafo.py Codex mode."""
from pathlib import Path

from grafo_core import (OCCUPYING, dependents_count, fail, find_cycle, norm_id, overlap)

REVIEW_QUEUE = {"EM_AVALIACAO", "APROVADA", "PR_ABERTO"}


def resolve_graph_paths(graph, graph_path):
    """Normalize persisted plan/log paths against the graph directory."""
    parent = Path(graph_path).resolve().parent
    for data in graph.get("slices", {}).values():
        for key in ("plan", "red_log", "green_log"):
            value = data.get(key)
            if value:
                path = Path(value).expanduser()
                data[key] = str((path if path.is_absolute() else parent / path).resolve())
    return graph


def resolve_set_value(key, value, graph_path):
    if key in {"plan", "red_log", "green_log"} and value:
        path = Path(value).expanduser()
        return str((path if path.is_absolute() else Path(graph_path).parent / path).resolve())
    return value


def cmd_codex_enable(a, graph):
    if graph["config"].get("codex_mode"):
        return graph, "OK: validação Codex já estava ativa"
    graph["config"]["codex_mode"] = True
    return graph, "OK: validação Codex ativa; estados e histórico preservados"


def _owner_token(value):
    token = (value or "").strip()
    if not token or len(token) > 128 or any(ch.isspace() for ch in token):
        fail("--owner exige um TOKEN não vazio, sem espaços, de até 128 caracteres")
    return token


def _claimed_ready(graph):
    return {sid: sl for sid, sl in graph["slices"].items()
            if sl["state"] == "PRONTA" and sl.get("owner")}


def _working(graph):
    return sorted(sid for sid, sl in graph["slices"].items()
                  if sl["state"] in OCCUPYING and sl["state"] not in REVIEW_QUEUE)


def _files_locked(graph):
    result = [f for sl in graph["slices"].values()
              if sl["state"] in OCCUPYING for f in sl["files"]]
    result.extend(f for sl in _claimed_ready(graph).values() for f in sl["files"])
    return result


def cmd_claim(a, graph):
    sid = norm_id(a.id)
    data = graph["slices"].get(sid) or fail(f"fatia '{sid}' não existe")
    owner = _owner_token(a.owner)
    if data.get("owner") == owner:
        return graph, f"OK: {sid} já reservada por {owner}"
    if data.get("owner"):
        fail(f"fatia {sid} já reservada por {data['owner']}")
    if data["state"] != "PRONTA":
        fail(f"claim só vale para PRONTA (atual: {data['state']})")
    if not data["files"]:
        fail("claim exige arquivos estimados")
    missing = [dep for dep in data["deps"] if dep not in graph["slices"]]
    pending = [dep for dep in data["deps"] if dep in graph["slices"] and
               graph["slices"][dep]["state"] != "MERGEADA"]
    if missing or pending:
        reason = f"dependências ausentes: {', '.join(missing)}" if missing else \
            f"dependências pendentes: {', '.join(pending)}"
        fail(f"claim revalidou a fatia {sid}: {reason}")
    claimed = _claimed_ready(graph)
    used = len(_working(graph)) + len(claimed)
    if used >= graph["config"]["max_parallel"]:
        fail(f"sem vaga: {used} worker(s)/reserva(s) ocupam max-parallel")
    hit = overlap(data["files"], _files_locked(graph))
    if hit:
        fail(f"claim revalidou sobreposição: {', '.join(sorted(hit))}")
    data["owner"], data["activity"] = owner, "claimed"
    return graph, f"OK: {sid} reservada por {owner}"


def cmd_release(a, graph):
    sid = norm_id(a.id)
    data = graph["slices"].get(sid) or fail(f"fatia '{sid}' não existe")
    owner = _owner_token(a.owner)
    if data.get("owner") != owner:
        fail(f"release exige owner atual da fatia {sid}")
    data["owner"], data["activity"] = None, ""
    return graph, f"OK: reserva de {sid} liberada por {owner}"


def cmd_show(a, graph):
    sid = norm_id(a.id)
    data = graph["slices"].get(sid) or fail(f"fatia '{sid}' não existe")
    fields = ("title", "state", "deps", "files", "priority", "critical", "owner", "activity",
              "role", "model", "thinking", "plan", "worktree", "branch", "cycle_no", "cycles",
              "cycles_total", "pr", "block_reason")
    lines = [f"FATIA {sid}"]
    for key in fields:
        value = data.get(key)
        if value not in (None, "", [], False):
            lines.append(f"{key}: {', '.join(value) if isinstance(value, list) else value}")
    return None, "\n".join(lines)


def cmd_summary(a, graph):
    open_slices = {sid: data for sid, data in graph["slices"].items() if data["state"] != "MERGEADA"}
    counts = {}
    for data in open_slices.values():
        counts[data["state"]] = counts.get(data["state"], 0) + 1
    state_text = ", ".join(f"{key}={counts[key]}" for key in sorted(counts)) or "nenhuma"
    mode = "codex" if graph.get("config", {}).get("codex_mode") else "legado"
    claims = _claimed_ready(graph)
    owners = ", ".join(f"{sid}@{sl['owner']}" for sid, sl in sorted(claims.items())) or "nenhuma"
    open_ids = sorted(open_slices)
    shown = ", ".join(open_ids[:12]) or "nenhuma"
    if len(open_ids) > 12:
        shown += f" +{len(open_ids) - 12}"
    return None, (f"WAVE {graph['wave']} · {mode} · abertas {len(open_slices)} [{shown}] · "
                  f"estados {state_text} · reservadas {owners}")


def classify_ready(graph, sid):
    sl, data = graph["slices"], graph["slices"][sid]
    missing = [dep for dep in data["deps"] if dep not in sl]
    pending = [dep for dep in data["deps"] if dep in sl and sl[dep]["state"] != "MERGEADA"]
    if missing:
        return (f"dependência fora do grafo: {', '.join(missing)} "
                "(consulte o tracker; concluída → add --id <n> --state MERGEADA)")
    if pending:
        return f"aguarda: {', '.join(pending)}"
    if not data["files"]:
        return "arquivos não estimados (Explore → grafo.py add --files)"
    return None


def cmd_frontier(a, graph):
    cycle = find_cycle(graph)
    if cycle:
        fail(f"ciclo de dependência entre: {', '.join(cycle)}")
    slices, config = graph["slices"], graph["config"]
    busy = sorted(sid for sid, data in slices.items() if data["state"] in OCCUPYING)
    working = [sid for sid in busy if slices[sid]["state"] not in REVIEW_QUEUE]
    claimed = {sid: data for sid, data in slices.items()
               if data["state"] == "PRONTA" and data.get("owner")}
    locked_files = [f for sid in busy for f in slices[sid]["files"]]
    locked_files.extend(f for data in claimed.values() for f in data["files"])
    slots = max(config["max_parallel"] - len(working) - len(claimed), 0)
    ready = [sid for sid, data in slices.items() if data["state"] == "PRONTA"]
    skipped = [(sid, f"reservada por {slices[sid]['owner']}") for sid in ready if slices[sid].get("owner")]
    skipped += [(sid, why) for sid in ready if not slices[sid].get("owner") and
                (why := classify_ready(graph, sid))]
    candidates = sorted((sid for sid in ready if not slices[sid].get("owner") and
                         not classify_ready(graph, sid)),
                        key=lambda sid: (slices[sid]["priority"],
                                         -dependents_count(graph, sid), sid))
    chosen = []
    for sid in candidates:
        hit = overlap(slices[sid]["files"], locked_files)
        if len(chosen) >= slots:
            skipped.append((sid, "sem vaga"))
        elif hit:
            skipped.append((sid, f"conflito de arquivos: {', '.join(sorted(hit))}"))
        else:
            chosen.append(sid)
            locked_files += slices[sid]["files"]
    reserved = [f"{sid}@{data['owner']}" for sid, data in sorted(claimed.items())]
    review = [sid for sid in busy if sid not in working]
    out = [f"VAGAS: {slots} (trabalhando: {', '.join(working) or 'nenhuma'}; "
           f"reservadas: {', '.join(reserved) or 'nenhuma'}; na fila de merge: "
           f"{', '.join(review) or 'nenhuma'})"]
    out += [f"ESCOLHIDA: {sid} · prio {slices[sid]['priority']} · "
            f"desbloqueia {dependents_count(graph, sid)} · "
            f"{'CRÍTICA' if slices[sid]['critical'] else 'normal'} · {slices[sid]['title']}"
            for sid in chosen]
    out += [f"FORA: {sid} — {why}" for sid, why in skipped]
    if not busy and not ready:
        out.append("FIM: nenhuma fatia pronta nem em andamento")
    elif not busy and not chosen:
        out.append("SEM_ELEGIVEL: há fatias prontas, nenhuma elegível — resolva os motivos FORA")
    return None, "\n".join(out)
