#!/usr/bin/env python3
"""Executa um comando argv e grava recibo assinado fora do worktree.

Exemplo: evidence.py --graph /abs/tasks/graph.json --id 12 --phase red --cwd /repo -- pytest -q
"""
import argparse
import fcntl
import hashlib
import json
import os
import shlex
import subprocess
import sys
import tempfile
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

from codex_guards import fingerprint, repo_lock_path, repo_root_for
from evidence_store import (artifact_root, cycle_token, ensure_key, id_token, sign_receipt)
from grafo_core import load, locked, norm_id


def utc_now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def read_graph(path):
    with locked(path):
        return load(path)


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--graph", required=True, type=Path)
    parser.add_argument("--id", required=True)
    parser.add_argument("--phase", required=True, choices=("red", "green", "gate"))
    parser.add_argument("--cwd", required=True, type=Path)
    parser.add_argument("--base", help="ref base; por padrão usa config.base_branch do grafo")
    parser.add_argument("command", nargs=argparse.REMAINDER, help="argv do comando depois de --")
    args = parser.parse_args(argv)
    args.graph = args.graph.expanduser().resolve()
    args.cwd = args.cwd.expanduser().resolve(strict=True)
    args.command = args.command[1:] if args.command[:1] == ["--"] else args.command
    if not args.command or not args.command[0]:
        parser.error("informe um comando argv depois de --; shell não é usado")
    return args


def expected_test_argv(config):
    try:
        result = shlex.split(str(config.get("test_cmd", "")))
    except ValueError as exc:
        raise ValueError(f"TEST_CMD inválido como argv: {exc}") from exc
    if not result:
        raise ValueError("TEST_CMD vazio no grafo")
    return result


def execute(argv, cwd, log_path):
    temp_path = log_path.with_suffix(".tmp")
    try:
        with temp_path.open("wb") as stream:
            try:
                result = subprocess.run(argv, cwd=cwd, stdout=stream, stderr=subprocess.STDOUT,
                                        shell=False, check=False)
                code = result.returncode
            except OSError as exc:
                stream.write(f"erro ao iniciar comando: {exc}\n".encode())
                code = 127
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temp_path, log_path)
    finally:
        if temp_path.exists():
            temp_path.unlink()
    hasher = hashlib.sha256()
    with log_path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            hasher.update(chunk)
    return code, hasher.hexdigest()


def atomic_json(path, value):
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    fd, temp = tempfile.mkstemp(prefix=".receipt-", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(value, stream, ensure_ascii=False, sort_keys=True, indent=2)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temp, path)
        directory = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        if os.path.exists(temp):
            os.unlink(temp)


def _execute_locked(args, sid, slice_data, base_ref, cwd, repo, excludes):
    token = cycle_token(slice_data)
    artifact = artifact_root(args.graph)
    log_dir = artifact / "logs" / id_token(sid) / token / args.phase
    log_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
    run_id = f"{int(time.time_ns())}-{uuid.uuid4().hex}"
    log_path = log_dir / f"{run_id}.log"
    lock_path = repo_lock_path(repo)
    lock_path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    with lock_path.open("a") as lock_stream:
        fcntl.flock(lock_stream, fcntl.LOCK_EX)
        ensure_key(args.graph)
        before, _, _ = fingerprint(cwd, base_ref, slice_data.get("files", []), excludes)
        started = utc_now()
        exit_code, log_sha256 = execute(args.command, cwd, log_path)
        snap, _, _ = fingerprint(cwd, base_ref, slice_data.get("files", []), excludes)
        receipt = _receipt(args, sid, slice_data, base_ref, cwd, repo, before, snap,
                           started, exit_code, log_path, log_sha256)
        receipt = sign_receipt(receipt, args.graph)
        path = artifact / "receipts" / id_token(sid) / token / args.phase / f"{run_id}.json"
        atomic_json(path, receipt)
        fcntl.flock(lock_stream, fcntl.LOCK_UN)
    print(f"RECIBO: {path}")
    return exit_code


def _receipt(args, sid, data, base_ref, cwd, repo, before, snap, started, code, log_path, log_sha):
    return {"version": 1, "graph_path": str(args.graph), "id": sid, "phase": args.phase,
            "cycle_no": data["cycle_no"], "cycle_entered_ts": data["cycle_entered_ts"],
            "entered_ts": data.get("entered_ts"), "command_argv": args.command,
            "exit_code": code, "cwd": str(cwd), "repo_root": str(repo), "base_ref": base_ref,
            "head": snap["head"], "base_sha": snap["base_sha"], "fingerprint_before": before,
            "fingerprint": snap, "snapshot_stable": before == snap,
            "finished_ns": time.time_ns(), "log_path": str(log_path), "log_sha256": log_sha,
            "started_ts": started, "finished_ts": utc_now()}


def run(args):
    graph = read_graph(args.graph)
    if not graph.get("config", {}).get("codex_mode"):
        raise ValueError("modo Codex desativado; use grafo.py codex-enable")
    sid = norm_id(args.id)
    slice_data = graph["slices"].get(sid)
    if not slice_data:
        raise ValueError(f"fatia {sid!r} não existe")
    if not slice_data.get("cycle_no") or not slice_data.get("cycle_entered_ts"):
        raise ValueError("inicie um ciclo em PLANEJADA antes de registrar evidência")
    expected = expected_test_argv(graph["config"])
    if args.phase == "green" and args.command != expected:
        raise ValueError(f"GREEN deve executar TEST_CMD exato: {shlex.join(expected)}")
    configured_base = graph["config"].get("base_branch", "main")
    if args.base is not None and args.base != configured_base:
        raise ValueError(f"--base precisa corresponder a config.base_branch ({configured_base})")
    base_ref = configured_base
    excludes = [args.graph, args.graph.with_suffix(".lock")]
    # Resolve e valida a raiz/worktree antes de criar artefatos ou executar processos.
    cwd, repo = repo_root_for(args.cwd)
    if cwd != repo:
        raise ValueError("--cwd precisa ser a raiz do repositório, onde TEST_CMD é executado")
    worktree = slice_data.get("worktree")
    if worktree and Path(worktree).expanduser().resolve(strict=True) != repo:
        raise ValueError("--cwd precisa pertencer ao worktree configurado na fatia")
    return _execute_locked(args, sid, slice_data, base_ref, cwd, repo, excludes)


def main(argv=None):
    try:
        return run(parse_args(argv))
    except (OSError, ValueError, KeyError, json.JSONDecodeError) as exc:
        print(f"ERRO: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
