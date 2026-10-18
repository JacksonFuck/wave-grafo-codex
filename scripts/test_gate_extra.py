"""Regressions for independent and stale optional gate commands."""
import unittest

from test_codex import CodexBase


class TestGateCommands(CodexBase):
    def approve(self):
        result = self.enter_evaluation()
        self.assertEqual(result.returncode, 0, result.stderr)
        result = self.run_g("move", "1", "APROVADA", "--score", "90")
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_failed_build_gate_is_not_hidden_by_passed_lint(self):
        self.approve()
        self.run_evidence("gate", "/usr/bin/true")
        self.run_evidence("gate", "/usr/bin/false", expected_exit=1)
        result = self.run_g("move", "1", "PR_ABERTO", "--test-exit", "0", "--pr", "1")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("GATE vigente", result.stderr)


if __name__ == "__main__":
    unittest.main(verbosity=2)
