#!/usr/bin/env python3
"""Run one Grafo role with an explicit model, reasoning level, and sandbox."""

import argparse
import json
import shutil
import subprocess
import sys
from pathlib import Path


ROLES_FILE = Path(__file__).resolve().parents[1] / "agents" / "models.json"
EXPECTED_ROLES = {"coordinator", "planner", "plan-reviewer", "reviewer", "coder", "corrector"}
EXPECTED_MODEL = {
    "coordinator": "gpt-6.1-sol",
    "planner": "gpt-6.1-sol",
    "plan-reviewer": "gpt-6.1-sol",
    "reviewer": "gpt-6.1-sol",
    "coder": "gpt-6-luna",
    "corrector": "gpt-6-luna",
}
EXPECTED_SANDBOX = {
    "coordinator": "workspace-write",
    "planner": "read-only",
    "plan-reviewer": "read-only",
    "reviewer": "read-only",
    "coder": "workspace-write",
    "corrector": "workspace-write",
}


class RunRoleError(Exception):
    pass


def load_roles():
    try:
        data = json.loads(ROLES_FILE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RunRoleError(f"cannot load role model configuration {ROLES_FILE}: {exc}") from exc
    roles = data.get("roles") if isinstance(data, dict) else None
    if not isinstance(roles, dict) or set(roles) != EXPECTED_ROLES:
        raise RunRoleError("role configuration must define exactly the six supported roles")
    for role, entry in roles.items():
        if (not isinstance(entry, dict) or not isinstance(entry.get("model"), str)
                or entry.get("model") != EXPECTED_MODEL[role]
                or entry.get("reasoning_effort") != "max"
                or entry.get("sandbox") != EXPECTED_SANDBOX[role]):
            raise RunRoleError(f"invalid model, max effort, or sandbox configuration for role {role}")
    return roles


def validate_cache(cache_path, model, effort):
    try:
        data = json.loads(Path(cache_path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RunRoleError(f"cannot read model cache {cache_path}: {exc}") from exc
    models = data.get("models") if isinstance(data, dict) else None
    if not isinstance(models, list):
        raise RunRoleError(f"model cache {cache_path} has no usable models list")
    match = next((item for item in models if isinstance(item, dict) and item.get("slug") == model), None)
    if match is None:
        raise RunRoleError(f"model {model} is absent from model cache {cache_path}; no fallback will be used")
    levels = match.get("supported_reasoning_levels")
    if not isinstance(levels, list):
        raise RunRoleError(f"model cache does not verify reasoning effort {effort} for {model}")
    supported = {item if isinstance(item, str) else item.get("effort")
                 for item in levels if isinstance(item, (str, dict))}
    if effort not in supported:
        raise RunRoleError(f"model {model} does not list reasoning effort {effort} in {cache_path}")


def absolute_path(raw, label, strict):
    path = Path(raw)
    if not path.is_absolute():
        raise RunRoleError(f"{label} path must be absolute: {raw}")
    try:
        return path.resolve(strict=strict)
    except OSError as exc:
        raise RunRoleError(f"cannot resolve {label} path {raw}: {exc}") from exc


def prepare_paths(args):
    repo = absolute_path(args.repo, "repo", True)
    prompt = absolute_path(args.prompt_file, "prompt", True)
    output = absolute_path(args.output, "output", False)
    if not repo.is_dir():
        raise RunRoleError(f"repo is not a directory: {repo}")
    if not prompt.is_file():
        raise RunRoleError(f"prompt is not a file: {prompt}")
    events = output.with_suffix(".events.jsonl")
    if output in {prompt, events} or events == prompt:
        raise RunRoleError("output, event log, and prompt paths must be distinct")
    if output.exists():
        raise RunRoleError(f"refusing to overwrite existing output: {output}")
    if events.exists():
        raise RunRoleError(f"refusing to overwrite existing event log: {events}")
    return repo, prompt, output, events


def build_argv(role, config, repo, output):
    return [
        "codex", "exec", "-m", config["model"],
        "-c", f'model_reasoning_effort="{config["reasoning_effort"]}"',
        "-C", str(repo), "-s", config["sandbox"], "--json",
        "--output-last-message", str(output),
    ]


def read_usage(event_path):
    """Return usage from the last completed turn event, not a run-wide total."""
    usage = None
    try:
        with event_path.open(encoding="utf-8") as events:
            for line in events:
                try:
                    event = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if isinstance(event, dict) and event.get("type") == "turn.completed":
                    value = event.get("usage")
                    usage = value if isinstance(value, dict) else None
    except OSError:
        return None
    return usage


def summary(role, config, repo, output, events, argv, exit_code=None, usage=None, dry_run=False,
            codex_exit_code=None, error=None):
    success = None if dry_run else exit_code == 0
    return {
        "status": "dry_run" if dry_run else ("success" if success else "failed"),
        "dry_run": dry_run,
        "success": success,
        "role": role,
        "model": config["model"],
        "reasoning_effort": config["reasoning_effort"],
        "sandbox": config["sandbox"],
        "repo": str(repo),
        "output": str(output),
        "events": str(events),
        "exit_code": exit_code,
        "codex_exit_code": codex_exit_code,
        "usage": usage,
        "metrics": {"cost_usd": None, "context_percent": None},
        **({"error": error} if error else {}),
        **({"argv": argv} if dry_run else {}),
    }


def final_message_result(exit_code, output):
    if exit_code != 0:
        return exit_code, None
    try:
        has_message = output.is_file() and output.stat().st_size > 0
    except OSError:
        has_message = False
    if has_message:
        return exit_code, None
    error = f"codex exited 0 but produced no non-empty final message at {output}"
    print(f"run_role: {error}", file=sys.stderr)
    return 1, error


def run(args):
    roles = load_roles()
    config = roles[args.role]
    repo, prompt, output, events = prepare_paths(args)
    if args.check_models:
        validate_cache(args.check_models, config["model"], config["reasoning_effort"])
    argv = build_argv(args.role, config, repo, output)
    if args.dry_run:
        print(json.dumps(summary(args.role, config, repo, output, events, argv, dry_run=True)))
        return 0
    if shutil.which("codex") is None:
        raise RunRoleError("codex executable was not found on PATH")
    output.parent.mkdir(parents=True, exist_ok=True)
    reserved = []
    try:
        output_handle = output.open("xb")
        reserved.append(output)
        output_handle.close()
        event_handle = events.open("xb")
        reserved.append(events)
    except OSError:
        for path in reserved:
            try:
                path.unlink()
            except OSError:
                pass
        raise
    try:
        with prompt.open("rb") as prompt_stream, event_handle:
            result = subprocess.run(argv, stdin=prompt_stream, stdout=event_handle, check=False)
    except OSError as exc:
        for path in reserved:
            try:
                path.unlink()
            except OSError:
                pass
        raise RunRoleError(f"could not execute codex: {exc}") from exc
    usage = read_usage(events)
    exit_code, error = final_message_result(result.returncode, output)
    print(json.dumps(summary(
        args.role, config, repo, output, events, argv, exit_code, usage,
        codex_exit_code=result.returncode, error=error,
    )))
    return exit_code


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--role", required=True, choices=sorted(EXPECTED_ROLES))
    parser.add_argument("--repo", required=True, help="absolute repository directory")
    parser.add_argument("--prompt-file", required=True, help="absolute prompt file, sent on stdin")
    parser.add_argument("--output", required=True, help="absolute final-message path")
    parser.add_argument("--dry-run", action="store_true", help="print the plan without running codex")
    parser.add_argument("--check-models", metavar="CACHE", help="validate model and max effort against a local cache")
    args = parser.parse_args(argv)
    try:
        return run(args)
    except (RunRoleError, OSError) as exc:
        print(f"run_role: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
