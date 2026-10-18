#!/usr/bin/env python3
"""Motor determinístico do /wave-grafo: DAG de fatias + máquina de estados em tasks/graph.json.
Transição ilegal = erro (exit 1). Chamadas concorrentes são serializadas por lock. Uso: grafo.py <cmd> --help"""
import argparse
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from grafo_core import (EDGES, NO_BLOCK_FROM, OCCUPYING, RESET_ON_UNBLOCK, SETTABLE,  # noqa: E402
                        cell, fail, files, find_cycle, ids, load, locked,
                        mtime_after, norm_id, now, overlap, save)
from codex_guards import fail_transition  # noqa: E402
from grafo_codex import (cmd_claim, cmd_codex_enable, cmd_frontier, cmd_release, cmd_show,
                         cmd_summary, resolve_graph_paths, resolve_set_value)  # noqa: E402


def new_slice(state="PRONTA"):
    return {"title": "", "deps": [], "files": [], "priority": 3, "critical": False,
            "state": state, "entered": now(), "entered_ts": time.time(),
            "cycles": 0, "cycles_total": 0, "scores": [],
            "test_exit": None, "pr": None, "block_reason": None, "history": [],
            "origin": None, "review_seen": None, "owner": None, "activity": "",
            "thinking": "", "role": "", "cycle_no": 0, "cycle_entered_ts": None,
            "approval_fingerprint": None, "approval_base_ref": None,
            "approval_test_argv": None}


def cmd_init(a, g):
    if a.path.exists() and not a.force:
        fail(f"{a.path} já existe; o tick deve retomar, não recriar")
    if not 1 <= a.max_parallel <= 3:
        fail("--max-parallel deve estar entre 1 e 3")
    if a.max_cycles < 1 or not 1 <= a.score_min <= 100:
        fail("--max-cycles ≥ 1 e --score-min entre 1 e 100")
    return {"version": 2, "wave": 0, "slices": {}, "config": {
        "test_cmd": a.test_cmd, "tracker": a.tracker, "ready_label": a.ready_label,
        "base_branch": a.base_branch, "max_parallel": a.max_parallel,
        "score_min": a.score_min, "max_cycles": a.max_cycles,
        "codex_mode": a.codex}}, f"OK: {a.path} criado"


def cmd_add(a, g):
    sid = norm_id(a.id)
    s = g["slices"].get(sid)
    if s is None:
        s = g["slices"][sid] = new_slice(a.state or "PRONTA")
        msg = f"OK: fatia {sid} criada ({s['state']})"
    elif a.state:
        fail("--state só vale na criação da fatia")
    else:
        msg = f"OK: fatia {sid} atualizada (estado {s['state']} mantido)"
    if a.title:
        s["title"] = a.title
    if a.deps is not None:
        proposed_deps = ids(a.deps)
        if s.get("owner") and proposed_deps != s["deps"]:
            fail(f"fatia {sid} está reservada por {s['owner']}; libere antes de mudar dependências")
        s["deps"] = proposed_deps
    if a.files is not None:
        proposed_files = files(a.files)
        if s.get("owner") and proposed_files != s["files"]:
            fail(f"fatia {sid} está reservada por {s['owner']}; libere antes de mudar arquivos")
        s["files"] = proposed_files
        if s["state"] in OCCUPYING:
            others = [f for o, d in g["slices"].items()
                      if o != sid and d["state"] in OCCUPYING for f in d["files"]]
            others += [f for o, d in g["slices"].items()
                       if o != sid and d["state"] == "PRONTA" and d.get("owner") for f in d["files"]]
            hit = overlap(s["files"], others)
            if hit:
                fail(f"arquivos colidem com fatia ocupada: {', '.join(sorted(hit))} — replaneje ou espere")
    if a.priority is not None:
        s["priority"] = a.priority
    if a.critical is not None:
        s["critical"] = a.critical == "sim"
    if sid in s["deps"]:
        fail("fatia não pode depender de si mesma")
    cyc = find_cycle(g)
    if cyc:
        fail(f"ciclo de dependência entre: {', '.join(cyc)} — nada foi gravado")
    return g, msg


def cmd_retire(a, g):
    sid = norm_id(a.id)
    s = g["slices"].get(sid) or fail(f"fatia '{sid}' não existe")
    if s["state"] != "PRONTA":
        fail(f"retire só vale para PRONTA (atual: {s['state']}); BLOQUEADA espera decisão humana")
    del g["slices"][sid]
    return g, f"OK: fatia {sid} retirada do grafo — {a.note}"


