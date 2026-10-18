"""Assinatura, fingerprint e validação de recibos Codex do motor do grafo."""
import hashlib
import os
import re
import shlex
import stat
import subprocess
import sys
from pathlib import Path
from evidence_store import LOCKS, canonical_json, cycle_token, digest, gate_dir, receipts_for


def repo_lock_path(repo_root):
    common = git_text(repo_root, "rev-parse", "--git-common-dir")
    common_path = Path(common)
    if not common_path.is_absolute():
        common_path = Path(repo_root) / common_path
    return LOCKS / f"{digest(str(common_path.resolve()).encode())}.lock"

def safe_repo_path(path, root):
    try:
        return Path(path).resolve().relative_to(Path(root).resolve()).as_posix()
    except (ValueError, OSError):
        return None


def git_text(root, *args):
    result = subprocess.run(["git", "-C", str(root), *args], capture_output=True, text=True)
    if result.returncode:
        detail = result.stderr.strip() or result.stdout.strip() or "git falhou"
        raise ValueError(detail)
    return result.stdout.strip()


def repo_root_for(cwd):
    path = Path(cwd).expanduser().resolve(strict=True)
    if not path.is_dir():
        raise ValueError(f"cwd não é diretório: {path}")
    root = Path(git_text(path, "rev-parse", "--show-toplevel")).resolve(strict=True)
    path.relative_to(root)
    return path, root


def validate_ref(base_ref):
    if not isinstance(base_ref, str) or not re.fullmatch(r"[A-Za-z0-9._/-]+", base_ref) or \
            base_ref.startswith(("/", "-")) or base_ref.endswith("/") or \
            ".." in base_ref.split("/") or "//" in base_ref:
        raise ValueError(f"base ref inválida: {base_ref!r}")


def _untracked_digest(root, name):
    path = root / name
    try:
        info = path.lstat()
        if stat.S_ISLNK(info.st_mode):
            value = b"symlink\0" + os.readlink(path).encode(errors="surrogateescape")
        elif stat.S_ISREG(info.st_mode):
            hasher = hashlib.sha256()
            with path.open("rb") as stream:
                for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                    hasher.update(chunk)
            value = b"file\0" + hasher.digest()
        else:
            value = f"mode:{stat.S_IFMT(info.st_mode)}".encode()
        return digest(value)
    except OSError as exc:
        raise ValueError(f"não foi possível fingerprint untracked {name}: {exc}") from exc


def _relevant_untracked(name, owned_files):
    name = name.replace("\\", "/")
    if not owned_files or "." in owned_files:
        return True
    return any(name == owned or name.startswith(owned.rstrip("/") + "/") or
               owned.startswith(name.rstrip("/") + "/") for owned in owned_files)


def fingerprint(cwd, base_ref, owned_files=(), exclude_paths=()):
    cwd_path, root = repo_root_for(cwd)
    validate_ref(base_ref)
    head = git_text(root, "rev-parse", "--verify", "HEAD")
    base_sha = git_text(root, "rev-parse", "--verify", f"{base_ref}^{{commit}}")
    exclude = []
    for path in exclude_paths:
        rel = safe_repo_path(path, root)
        if rel and rel != ".":
            exclude.append(f":(exclude){rel}")
    diff = subprocess.run(["git", "-C", str(root), "diff", "--binary", "HEAD", "--", ".", *exclude],
                          capture_output=True)
    if diff.returncode:
        raise ValueError(diff.stderr.decode(errors="replace").strip() or "git diff falhou")
    listed = subprocess.run(["git", "-C", str(root), "ls-files", "--others", "--exclude-standard", "-z"],
                            capture_output=True)
    if listed.returncode:
        raise ValueError("git ls-files falhou")
    ignored = {safe_repo_path(path, root) for path in exclude_paths}
    owned = [str(item).replace("\\", "/").removeprefix("./") for item in owned_files]
    untracked = []
    for raw in listed.stdout.split(b"\0"):
        if not raw:
            continue
        name = os.fsdecode(raw)
        if name not in ignored and _relevant_untracked(name, owned):
            untracked.append({"path": name, "sha256": _untracked_digest(root, name)})
    fields = {"head": head, "base_ref": base_ref, "base_sha": base_sha,
              "tracked_sha256": digest(diff.stdout), "untracked": sorted(untracked, key=lambda x: x["path"])}
    return {**fields, "sha256": digest(canonical_json(fields))}, cwd_path, root


def _test_argv(config):
    try:
        argv = shlex.split(str(config.get("test_cmd", "")))
    except ValueError as exc:
        raise ValueError(f"TEST_CMD não pode ser lido como argv: {exc}") from exc
    if not argv:
        raise ValueError("TEST_CMD vazio")
    return argv


def _current(receipt, slice_data, graph_path, config):
    configured_base = config.get("base_branch", "main")
    if receipt.get("base_ref") != configured_base:
        return False, f"base do recibo não corresponde a config.base_branch ({configured_base})"
    worktree = slice_data.get("worktree")
    try:
        cwd, repo = repo_root_for(receipt["cwd"])
        if cwd != repo or receipt.get("repo_root") != str(repo):
            return False, "recibo não foi executado na raiz do repositório"
        if worktree and Path(worktree).expanduser().resolve(strict=True) != repo:
            return False, "worktree mudou"
        excludes = [graph_path, Path(graph_path).with_suffix(".lock")]
        current, _, _ = fingerprint(cwd, configured_base, slice_data.get("files", []), excludes)
    except (KeyError, OSError, ValueError) as exc:
        return False, f"fingerprint indisponível: {exc}"
    if current != receipt.get("fingerprint"):
        return False, "HEAD, base ou fingerprint do worktree mudou"
    return True, ""


