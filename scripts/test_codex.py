"""Testes das garantias Codex; cada caso usa grafos e repositórios em /tmp."""
import json
import os
import subprocess
import sys
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent
ENGINE = SCRIPTS / "grafo.py"
EVIDENCE = SCRIPTS / "evidence.py"


class CodexBase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="wave-codex-test-")
        self.root = Path(self.tmp.name)
        self.graph = self.root / "skill/tasks/graph.json"
        self.repo = self.root / "repo"
        self.repo.mkdir()
        self.git("init", "-q", "-b", "main")
        self.git("config", "user.email", "codex-test@example.invalid")
        self.git("config", "user.name", "Codex Tests")
        (self.repo / "src").mkdir()
        (self.repo / "src/a.txt").write_text("broken\n")
        (self.repo / "docs").mkdir()
        (self.repo / "docs/readme.txt").write_text("base docs\n")
        (self.repo / "check").write_text(
            "#!/usr/bin/env python3\nfrom pathlib import Path\nimport sys\n"
            "sys.exit(0 if Path(__file__).with_name('src').joinpath('a.txt').read_text() == 'fixed\\n' else 1)\n"
        )
        (self.repo / "check").chmod(0o755)
        self.git("add", "src/a.txt", "docs/readme.txt", "check")
        self.git("commit", "-qm", "baseline")
        self.git("checkout", "-qb", "work")
        self.run_g("init", "--test-cmd", str(self.repo / "check"), "--tracker", "local",
                   "--base-branch", "main", "--max-parallel", "1", "--codex", check=True)

    def tearDown(self):
        self.tmp.cleanup()

    def git(self, *args):
        return subprocess.run(["git", "-C", str(self.repo), *args], check=True,
                              capture_output=True, text=True).stdout.strip()

    def run_g(self, *args, check=False):
        cmd = [sys.executable, str(ENGINE), "--path", str(self.graph), *args]
        return subprocess.run(cmd, capture_output=True, text=True, check=check)

    def run_evidence(self, phase, *argv, expected_exit=0):
        cmd = [sys.executable, str(EVIDENCE), "--graph", str(self.graph), "--id", "1",
               "--phase", phase, "--cwd", str(self.repo), "--", *argv]
        result = subprocess.run(cmd, capture_output=True, text=True)
        self.assertEqual(result.returncode, expected_exit, result.stderr)
        return result

    def setup_slice(self):
        self.run_g("add", "--id", "1", "--title", "slice", "--files", "src/a.txt", check=True)
        self.run_g("set", "1", "plan=plan.md", f"worktree={self.repo}", "branch=work", check=True)
        self.run_g("claim", "1", "--owner", "worker-a", check=True)
        self.run_g("move", "1", "PLANEJADA", check=True)
        self.run_g("move", "1", "PLANO_APROVADO", "--note", "ok", check=True)
        self.red_result = self.run_evidence("red", str(self.repo / "check"), expected_exit=1)
        self.run_g("move", "1", "EM_IMPLEMENTACAO", "--test-exit", "0", check=True)

    def finish_implementation(self):
        (self.repo / "src/a.txt").write_text("fixed\n")
        self.git("add", "src/a.txt")
        self.git("commit", "-qm", "implement slice")
        self.green_result = self.run_evidence("green", str(self.repo / "check"))

    def enter_evaluation(self):
        self.setup_slice()
        self.finish_implementation()
        return self.run_g("move", "1", "EM_AVALIACAO")


