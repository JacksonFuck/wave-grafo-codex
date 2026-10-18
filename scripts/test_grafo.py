#!/usr/bin/env python3
"""Testes do motor: python3 test_grafo.py (cada teste roda num diretório temporário)."""
import json
import subprocess
import sys
import tempfile
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ENGINE = str(Path(__file__).resolve().parent / "grafo.py")


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)
        self.ok("init", "--test-cmd", "t", "--tracker", "gh")

    def tearDown(self):
        self.tmp.cleanup()

    def run_g(self, *args):
        return subprocess.run([sys.executable, ENGINE, *args], cwd=self.dir,
                              capture_output=True, text=True)

    def ok(self, *args):
        r = self.run_g(*args)
        self.assertEqual(r.returncode, 0, f"{args}: {r.stderr}")
        return r.stdout

    def ko(self, *args, contains=""):
        r = self.run_g(*args)
        self.assertNotEqual(r.returncode, 0, f"{args} deveria falhar: {r.stdout}")
        self.assertIn(contains, r.stderr)

    def slice(self, sid):
        return json.loads((self.dir / "tasks/graph.json").read_text())["slices"][sid]

    def logs(self, sid):
        time.sleep(1.1)
        (self.dir / "tasks").mkdir(parents=True, exist_ok=True)
        for name in ("red", "green"):
            (self.dir / f"tasks/{name}{sid}.log").write_text("x")

    def to_eval(self, sid):
        self.ok("set", sid, f"plan=p{sid}", f"worktree=w{sid}", f"branch=b{sid}",
                f"red_log=red{sid}.log", f"green_log=green{sid}.log")
        self.ok("move", sid, "PLANEJADA")
        self.ok("move", sid, "PLANO_APROVADO", "--note", "ok")
        self.ok("move", sid, "EM_IMPLEMENTACAO", "--test-exit", "0")
        self.logs(sid)
        self.ok("move", sid, "EM_AVALIACAO")