def _phase_candidates(graph_path, sid, slice_data, phase):
    return receipts_for(graph_path, sid, slice_data, phase)


def _latest_green(graph, sid, graph_path):
    slice_data = graph["slices"][sid]
    config = graph["config"]
    expected = _test_argv(config)
    receipts, errors = _phase_candidates(graph_path, sid, slice_data, "green")
    if errors:
        raise ValueError(f"GREEN contém recibo inválido: {'; '.join(errors)}")
    # A tentativa completa mais recente manda: um GREEN antigo não pode mascarar
    # a falha de uma suíte repetida no mesmo snapshot.
    matching = [r for r in receipts if r.get("command_argv") == expected]
    if not matching:
        raise ValueError("não há recibo GREEN completo de TEST_CMD neste ciclo")
    latest = max(matching, key=lambda r: int(r.get("finished_ns", 0)))
    current, reason = _current(latest, slice_data, graph_path, config)
    if latest.get("exit_code") != 0 or latest.get("snapshot_stable") is not True or not current:
        detail = reason or "o último GREEN TEST_CMD não passou de forma estável"
        raise ValueError(f"GREEN TEST_CMD mais recente não é válido: {detail}")
    return latest


def _red_matches(receipt, slice_data, config, green):
    if (receipt.get("exit_code", 0) == 0 or receipt.get("snapshot_stable") is not True or
            receipt.get("base_ref") != config.get("base_branch", "main") or
            receipt.get("repo_root") != green.get("repo_root")):
        return False
    try:
        cwd, repo = repo_root_for(receipt["cwd"])
        worktree = slice_data.get("worktree")
        return cwd == repo and str(repo) == receipt.get("repo_root") and (
            not worktree or Path(worktree).expanduser().resolve(strict=True) == repo)
    except (KeyError, OSError, ValueError):
        return False


def _require_evaluation(graph, sid, graph_path):
    slice_data, config = graph["slices"][sid], graph["config"]
    try:
        green = _latest_green(graph, sid, graph_path)
    except ValueError as exc:
        raise ValueError(f"EM_AVALIACAO exige GREEN vigente: {exc}") from exc
    red, errors = _phase_candidates(graph_path, sid, slice_data, "red")
    matching = [receipt for receipt in red if _red_matches(receipt, slice_data, config, green)]
    if errors or not matching:
        detail = f" ({'; '.join(errors)})" if errors else ""
        raise ValueError(f"EM_AVALIACAO exige RED não zero, estável, mesmo ciclo/repo/base{detail}")


def _require_gates(graph, sid, destination, graph_path):
    slice_data = graph["slices"][sid]
    gates, errors = _phase_candidates(graph_path, sid, slice_data, "gate")
    directory = gate_dir(graph_path, sid, slice_data)
    if not directory.exists() or not list(directory.glob("*.json")):
        return
    if errors:
        raise ValueError(f"{destination} tem recibo GATE inválido: {'; '.join(errors)}")
    latest = {}
    for receipt in gates:
        key = canonical_json(receipt.get("command_argv"))
        previous = latest.get(key)
        if previous is None or int(receipt.get("finished_ns", 0)) > int(previous.get("finished_ns", 0)):
            latest[key] = receipt
    for receipt in latest.values():
        current, reason = _current(receipt, slice_data, graph_path, graph["config"])
        if receipt.get("exit_code") != 0 or receipt.get("snapshot_stable") is not True or not current:
            detail = reason or "o último recibo do comando não passou de forma estável"
            raise ValueError(f"{destination} exige cada GATE vigente e verde: {detail}")


def _require_final_snapshot(graph, sid, destination, graph_path):
    slice_data = graph["slices"][sid]
    try:
        green = _latest_green(graph, sid, graph_path)
    except ValueError as exc:
        raise ValueError(f"{destination} exige GREEN TEST_CMD vigente: {exc}") from exc
    if destination == "PR_ABERTO" and (
            slice_data.get("approval_fingerprint") != green.get("fingerprint") or
            slice_data.get("approval_base_ref") != green.get("base_ref") or
            slice_data.get("approval_test_argv") != green.get("command_argv")):
        raise ValueError("PR_ABERTO exige snapshot idêntico ao da aprovação; volte a EM_CORRECAO e revise")
    _require_gates(graph, sid, destination, graph_path)
    return green


def require_transition(graph, sid, destination, graph_path):
    if not graph.get("config", {}).get("codex_mode"):
        return None
    if destination == "EM_AVALIACAO":
        _require_evaluation(graph, sid, graph_path)
    if destination in {"APROVADA", "PR_ABERTO"}:
        green = _require_final_snapshot(graph, sid, destination, graph_path)
        if destination == "APROVADA":
            return {"approval_fingerprint": green["fingerprint"],
                    "approval_base_ref": green["base_ref"],
                    "approval_test_argv": green["command_argv"]}
    return None


def fail_transition(graph, sid, destination, graph_path):
    try:
        return require_transition(graph, sid, destination, graph_path)
    except ValueError as exc:
        print(f"ERRO: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc
