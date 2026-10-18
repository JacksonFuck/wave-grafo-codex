import json
import os
import pathlib
import subprocess
import sys
import tempfile
import unittest


SCRIPT = pathlib.Path(__file__).with_name("run_role.py")


class RunRoleTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = pathlib.Path(self.temp.name)
        self.repo = self.root / "repo"
        self.repo.mkdir()
        self.prompt = self.root / "prompt.txt"
        self.marker = self.root / "command-ran"
        self.prompt.write_text(f"Review this literal: $(touch {self.marker})\n")
        self.output = self.root / "result.md"
        self.bin_dir = self.root / "bin"
        self.bin_dir.mkdir()
        self.received = self.root / "received.json"
        self.called = self.root / "called"
        self.fake = self.bin_dir / "codex"
        self.fake.write_text(
            "#!/usr/bin/env python3\n"
            "import json, os, pathlib, sys\n"
            "pathlib.Path(os.environ['FAKE_CALLED']).write_text('yes')\n"
            "pathlib.Path(os.environ['FAKE_RECEIVED']).write_text(json.dumps({'argv': sys.argv[1:], 'stdin': sys.stdin.read()}))\n"
            "args = sys.argv[1:]\n"
            "target = args[args.index('--output-last-message') + 1]\n"
            "if os.environ.get('FAKE_NO_MESSAGE') != '1': pathlib.Path(target).write_text('final message')\n"
            "print(json.dumps({'type': 'turn.completed', 'usage': {'input_tokens': 12, 'output_tokens': 4}}))\n"
            "sys.exit(int(os.environ.get('FAKE_EXIT', '0')))\n"
        )
        self.fake.chmod(0o755)
        self.env = os.environ.copy()
        self.env["PATH"] = str(self.bin_dir) + os.pathsep + self.env.get("PATH", "")
        self.env["FAKE_CALLED"] = str(self.called)
        self.env["FAKE_RECEIVED"] = str(self.received)

    def tearDown(self):
        self.temp.cleanup()

    def run_cli(self, *args, env=None):
        return subprocess.run(
            [sys.executable, str(SCRIPT), *map(str, args)],
            text=True,
            capture_output=True,
            env=env or self.env,
        )

    def base_args(self, *extra):
        return (
            "--role", "coder",
            "--repo", self.repo,
            "--prompt-file", self.prompt,
            "--output", self.output,
            *extra,
        )

    def test_uses_luna_max_workspace_write_and_safe_stdin(self):
        result = self.run_cli(*self.base_args())
        self.assertEqual(result.returncode, 0, result.stderr)
        call = json.loads(self.received.read_text())
        self.assertEqual(call["stdin"], self.prompt.read_text())
        self.assertFalse(self.marker.exists())
        self.assertIn("gpt-6-luna", call["argv"])
        self.assertIn("workspace-write", call["argv"])
        self.assertIn('model_reasoning_effort="max"', call["argv"])
        self.assertEqual(self.output.read_text(), "final message")
        events = self.output.with_suffix(".events.jsonl")
        event = json.loads(events.read_text().splitlines()[-1])
        self.assertEqual(event["type"], "turn.completed")
        summary = json.loads(result.stdout)
        self.assertEqual(summary["usage"], {"input_tokens": 12, "output_tokens": 4})
        self.assertEqual(summary["events"], str(events))

    def test_creates_missing_output_parent_when_running(self):
        self.output = self.root / "created" / "nested" / "result.md"
        result = self.run_cli(*self.base_args())
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.output.read_text(), "final message")
        self.assertTrue(self.output.with_suffix(".events.jsonl").is_file())

    def test_dry_run_reports_plan_without_invoking_codex_or_writing(self):
        result = self.run_cli(*self.base_args("--dry-run"))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse(self.called.exists())
        self.assertFalse(self.output.exists())
        plan = json.loads(result.stdout)
        self.assertTrue(plan["dry_run"])
        self.assertEqual(plan["model"], "gpt-6-luna")
        self.assertEqual(plan["reasoning_effort"], "max")
        self.assertEqual(plan["sandbox"], "workspace-write")
        self.assertIn("--output-last-message", plan["argv"])

    def test_all_roles_use_their_assigned_model_and_sandbox(self):
        expected = {
            "coordinator": ("gpt-6.1-sol", "workspace-write"),
            "planner": ("gpt-6.1-sol", "read-only"),
            "plan-reviewer": ("gpt-6.1-sol", "read-only"),
            "reviewer": ("gpt-6.1-sol", "read-only"),
            "coder": ("gpt-6-luna", "workspace-write"),
            "corrector": ("gpt-6-luna", "workspace-write"),
        }
        for role, (model, sandbox) in expected.items():
            with self.subTest(role=role):
                args = list(self.base_args("--dry-run"))
                args[1] = role
                result = self.run_cli(*args)
                self.assertEqual(result.returncode, 0, result.stderr)
                plan = json.loads(result.stdout)
                self.assertEqual(plan["model"], model)
                self.assertEqual(plan["sandbox"], sandbox)
                self.assertEqual(plan["reasoning_effort"], "max")
        self.assertFalse(self.called.exists())

    def test_nonzero_codex_exit_is_propagated_without_retry(self):
        env = self.env.copy()
        env["FAKE_EXIT"] = "17"
        result = self.run_cli(*self.base_args(), env=env)
        self.assertEqual(result.returncode, 17)
        self.assertTrue(self.called.exists())
        self.assertEqual(self.received.read_text().count('"argv"'), 1)

    def test_zero_exit_without_final_message_is_failure(self):
        env = self.env.copy()
        env["FAKE_NO_MESSAGE"] = "1"
        result = self.run_cli(*self.base_args(), env=env)
        self.assertEqual(result.returncode, 1)
        self.assertIn("no non-empty final message", result.stderr)
        summary = json.loads(result.stdout)
        self.assertFalse(summary["success"])
        self.assertEqual(summary["codex_exit_code"], 0)
        self.assertEqual(summary["exit_code"], 1)
        self.assertEqual(self.output.stat().st_size, 0)

    def test_rejects_existing_output_and_prompt_output_collision(self):
        self.output.write_text("keep me")
        result = self.run_cli(*self.base_args())
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(self.output.read_text(), "keep me")

        same = self.run_cli(
            "--role", "coder", "--repo", self.repo,
            "--prompt-file", self.prompt, "--output", self.prompt,
        )
        self.assertNotEqual(same.returncode, 0)
        self.assertEqual(self.prompt.read_text(), f"Review this literal: $(touch {self.marker})\n")

        self.output.unlink()
        event_path = self.output.with_suffix(".events.jsonl")
        event_path.write_text("keep events")
        collision = self.run_cli(*self.base_args())
        self.assertNotEqual(collision.returncode, 0)
        self.assertEqual(event_path.read_text(), "keep events")

    def test_rejects_cache_without_model_or_max_effort(self):
        cases = [
            {"models": []},
            {"models": [{"slug": "gpt-6-luna", "supported_reasoning_levels": [{"effort": "high"}]}]},
        ]
        for cache in cases:
            with self.subTest(cache=cache):
                cache_path = self.root / "models.json"
                cache_path.write_text(json.dumps(cache))
                result = self.run_cli(*self.base_args("--check-models", cache_path))
                self.assertNotEqual(result.returncode, 0)
                self.assertFalse(self.called.exists())
                self.assertTrue(result.stderr)


if __name__ == "__main__":
    unittest.main()
