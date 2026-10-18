#!/usr/bin/env python3
"""Testes locais do Kanban e de sua API somente leitura."""
import http.client
import json
import subprocess
import sys
import tempfile
import threading
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import kanban
import painel


SCRIPT = Path(__file__).resolve().parent / "kanban.py"


def sample_graph():
    states = ["PRONTA", "PLANEJADA", "PLANO_APROVADO", "EM_IMPLEMENTACAO",
              "EM_AVALIACAO", "EM_CORRECAO", "APROVADA", "PR_ABERTO",
              "CONFLITO", "MERGEADA", "BLOQUEADA", "FUTURA"]
    slices = {str(index): {"state": state, "title": f"item {state}", "deps": [],
                           "history": [], "entered": "2026-10-18T12:00:00Z",
                           "entered_ts": index}
              for index, state in enumerate(states)}
    slices.update({
        "old-pesq": {"state": "PRONTA", "title": "busca legada", "agent_id": "pesq-1"},
        "old-plan": {"state": "PRONTA", "title": "plano legado", "agent_id": "plan-1"},
        "old-ready": {"state": "PRONTA", "title": "aprovado legado", "agent_id": "planejado-1"},
        "legacy-pause": {"state": "PRONTA", "title": "PAUSADA - aguarda pessoa"},
        "act-research": {"state": "PRONTA", "activity": "research", "title": "pesquisa"},
        "act-planning": {"state": "PRONTA", "activity": "planning", "title": "planejar"},
        "act-plan-ready": {"state": "PRONTA", "activity": "plan_ready", "title": "plano pronto"},
    })
    return {"version": 2, "slices": slices, "config": {}}


class KanbanTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.graph_path = self.root / "custom" / "graph.json"
        self.graph_path.parent.mkdir()
        self.write_graph(sample_graph())

    def tearDown(self):
        self.temp.cleanup()

    def write_graph(self, graph):
        self.graph_path.write_text(json.dumps(graph, ensure_ascii=False), encoding="utf-8")

    def start_server(self):
        try:
            server = painel.create_server(self.graph_path, 0)
        except PermissionError as exc:
            self.skipTest(f"sandbox bloqueou listen em 127.0.0.1; executar estes testes fora do sandbox: {exc}")
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        def stop():
            server.shutdown()
            thread.join(2)
            server.server_close()
        self.addCleanup(stop)
        return server

    def request(self, server, method, path):
        connection = http.client.HTTPConnection("127.0.0.1", server.server_port, timeout=3)
        connection.request(method, path)
        response = connection.getresponse()
        body = response.read()
        result = (response.status, dict(response.getheaders()), body)
        connection.close()
        return result

    def test_all_statuses_activity_and_legacy_cards_get_one_column(self):
        board = kanban.build_board(sample_graph(), self.graph_path)
        occurrences = {}
        card_by_id = {}
        for column in board["columns"]:
            for card in column["cards"]:
                occurrences[card["id"]] = occurrences.get(card["id"], 0) + 1
                card_by_id[card["id"]] = column["id"]
        self.assertEqual(set(occurrences), set(sample_graph()["slices"]))
        self.assertTrue(all(count == 1 for count in occurrences.values()))
        self.assertEqual(card_by_id["old-pesq"], "queue")
        self.assertEqual(card_by_id["old-plan"], "queue")
        self.assertEqual(card_by_id["old-ready"], "queue")
        self.assertEqual(card_by_id["legacy-pause"], "blocked")
        self.assertEqual(card_by_id["act-research"], "research")
        self.assertEqual(card_by_id["act-planning"], "planning")
        self.assertEqual(card_by_id["act-plan-ready"], "plan_ready")
        self.assertEqual(card_by_id["2"], "plan_ready")
        self.assertEqual(card_by_id["11"], "unknown")

    def test_motor_thinking_is_used_when_reasoning_effort_is_missing(self):
        graph = {"slices": {"1": {"state": "PRONTA", "thinking": "max"}}}
        board = kanban.build_board(graph, self.graph_path)
        card = board["columns"][0]["cards"][0]
        self.assertEqual(card["reasoning_effort"], "max")
        self.assertIn("reasoning\\_effort: max", kanban.render_markdown(board))

    def test_markdown_and_board_share_exact_column_membership(self):
        board = kanban.build_board(sample_graph(), self.graph_path, "2026-10-18T13:00:00Z")
        markdown = kanban.render_markdown(board)
        section = None
        rendered = {}
        for line in markdown.splitlines():
            if line.startswith("## "):
                section = line[3:].rsplit(" (", 1)[0]
                rendered[section] = []
            elif line.startswith("- **#"):
                rendered[section].append(line.split("**", 2)[1][1:])
        expected = {column["name"]: [card["id"] for card in column["cards"]]
                    for column in board["columns"]}
        self.assertEqual(rendered, expected)

    def test_cli_uses_custom_graph_without_copying_or_modifying_it(self):
        original = self.graph_path.read_bytes()
        output = self.root / "out" / "board.md"
        result = subprocess.run([sys.executable, str(SCRIPT), "--graph", str(self.graph_path),
                                 "--output", str(output)], cwd=self.root, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.graph_path.read_bytes(), original)
        self.assertTrue(output.is_file())
        self.assertIn(kanban._md(str(self.graph_path)), output.read_text(encoding="utf-8"))
        self.assertFalse((self.root / "graph.json").exists())
        self.assertEqual(list(output.parent.glob("*.tmp")), [])

    def test_api_reads_external_graph_again_on_each_request(self):
        server = self.start_server()
        status, _, body = self.request(server, "GET", "/board.json")
        self.assertEqual(status, 200)
        payload = json.loads(body)
        self.assertEqual(payload["source"], str(self.graph_path))
        self.assertEqual(payload["columns"][0]["cards"][0]["id"], "0")
        graph = json.loads(self.graph_path.read_text(encoding="utf-8"))
        graph["slices"]["0"]["title"] = "alterado externamente"
        self.write_graph(graph)
        status, _, body = self.request(server, "GET", "/board.json")
        self.assertEqual(status, 200)
        cards = [card for column in json.loads(body)["columns"] for card in column["cards"]]
        self.assertEqual(next(card["title"] for card in cards if card["id"] == "0"), "alterado externamente")
        self.assertEqual(self.request(server, "GET", "/graph.json")[0], 404)

    def test_allowlist_rejects_path_traversal_and_mutation_methods(self):
        server = self.start_server()
        status, _, _ = self.request(server, "GET", "/..%2F..%2Fetc%2Fpasswd")
        self.assertEqual(status, 404)
        status, headers, body = self.request(server, "POST", "/board.json")
        self.assertEqual(status, 405)
        self.assertEqual(headers.get("Allow"), "GET")
        self.assertIn("somente GET", json.loads(body)["error"])

    def test_html_content_is_text_and_graph_values_are_not_embedded(self):
        malicious = '</script><img src=x onerror="alert(1)">'
        graph = sample_graph()
        graph["slices"]["0"]["title"] = malicious
        self.write_graph(graph)
        server = self.start_server()
        _, _, body = self.request(server, "GET", "/board.json")
        payload = json.loads(body)
        card = next(card for column in payload["columns"] for card in column["cards"] if card["id"] == "0")
        self.assertEqual(card["title"], malicious)
        _, headers, html = self.request(server, "GET", "/")
        source = html.decode("utf-8")
        self.assertTrue(headers["Content-Type"].startswith("text/html"))
        self.assertNotIn(malicious, source)
        self.assertNotIn("innerHTML", source)
        self.assertIn("textContent", source)

    def test_corrupt_graph_returns_visible_json_error(self):
        self.graph_path.write_text("{truncated", encoding="utf-8")
        server = self.start_server()
        status, headers, body = self.request(server, "GET", "/board.json")
        payload = json.loads(body)
        self.assertEqual(status, 500)
        self.assertTrue(headers["Content-Type"].startswith("application/json"))
        self.assertIn("falha ao ler o grafo", payload["error"])
        self.assertNotIn("truncated", payload["error"])
        self.assertEqual(payload["source"], str(self.graph_path))

    def test_pr_link_requires_numeric_pr_and_secure_tracker_url(self):
        graph = {"config": {"tracker_url": "https://github.com/acme/project"}, "slices": {
            "safe": {"state": "PR_ABERTO", "pr": "27"},
            "bad-pr": {"state": "PR_ABERTO", "pr": "27/../../evil"},
        }}
        board = kanban.build_board(graph, self.graph_path)
        cards = {card["id"]: card for column in board["columns"] for card in column["cards"]}
        self.assertEqual(cards["safe"]["pr_url"], "https://github.com/acme/project/pull/27")
        self.assertIsNone(cards["bad-pr"]["pr_url"])
        self.assertTrue(cards["bad-pr"]["pr_invalid"])
        graph["config"]["tracker_url"] = "https://github.com.evil.test/acme/project"
        board = kanban.build_board(graph, self.graph_path)
        cards = {card["id"]: card for column in board["columns"] for card in column["cards"]}
        self.assertIsNone(cards["safe"]["pr_url"])

    def test_markdown_escapes_graph_markup_but_keeps_safe_pr_link(self):
        title = "![click](javascript:alert(1)) <img src=x>"
        graph = {"config": {"tracker_url": "https://github.com/acme/project"}, "slices": {
            "1": {"state": "PR_ABERTO", "title": title, "pr": 27},
        }}
        markdown = kanban.render_markdown(kanban.build_board(graph, self.graph_path))
        self.assertIn(r"\!\[click\]\(javascript:alert\(1\)\) &lt;img src=x&gt;", markdown)
        self.assertIn("[PR #27](https://github.com/acme/project/pull/27)", markdown)


if __name__ == "__main__":
    unittest.main(verbosity=2)
