"""Regressions for approval binding, latest GREEN, base pinning, and claims."""
import json
import subprocess
import sys
from pathlib import Path

from test_codex import CodexBase, EVIDENCE


class TestReviewSnapshots(CodexBase):
    def test_approved_snapshot_cannot_be_replaced_by_a_new_green(self):
        result = self.enter_evaluation()
        self.assertEqual(result.returncode, 0, result.stderr)
        result = self.run_g("move", "1", "APROVADA", "--score", "90")
        self.assertEqual(result.returncode, 0, result.stderr)
        (self.repo / "docs/readme.txt").write_text("changed after approval\n")
        self.git("add", "docs/readme.txt")
        self.git("commit", "-qm", "change after review")
        self.run_evidence("green", str(self.repo / "check"))
        result = self.run_g("move", "1", "PR_ABERTO", "--test-exit", "0", "--pr", "17")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("snapshot idêntico", result.stderr.lower())

    def test_failed_latest_green_overrides_older_success_on_same_snapshot(self):
        check = self.repo / "check"
        check.write_text(
            "#!/usr/bin/env python3\nfrom pathlib import Path\nimport sys\n"
            "if Path(__file__).with_name('.fail').exists(): sys.exit(1)\n"
            "sys.exit(0 if Path(__file__).with_name('src').joinpath('a.txt').read_text() == 'fixed\\n' else 1)\n"
        )
        check.chmod(0o755)
        (self.repo / ".gitignore").write_text(".fail\n")
        self.git("add", "check", ".gitignore")
        self.git("commit", "-qm", "make test result depend on ignored switch")
        self.setup_slice()
        self.finish_implementation()
        flag = self.repo / ".fail"
        flag.write_text("fail\n")
        self.run_evidence("green", str(check), expected_exit=1)
        flag.unlink()
        result = self.run_g("move", "1", "EM_AVALIACAO")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("mais recente", result.stderr.lower())

    def test_executor_rejects_base_override_and_receipt_old_base_after_config_change(self):
        self.setup_slice()
        command = [sys.executable, str(EVIDENCE), "--graph", str(self.graph), "--id", "1",
                   "--phase", "red", "--cwd", str(self.repo), "--base", "work", "--",
                   str(self.repo / "check")]
        result = subprocess.run(command, capture_output=True, text=True)
        self.assertEqual(result.returncode, 2)
        self.assertIn("base_branch", result.stderr)
        self.finish_implementation()
        self.run_g("move", "1", "EM_AVALIACAO", check=True)
        graph = json.loads(self.graph.read_text())
        graph["config"]["base_branch"] = "work"
        self.graph.write_text(json.dumps(graph))
        result = self.run_g("move", "1", "APROVADA", "--score", "90")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("config.base_branch", result.stderr)

    def test_executor_rejects_subdirectory_cwd(self):
        self.setup_slice()
        command = [sys.executable, str(EVIDENCE), "--graph", str(self.graph), "--id", "1",
                   "--phase", "gate", "--cwd", str(self.repo / "src"), "--",
                   sys.executable, "-c", "pass"]
        result = subprocess.run(command, capture_output=True, text=True)
        self.assertEqual(result.returncode, 2)
        self.assertIn("raiz do repositório", result.stderr)

    def test_codex_mode_cannot_enter_planned_without_claim(self):
        self.run_g("add", "--id", "1", "--files", "src/a.txt", check=True)
        self.run_g("set", "1", "plan=plan.md", check=True)
        result = self.run_g("move", "1", "PLANEJADA")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("claim", result.stderr.lower())
        self.run_g("claim", "1", "--owner", "worker-a", check=True)
        self.run_g("move", "1", "PLANEJADA", check=True)


if __name__ == "__main__":
    import unittest
    unittest.main(verbosity=2)