def cmd_set(a, g):
    s = g["slices"].get(norm_id(a.id)) or fail(f"fatia '{a.id}' não existe")
    for kv in a.pairs:
        k, _, v = kv.partition("=")
        if k not in SETTABLE:
            fail(f"campo '{k}' não editável; permitidos: {', '.join(sorted(SETTABLE))}")
        s[k] = resolve_set_value(k, v, a.path)
    return g, f"OK: {a.id} ← {', '.join(a.pairs)}"


def check_guards(cfg, s, to, a):
    frm = s["state"]
    need_note = to in ("BLOQUEADA", "PLANO_APROVADO") or frm == "BLOQUEADA" or \
        (to == "EM_CORRECAO" and frm != "EM_AVALIACAO")
    if need_note and not a.note:
        fail(f"{frm} → {to} exige --note com o motivo")
    if to == "PLANEJADA" and not s.get("plan"):
        fail("PLANEJADA exige plano: grafo.py set <id> plan=<caminho>")
    if to == "PLANEJADA" and cfg.get("codex_mode") and not s.get("owner"):
        fail("PLANEJADA em modo Codex exige claim prévio: grafo.py claim <id> --owner TOKEN")
    if to == "EM_IMPLEMENTACAO":
        if not (s.get("worktree") and s.get("branch")):
            fail("EM_IMPLEMENTACAO exige worktree e branch (grafo.py set)")
        if a.test_exit != 0:
            fail("EM_IMPLEMENTACAO exige --test-exit 0: linha de base do TEST_CMD no worktree")
    if to == "EM_AVALIACAO" and not cfg.get("codex_mode"):
        if a.sem_red and s.get("origin") != "CONFLITO":
            fail("--sem-red só vale para correção de conflito com a base")
        for k in ("green_log",) if a.sem_red else ("red_log", "green_log"):
            if not s.get(k) or not mtime_after(s[k], s.get("entered_ts")):
                fail(f"EM_AVALIACAO exige {k} escrito neste ciclo (evidência TDD nova)")
    if frm == "EM_AVALIACAO" and to != "BLOQUEADA":
        if a.score is None:
            fail("saída de EM_AVALIACAO exige --score")
        passed = a.score >= cfg["score_min"] and not a.veto
        if to == "APROVADA" and not passed:
            fail(f"APROVADA exige nota ≥ {cfg['score_min']} e nenhum veto")
        if to == "EM_CORRECAO" and passed:
            fail("EM_CORRECAO exige nota abaixo do mínimo ou --veto")
    if to == "EM_CORRECAO":
        used = s["cycles"] + (1 if frm == "EM_AVALIACAO" else 0)
        if used >= cfg["max_cycles"]:
            fail(f"limite de {cfg['max_cycles']} avaliações nesta rodada: use BLOQUEADA --note")
    if to == "PR_ABERTO":
        if a.test_exit != 0:
            fail("PR_ABERTO exige --test-exit 0 (TEST_CMD no worktree)")
        if not (a.pr or s["pr"]):
            fail("PR_ABERTO exige --pr <número>")


def cmd_move(a, g):
    sid = norm_id(a.id)
    s = g["slices"].get(sid) or fail(f"fatia '{sid}' não existe")
    frm, to = s["state"], a.to
    if to not in EDGES:
        fail(f"estado desconhecido: {to}")
    legal = EDGES[frm] | (set() if frm in NO_BLOCK_FROM else {"BLOQUEADA"})
    if to not in legal:
        fail(f"transição ilegal {frm} → {to}. Legais: {', '.join(sorted(legal)) or 'nenhuma'}")
    check_guards(g["config"], s, to, a)
    approval = fail_transition(g, sid, to, a.path)
    if approval:
        s.update(approval)
    if frm == "EM_AVALIACAO" and a.score is not None:
        s["cycles"] += 1
        s["cycles_total"] += 1
        s["scores"].append(a.score)
    if frm == "BLOQUEADA":
        s.update({k: None for k in RESET_ON_UNBLOCK}, cycles=0, scores=[])
    s.update({k: v for k, v in (("test_exit", a.test_exit), ("pr", a.pr)) if v is not None})
    if a.sem_red:
        a.note = f"{a.note or ''} [sem RED: {a.sem_red}]".strip()
    if to == "PR_ABERTO":
        s["cycles"], s["scores"] = 0, []  # nova rodada para pedido humano ou conflito
    if to == "EM_CORRECAO":
        s["origin"] = frm
        s.update({k: None for k in ("approval_fingerprint", "approval_base_ref",
                                    "approval_test_argv")})
    s["block_reason"] = a.note if to == "BLOQUEADA" else None
    s["state"], s["entered"], s["entered_ts"] = to, now(), time.time()
    if to == "PLANEJADA" or to == "EM_CORRECAO":
        s["cycle_no"] = int(s.get("cycle_no") or 0) + 1
        s["cycle_entered_ts"] = s["entered"]
    if to == "MERGEADA":
        s["owner"], s["activity"] = None, ""
    s["history"].append({"t": s["entered"], "from": frm, "to": to, "note": a.note,
                         "score": a.score, "veto": a.veto})
    msg = f"OK: {sid} {frm} → {to}"
    sc = s["scores"]
    if to == "EM_CORRECAO" and len(sc) >= 2 and sc[-1] <= sc[-2]:
        advice = "Sol replaneja; Luna implementa" if g["config"].get("codex_mode") else \
            "escale o modelo do Code Agent"
        msg += f"\nALERTA_ESTAGNACAO: notas {sc[-2]} → {sc[-1]}; {advice}"
    return g, msg