class TestClaimsAndPaths(CodexBase):
    def test_claim_is_atomic_and_reserves_slot_until_release(self):
        self.run_g("add", "--id", "1", "--files", "src/a.txt", check=True)
        self.run_g("add", "--id", "2", "--files", "docs/b.txt", check=True)
        with ThreadPoolExecutor(2) as pool:
            results = list(pool.map(lambda owner: self.run_g("claim", "1", "--owner", owner),
                                    ("worker-a", "worker-b")))
        self.assertEqual(sum(result.returncode == 0 for result in results), 1)
        data = json.loads(self.graph.read_text())["slices"]["1"]
        self.assertIn(data["owner"], {"worker-a", "worker-b"})
        self.assertEqual(data["activity"], "claimed")
        self.assertIn("sem vaga", self.run_g("claim", "2", "--owner", "worker-c").stderr)
        self.assertNotEqual(self.run_g("release", "1", "--owner", "other").returncode, 0)
        self.run_g("release", "1", "--owner", data["owner"], check=True)
        self.run_g("claim", "2", "--owner", "worker-c", check=True)

    def test_claims_on_same_slice_cannot_both_win(self):
        self.run_g("add", "--id", "1", "--files", "src/a.txt", check=True)
        with ThreadPoolExecutor(2) as pool:
            results = list(pool.map(lambda owner: self.run_g("claim", "1", "--owner", owner),
                                    ("worker-a", "worker-b")))
        self.assertEqual(sum(result.returncode == 0 for result in results), 1)

    def test_claim_rechecks_overlap_when_capacity_allows_parallel_work(self):
        graph = json.loads(self.graph.read_text())
        graph["config"]["max_parallel"] = 2
        self.graph.write_text(json.dumps(graph))
        self.run_g("add", "--id", "1", "--files", "src", check=True)
        self.run_g("add", "--id", "2", "--files", "src/a.txt", check=True)
        self.run_g("claim", "1", "--owner", "worker-a", check=True)
        result = self.run_g("claim", "2", "--owner", "worker-b")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("sobreposição", result.stderr)

    def test_set_panel_fields_show_owner_and_codex_enable_preserves_history(self):
        self.run_g("add", "--id", "1", "--files", "src/a.txt", check=True)
        self.run_g("claim", "1", "--owner", "worker-a", check=True)
        self.run_g("set", "1", "activity=investigando", "model=codex", "thinking=max",
                   "role=reviewer", check=True)
        shown = self.run_g("show", "1", check=True).stdout
        for value in ("worker-a", "investigando", "codex", "max", "reviewer"):
            self.assertIn(value, shown)
        graph = json.loads(self.graph.read_text())
        graph["config"]["codex_mode"] = False
        self.graph.write_text(json.dumps(graph))
        self.run_g("release", "1", "--owner", "worker-a", check=True)
        self.run_g("set", "1", "plan=plan.md", check=True)
        self.run_g("move", "1", "PLANEJADA", check=True)
        graph = json.loads(self.graph.read_text())
        before = graph["slices"]["1"]
        state, history = before["state"], list(before["history"])
        self.run_g("codex-enable", check=True)
        after = json.loads(self.graph.read_text())["slices"]["1"]
        self.assertEqual(after["state"], state)
        self.assertEqual(after["history"], history)

    def test_parent_traversal_is_rejected_in_slice_files(self):
        for path in ("../outside", "src/../../outside", ".."):
            result = self.run_g("add", "--id", "bad", "--files", path)
            self.assertNotEqual(result.returncode, 0, path)
            self.assertIn("..", result.stderr)

    def test_summary_is_compact_and_omits_merged_slices(self):
        self.run_g("add", "--id", "done", "--state", "MERGEADA", check=True)
        self.run_g("add", "--id", "open", "--files", "src/a.txt", check=True)
        summary = self.run_g("summary", check=True).stdout
        self.assertIn("open", summary)
        self.assertNotIn("done", summary)
        self.assertLess(len(summary), 500)

    def test_graph_path_is_canonical_and_plan_log_paths_use_graph_directory(self):
        raw = self.root / "alias/../skill/tasks/graph.json"
        self.assertEqual(raw.resolve(), self.graph.resolve())
        self.run_g("add", "--id", "1", "--files", "src/a.txt", check=True)
        self.run_g("set", "1", "plan=plan.md", "red_log=red.log", "green_log=green.log", check=True)
        data = json.loads(self.graph.read_text())["slices"]["1"]
        self.assertEqual(data["plan"], str((self.graph.parent / "plan.md").resolve()))
        self.assertEqual(data["red_log"], str((self.graph.parent / "red.log").resolve()))


