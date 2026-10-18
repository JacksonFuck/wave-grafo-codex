#!/usr/bin/env python3
"""Painel HTTP local, somente leitura, para um graph.json escolhido."""
import argparse
import json
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit

from kanban import build_board, read_graph, render_markdown


ASSET = Path(__file__).resolve().parent.parent / "assets" / "index.html"


class PanelHandler(BaseHTTPRequestHandler):
    graph_path: Path
    server_version = "KanbanLocal/1.0"

    def _send(self, status, content_type, body, extra_headers=None):
        encoded = body.encode("utf-8") if isinstance(body, str) else body
        self.send_response(status)
        for name, value in (extra_headers or {}).items():
            self.send_header(name, value)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(encoded)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        if content_type.startswith("text/html"):
            self.send_header("Content-Security-Policy", "default-src 'none'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; connect-src 'self'; base-uri 'none'")
            self.send_header("Referrer-Policy", "no-referrer")
        self.end_headers()
        self.wfile.write(encoded)

    def _json(self, status, value, extra_headers=None):
        self._send(status, "application/json; charset=utf-8",
                   json.dumps(value, ensure_ascii=False).encode("utf-8"), extra_headers)

    def _error(self, status, message):
        self._json(status, {"error": str(message), "source": str(self.graph_path)})

    def _board(self):
        graph = read_graph(self.graph_path)
        return build_board(graph, self.graph_path)

    def do_GET(self):
        path = urlsplit(self.path).path
        if path == "/":
            try:
                self._send(200, "text/html; charset=utf-8", ASSET.read_text(encoding="utf-8"))
            except OSError as exc:
                self._error(500, f"não foi possível ler o painel: {exc}")
            return
        if path == "/board.json":
            try:
                self._json(200, self._board())
            except (OSError, ValueError, json.JSONDecodeError) as exc:
                self._error(500, f"falha ao ler o grafo: {exc}")
            return
        if path == "/kanban.md":
            try:
                self._send(200, "text/markdown; charset=utf-8", render_markdown(self._board()))
            except (OSError, ValueError, json.JSONDecodeError) as exc:
                self._error(500, f"falha ao gerar Markdown: {exc}")
            return
        self._error(404, "rota não encontrada")

    def _method_not_allowed(self):
        self._json(405, {"error": "somente GET é permitido", "source": str(self.graph_path)},
                   {"Allow": "GET"})

    def do_HEAD(self):
        self._method_not_allowed()

    def do_POST(self):
        self._method_not_allowed()

    def do_PUT(self):
        self._method_not_allowed()

    def do_PATCH(self):
        self._method_not_allowed()

    def do_DELETE(self):
        self._method_not_allowed()


def make_handler(graph_path):
    graph_path = Path(graph_path)
    if not graph_path.is_absolute():
        raise ValueError("--graph precisa ser um caminho absoluto")

    class Handler(PanelHandler):
        pass
    Handler.graph_path = graph_path
    return Handler


def create_server(graph_path, port=8766):
    return ThreadingHTTPServer(("127.0.0.1", port), make_handler(graph_path))


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--graph", required=True, help="caminho absoluto do graph.json de origem")
    parser.add_argument("--port", type=int, default=8766)
    args = parser.parse_args(argv)
    if not 1 <= args.port <= 65535:
        parser.error("--port deve estar entre 1 e 65535")
    try:
        server = create_server(args.graph, args.port)
    except (OSError, ValueError) as exc:
        print(f"Erro ao iniciar painel: {exc}", file=sys.stderr)
        return 1
    print(f"Painel em http://127.0.0.1:{args.port}/ · fonte: {Path(args.graph)}", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