def cmd_render(a, g):
    sl = g["slices"]
    key = {sid: f"n{i}" for i, sid in enumerate(sl)}
    colors = {"MERGEADA": "#2e7d32", "BLOQUEADA": "#c62828", "PR_ABERTO": "#1565c0",
              "CONFLITO": "#ef6c00", "PRONTA": "#757575"}
    lines = ["# Grafo de fatias", "", f"Atualizado: {now()} · wave {g['wave']}", "",
             "```mermaid", "flowchart LR"]
    for sid, d in sl.items():
        lines.append(f'  {key[sid]}["{cell(sid, 12)} · {cell(d["title"], 40)}<br/>{d["state"]}"]')
        lines.append(f"  style {key[sid]} fill:{colors.get(d['state'], '#6a1b9a')},color:#fff")
        lines += [f"  {key[dep]} --> {key[sid]}" for dep in d["deps"] if dep in sl]
    lines += ["```", "", "| Fatia | Estado | Avaliações | Notas da rodada | PR | Motivo do bloqueio |",
              "|---|---|---|---|---|---|"]
    for sid, d in sl.items():
        notas = "→".join(str(x) for x in d["scores"]) or "—"
        lines.append(f"| {cell(sid, 12)} | {d['state']} | {d['cycles_total']} | {notas} | "
                     f"{cell(d['pr'], 12)} | {cell(d['block_reason'], 80)} |")
    out = a.path.parent / "grafo.md"
    out.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return None, f"OK: {out}"


def cmd_wave(a, g):
    g["wave"] += 1
    return g, f"OK: wave {g['wave']}"


def build_parser():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--path", type=Path, default=Path("tasks/graph.json"))
    sub = p.add_subparsers(dest="cmd", required=True)

    def cmd(name, fn, *args):
        sp = sub.add_parser(name)
        for flag, kw in args:
            sp.add_argument(flag, **kw)
        sp.set_defaults(fn=fn)

    req, num = {"required": True}, {"type": int}
    cmd("init", cmd_init, ("--test-cmd", req), ("--tracker", req),
        ("--ready-label", {"default": "ready"}), ("--base-branch", {"default": "main"}),
        ("--max-parallel", {"type": int, "default": 3}), ("--score-min", {"type": int, "default": 85}),
        ("--max-cycles", {"type": int, "default": 5}), ("--force", {"action": "store_true"}),
        ("--codex", {"action": "store_true"}))
    cmd("add", cmd_add, ("--id", req), ("--title", {}), ("--deps", {}), ("--files", {}),
        ("--priority", num), ("--critical", {"choices": ["sim", "nao"]}),
        ("--state", {"choices": ["PRONTA", "MERGEADA"]}))
    cmd("retire", cmd_retire, ("id", {}), ("--note", req))
    cmd("set", cmd_set, ("id", {}), ("pairs", {"nargs": "+", "metavar": "campo=valor"}))
    cmd("move", cmd_move, ("id", {}), ("to", {}), ("--note", {}), ("--score", num),
        ("--veto", {}), ("--test-exit", num), ("--pr", {}), ("--sem-red", {}))
    for name, fn in (("frontier", cmd_frontier), ("render", cmd_render), ("wave", cmd_wave)):
        cmd(name, fn)
    cmd("codex-enable", cmd_codex_enable)
    cmd("claim", cmd_claim, ("id", {}), ("--owner", req))
    cmd("release", cmd_release, ("id", {}), ("--owner", req))
    cmd("show", cmd_show, ("id", {}))
    cmd("summary", cmd_summary)
    return p


def main():
    a = build_parser().parse_args()
    a.path = a.path.expanduser().resolve()
    with locked(a.path):
        g = None if a.cmd == "init" else load(a.path)
        if g is not None:
            resolve_graph_paths(g, a.path)
        new_g, msg = a.fn(a, g)
        if new_g is not None:
            save(a.path, new_g)
    print(msg)


if __name__ == "__main__":
    main()