class TestEvidence(CodexBase):
    def test_red_and_green_receipts_are_required_for_current_cycle(self):
        result = self.enter_evaluation()
        self.assertEqual(result.returncode, 0, result.stderr)
        receipt_path = Path(self.red_result.stdout.split("RECIBO: ", 1)[1].strip())
        self.assertEqual(json.loads(receipt_path.read_text())["phase"], "red")

    def test_wrong_phase_or_previous_cycle_red_is_rejected(self):
        self.setup_slice()
        self.finish_implementation()
        self.run_g("move", "1", "EM_AVALIACAO", check=True)
        self.run_g("move", "1", "EM_CORRECAO", "--score", "60", check=True)
        self.run_evidence("green", str(self.repo / "check"))
        result = self.run_g("move", "1", "EM_AVALIACAO")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("red", result.stderr.lower())

    def test_focused_red_receipt_is_accepted_with_full_green(self):
        self.run_g("add", "--id", "1", "--files", "src/a.txt", check=True)
        self.run_g("set", "1", "plan=plan.md", f"worktree={self.repo}", "branch=work", check=True)
        self.run_g("claim", "1", "--owner", "worker-a", check=True)
        self.run_g("move", "1", "PLANEJADA", check=True)
        self.run_g("move", "1", "PLANO_APROVADO", "--note", "ok", check=True)
        result = self.run_evidence("red", sys.executable, "-c", "raise SystemExit(1)", expected_exit=1)
        receipt = json.loads(Path(result.stdout.split("RECIBO: ", 1)[1].strip()).read_text())
        self.assertEqual(receipt["command_argv"], [sys.executable, "-c", "raise SystemExit(1)"])
        self.run_g("move", "1", "EM_IMPLEMENTACAO", "--test-exit", "0", check=True)
        (self.repo / "src/a.txt").write_text("fixed\n")
        self.git("add", "src/a.txt")
        self.git("commit", "-qm", "implementation")
        self.run_evidence("green", str(self.repo / "check"))
        self.run_g("move", "1", "EM_AVALIACAO", check=True)

    def test_green_phase_with_nonzero_exit_cannot_substitute_red(self):
        self.run_g("add", "--id", "1", "--files", "src/a.txt", check=True)
        self.run_g("set", "1", "plan=plan.md", f"worktree={self.repo}", "branch=work", check=True)
        self.run_g("claim", "1", "--owner", "worker-a", check=True)
        self.run_g("move", "1", "PLANEJADA", check=True)
        self.run_g("move", "1", "PLANO_APROVADO", "--note", "ok", check=True)
        self.run_evidence("green", str(self.repo / "check"), expected_exit=1)
        self.run_g("move", "1", "EM_IMPLEMENTACAO", "--test-exit", "0", check=True)
        (self.repo / "src/a.txt").write_text("fixed\n")
        self.git("add", "src/a.txt")
        self.git("commit", "-qm", "implementation")
        self.run_evidence("green", str(self.repo / "check"))
        result = self.run_g("move", "1", "EM_AVALIACAO")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("RED", result.stderr)

    def test_modified_log_is_detected_even_if_mtime_is_restored(self):
        self.setup_slice()
        self.finish_implementation()
        result = self.green_result
        target = Path(result.stdout.split("RECIBO: ", 1)[1].strip())
        data = json.loads(target.read_text())
        log = Path(data["log_path"])
        old = log.stat()
        log.write_text("tampered\n")
        os.utime(log, ns=(old.st_atime_ns, old.st_mtime_ns))
        result = self.run_g("move", "1", "EM_AVALIACAO")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("log", result.stderr.lower())

    def test_receipt_field_tampering_is_rejected(self):
        self.setup_slice()
        self.finish_implementation()
        receipt_output = self.green_result.stdout
        receipt_path = Path(receipt_output.split("RECIBO: ", 1)[1].strip())
        data = json.loads(receipt_path.read_text())
        data["exit_code"] = 1
        receipt_path.write_text(json.dumps(data))
        result = self.run_g("move", "1", "EM_AVALIACAO")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("assinatura", result.stderr.lower())

    def approve(self):
        result = self.enter_evaluation()
        self.assertEqual(result.returncode, 0, result.stderr)
        result = self.run_g("move", "1", "APROVADA", "--score", "90")
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_final_gate_rejects_dirty_worktree(self):
        self.approve()
        (self.repo / "src/a.txt").write_text("dirty after gate\n")
        result = self.run_g("move", "1", "PR_ABERTO", "--test-exit", "0", "--pr", "1")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("vigente", result.stderr.lower())

    def test_final_gate_rejects_commit_after_gate(self):
        self.approve()
        (self.repo / "src/a.txt").write_text("commit after gate\n")
        self.git("add", "src/a.txt")
        self.git("commit", "-qm", "after gate")
        result = self.run_g("move", "1", "PR_ABERTO", "--test-exit", "0", "--pr", "1")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("vigente", result.stderr.lower())

    def test_final_gate_rejects_base_ref_change(self):
        self.approve()
        self.git("checkout", "-q", "main")
        (self.repo / "base.txt").write_text("new base\n")
        self.git("add", "base.txt")
        self.git("commit", "-qm", "advance base")
        self.git("checkout", "-q", "work")
        result = self.run_g("move", "1", "PR_ABERTO", "--test-exit", "0", "--pr", "1")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("vigente", result.stderr.lower())

    def test_stale_additional_gate_fails_even_after_a_fresh_green(self):
        self.enter_evaluation()
        self.run_evidence("gate", "/usr/bin/true")
        (self.repo / "docs/readme.txt").write_text("changed after gate\n")
        self.git("add", "docs/readme.txt")
        self.git("commit", "-qm", "tracked change after gate")
        self.run_evidence("green", str(self.repo / "check"))
        result = self.run_g("move", "1", "APROVADA", "--score", "90")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("GATE vigente", result.stderr)

    def test_green_that_changes_source_during_execution_is_not_valid(self):
        self.setup_slice()
        (self.repo / "src/a.txt").write_text("fixed\n")
        self.git("add", "src/a.txt")
        self.git("commit", "-qm", "implementation")
        (self.repo / "check").write_text(
            "#!/usr/bin/env python3\nfrom pathlib import Path\n"
            "Path('src/a.txt').write_text('changed during test\\n')\n"
        )
        (self.repo / "check").chmod(0o755)
        self.git("add", "check")
        self.git("commit", "-qm", "test command with side effect")
        result = self.run_evidence("green", str(self.repo / "check"))
        receipt = json.loads(Path(result.stdout.split("RECIBO: ", 1)[1].strip()).read_text())
        self.assertFalse(receipt["snapshot_stable"])
        move = self.run_g("move", "1", "EM_AVALIACAO")
        self.assertNotEqual(move.returncode, 0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