class TestGrafo(Base):
    def test_cycle_and_duplicate_deps(self):
        self.ok("add", "--id", "1", "--files", "a")
        self.ok("add", "--id", "2", "--deps", "#1,1", "--files", "b")
        self.assertEqual(self.slice("2")["deps"], ["1"])
        self.ko("add", "--id", "1", "--deps", "2", contains="ciclo")

    def test_frontier_overlap_and_external_dep(self):
        self.ok("add", "--id", "1", "--files", "src/db", "--priority", "1")
        self.ok("add", "--id", "2", "--files", "./src/db/schema.ts")
        self.ok("add", "--id", "3", "--deps", "9", "--files", "c")
        out = self.ok("frontier")
        self.assertIn("ESCOLHIDA: 1", out)
        self.assertIn("FORA: 2 — conflito de arquivos", out)
        self.assertIn("FORA: 3 — dependência fora do grafo", out)
        self.ok("add", "--id", "9", "--state", "MERGEADA")
        self.assertIn("ESCOLHIDA: 3", self.ok("frontier"))

    def test_fim_vs_sem_elegivel(self):
        self.assertIn("FIM", self.ok("frontier"))
        self.ok("add", "--id", "1")
        self.assertIn("SEM_ELEGIVEL", self.ok("frontier"))

    def test_stale_logs_rejected(self):
        self.ok("add", "--id", "1", "--files", "a")
        self.to_eval("1")
        self.ok("move", "1", "EM_CORRECAO", "--score", "60")
        self.ko("move", "1", "EM_AVALIACAO", contains="neste ciclo")
        self.logs("1")
        self.ok("move", "1", "EM_AVALIACAO")

    def test_cycle_limit_and_correction_guard(self):
        self.ok("add", "--id", "1", "--files", "a")
        self.to_eval("1")
        self.ko("move", "1", "EM_CORRECAO", "--score", "99", contains="abaixo do mínimo")
        for _ in range(4):
            self.ok("move", "1", "EM_CORRECAO", "--score", "60")
            self.logs("1")
            self.ok("move", "1", "EM_AVALIACAO")
        self.ko("move", "1", "EM_CORRECAO", "--score", "60", contains="limite")
        self.ok("move", "1", "BLOQUEADA", "--note", "5 avaliações")
        self.ok("move", "1", "PRONTA", "--note", "humano")
        s = self.slice("1")
        self.assertEqual((s["cycles"], s["plan"], s["pr"]), (0, None, None))

    def test_pr_round_resets_and_conflict_path(self):
        self.ok("add", "--id", "1", "--files", "a")
        self.to_eval("1")
        self.ok("move", "1", "APROVADA", "--score", "90")
        self.ko("move", "1", "PR_ABERTO", "--test-exit", "1", "--pr", "7", contains="test-exit 0")
        self.ok("move", "1", "PR_ABERTO", "--test-exit", "0", "--pr", "7")
        self.assertEqual(self.slice("1")["cycles"], 0)
        self.ok("move", "1", "CONFLITO", "--note", "base mudou")
        self.ok("move", "1", "EM_CORRECAO", "--note", "merge da base")
        self.logs("1")
        self.ok("move", "1", "EM_AVALIACAO")
        self.ok("move", "1", "APROVADA", "--score", "88")
        self.ok("move", "1", "PR_ABERTO", "--test-exit", "0")
        self.ok("move", "1", "CONFLITO", "--note", "de novo")
        self.ok("move", "1", "MERGEADA")
        self.ko("move", "1", "BLOQUEADA", "--note", "x", contains="ilegal")

    def test_retire_and_illegal(self):
        self.ok("add", "--id", "1", "--files", "a")
        self.ko("move", "1", "EM_IMPLEMENTACAO", contains="ilegal")
        self.ok("retire", "1", "--note", "saiu do tracker")
        self.ko("move", "1", "PLANEJADA", contains="não existe")

    def test_concurrent_adds(self):
        with ThreadPoolExecutor(10) as ex:
            results = list(ex.map(lambda i: self.run_g("add", "--id", str(i), "--files", f"f{i}"),
                                  range(20)))
        self.assertTrue(all(r.returncode == 0 for r in results))
        data = json.loads((self.dir / "tasks/graph.json").read_text())
        self.assertEqual(len(data["slices"]), 20)

    def test_corrupt_json_and_render(self):
        self.ok("add", "--id", "a b", "--title", 'x|<b>"#[1]', "--files", "a")
        self.ok("render")
        md = (self.dir / "tasks/grafo.md").read_text()
        self.assertIn('n0["a b · x/‹b›', md)
        (self.dir / "tasks/graph.json").write_text("{trunc")
        self.ko("frontier", contains="corrompido")

    def test_init_validation(self):
        self.ko("init", "--test-cmd", "t", "--tracker", "gh", contains="já existe")
        self.ko("init", "--test-cmd", "t", "--tracker", "gh", "--force", "--max-parallel", "0",
                contains="entre 1 e 3")

    def test_conflict_without_red(self):
        self.ok("add", "--id", "1", "--files", "a")
        self.to_eval("1")
        self.ok("move", "1", "EM_CORRECAO", "--score", "60")
        time.sleep(1.1)
        (self.dir / "tasks/green1.log").write_text("y")
        self.ko("move", "1", "EM_AVALIACAO", "--sem-red", "merge", contains="só vale")
        self.logs("1")
        self.ok("move", "1", "EM_AVALIACAO")
        self.ok("move", "1", "APROVADA", "--score", "90")
        self.ok("move", "1", "PR_ABERTO", "--test-exit", "0", "--pr", "3")
        self.ok("move", "1", "CONFLITO", "--note", "c")
        self.ok("move", "1", "EM_CORRECAO", "--note", "merge da base")
        time.sleep(1.1)
        (self.dir / "tasks/green1.log").write_text("z")
        self.ok("move", "1", "EM_AVALIACAO", "--sem-red", "merge sem regressão")

    def test_retire_blocked_refused_and_files_guards(self):
        self.ok("add", "--id", "1", "--files", "a")
        self.ok("move", "1", "BLOQUEADA", "--note", "x")
        self.ko("retire", "1", "--note", "y", contains="só vale para PRONTA")
        self.ko("add", "--id", "2", "--files", "/abs/path.py", contains="relativos")
        self.ok("add", "--id", "3", "--files", "b")
        self.ok("add", "--id", "4", "--files", "c")
        self.ok("set", "3", "plan=p")
        self.ok("move", "3", "PLANEJADA")
        self.ok("set", "4", "plan=p")
        self.ok("move", "4", "PLANEJADA")
        self.ko("add", "--id", "4", "--files", "b,c", contains="colidem")
        self.ok("add", "--id", "5", "--files", ".")
        self.assertIn("FORA: 5 — conflito", self.ok("frontier"))

    def test_v1_migration(self):
        p = self.dir / "tasks/graph.json"
        d = json.loads(p.read_text())
        d["version"] = 1
        d["slices"]["1"] = {"title": "v1", "deps": [], "files": ["a"], "priority": 3,
                            "critical": False, "state": "EM_AVALIACAO", "cycles": 2,
                            "scores": [60, 70], "test_exit": None, "pr": None,
                            "block_reason": None, "history": []}
        p.write_text(json.dumps(d))
        self.ok("move", "1", "APROVADA", "--score", "90")
        self.ok("render")
        self.assertEqual(self.slice("1")["cycles_total"], 3)

    def test_init_bounds(self):
        self.ko("init", "--test-cmd", "t", "--tracker", "gh", "--force", "--max-cycles", "0",
                contains="max-cycles")


if __name__ == "__main__":
    unittest.main(verbosity=1)
