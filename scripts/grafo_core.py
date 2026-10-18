"""Modelo do /wave-grafo: estados, guardas de E/S, lock e algoritmos do DAG."""
import fcntl
import json
import os
import posixpath
import sys
import tempfile
import time
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

# Arestas legais. BLOQUEADA é alcançável de qualquer estado, exceto MERGEADA e BLOQUEADA.
EDGES = {
    "PRONTA": {"PLANEJADA"}, "PLANEJADA": {"PLANO_APROVADO", "PRONTA"},
    "PLANO_APROVADO": {"EM_IMPLEMENTACAO"}, "EM_IMPLEMENTACAO": {"EM_AVALIACAO"},
    "EM_AVALIACAO": {"EM_CORRECAO", "APROVADA"}, "EM_CORRECAO": {"EM_AVALIACAO"},
    "APROVADA": {"PR_ABERTO", "EM_CORRECAO"},
    "PR_ABERTO": {"MERGEADA", "CONFLITO", "EM_CORRECAO"},
    "CONFLITO": {"MERGEADA", "EM_CORRECAO"},
    "BLOQUEADA": {"PRONTA"}, "MERGEADA": set()}
NO_BLOCK_FROM = {"MERGEADA", "BLOQUEADA"}
# Ocupam vaga e travam arquivos.
OCCUPYING = {"PLANEJADA", "PLANO_APROVADO", "EM_IMPLEMENTACAO", "EM_AVALIACAO",
             "EM_CORRECAO", "APROVADA", "PR_ABERTO", "CONFLITO"}
SETTABLE = {"title", "plan", "worktree", "branch", "agent_id", "model",
            "red_log", "green_log", "review_seen", "activity", "thinking", "role"}
# Defaults para migrar grafos antigos campo a campo.
SLICE_DEFAULTS = {"title": "", "deps": [], "files": [], "priority": 3, "critical": False,
                  "entered": None, "entered_ts": None, "cycles": 0, "cycles_total": None,
                  "scores": [], "test_exit": None, "pr": None, "block_reason": None,
                  "history": [], "origin": None, "review_seen": None,
                  "owner": None, "activity": "", "thinking": "", "role": "",
                  "cycle_no": 0, "cycle_entered_ts": None, "approval_fingerprint": None,
                  "approval_base_ref": None, "approval_test_argv": None}
# Limpos quando uma fatia bloqueada volta a PRONTA (worktree e branch são reaproveitados).
RESET_ON_UNBLOCK = ("plan", "pr", "agent_id", "red_log", "green_log", "test_exit",
                    "review_seen", "origin", "cycle_entered_ts", "approval_fingerprint",
                    "approval_base_ref", "approval_test_argv")


def now():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def fail(msg):
    print(f"ERRO: {msg}", file=sys.stderr)
    sys.exit(1)


@contextmanager
def locked(path):
    """Serializa leitura-alteração-gravação entre processos concorrentes."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path.with_suffix(".lock"), "w") as fh:
        fcntl.flock(fh, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(fh, fcntl.LOCK_UN)


def load(path):
    if not path.exists():
        fail(f"{path} não existe. Primeira execução: grafo.py init (nunca use --force sobre um grafo ativo)")
    try:
        g = json.loads(path.read_text(encoding="utf-8"))
        for sid, s in g["slices"].items():
            s["state"]  # noqa: B018 — estado é obrigatório
            for k, v in SLICE_DEFAULTS.items():
                s.setdefault(k, json.loads(json.dumps(v)))
            s["entered"] = s["entered"] or now()
            s["entered_ts"] = s["entered_ts"] or time.time()
            s["cycles_total"] = s["cycles"] if s["cycles_total"] is None else s["cycles_total"]
            if s["cycle_entered_ts"] is None and s["state"] != "PRONTA":
                s["cycle_entered_ts"] = s["entered"]
            if s["cycle_no"] == 0 and s["state"] != "PRONTA":
                s["cycle_no"] = max(1, s["cycles_total"] + 1)
        g["config"]["max_cycles"]  # noqa: B018
        g["version"] = 2
    except (json.JSONDecodeError, KeyError, TypeError, AttributeError) as exc:
        fail(f"{path} corrompido ({exc}). Restaure pelo git ou pelo histórico antes de seguir")
    return g


def save(path, g):
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".graph-", suffix=".tmp")
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        json.dump(g, fh, ensure_ascii=False, indent=2)
    os.replace(tmp, path)


def norm_id(value):
    return value.strip().lstrip("#").strip()


def ids(value):
    return list(dict.fromkeys(norm_id(x) for x in (value or "").split(",") if norm_id(x)))


def norm_file(value):
    p = posixpath.normpath(value.strip().replace("\\", "/"))
    return p[2:] if p.startswith("./") else p


def files(value):
    raw = [x.strip().replace("\\", "/") for x in (value or "").split(",") if x.strip()]
    escaped = [x for x in raw if ".." in x.split("/")]
    if escaped:
        fail(f"arquivos não podem subir diretórios com '..': {', '.join(escaped)}")
    out = list(dict.fromkeys(norm_file(x) for x in raw))
    absolute = [f for f in out if f.startswith("/") or ":" in f[:3]]
    if absolute:
        fail(f"use caminhos relativos à raiz do repositório: {', '.join(absolute)}")
    return out


def overlap(a, b):
    """Arquivos em conflito: iguais, ou um é diretório-prefixo do outro."""
    hits = set()
    for x in a:
        for y in b:
            if "." in (x, y) or x == y or y.startswith(x + "/") or x.startswith(y + "/"):
                hits.add(x)
    return hits


def find_cycle(g):
    """Fatias em ciclo (Kahn); lista vazia se o grafo é DAG."""
    sl = g["slices"]
    indeg = {s: sum(1 for d in set(v["deps"]) if d in sl) for s, v in sl.items()}
    queue, seen = [s for s, n in indeg.items() if n == 0], 0
    while queue:
        cur = queue.pop()
        seen += 1
        for s, v in sl.items():
            if cur in set(v["deps"]):
                indeg[s] -= 1
                if indeg[s] == 0:
                    queue.append(s)
    return sorted(s for s, n in indeg.items() if n > 0) if seen < len(sl) else []


def dependents_count(g, sid):
    """Quantas fatias dependem (transitivamente) de sid: prioriza o caminho crítico."""
    sl, found, stack = g["slices"], set(), [sid]
    while stack:
        cur = stack.pop()
        for s, v in sl.items():
            if cur in v["deps"] and s not in found:
                found.add(s)
                stack.append(s)
    return len(found)


def cell(text, limit=60):
    """Texto seguro para Mermaid e tabela Markdown."""
    t = " ".join(str(text or "—").split())
    for ch, rep in (("|", "/"), ('"', "'"), ("<", "‹"), (">", "›"), ("[", "("), ("]", ")"), ("#", "nº ")):
        t = t.replace(ch, rep)
    return t[:limit]


def mtime_after(path_str, entered_ts):
    """O arquivo foi alterado depois da entrada no estado atual (epoch em segundos)?"""
    p = Path(path_str)
    return p.exists() and p.stat().st_mtime > float(entered_ts or 0)
