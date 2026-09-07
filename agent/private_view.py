"""Local-only, allowlisted private graph viewer."""

from __future__ import annotations

import mimetypes
import threading
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit

from .intake import IntakeConfig, load_private_master


def make_server(config: IntakeConfig, host: str = "127.0.0.1", port: int = 8765) -> ThreadingHTTPServer:
    if host not in {"127.0.0.1", "localhost", "::1"}:
        raise ValueError("The private viewer may bind only to the local computer")
    root = Path(__file__).resolve().parents[1]
    assets = {
        "/": root / "index.html",
        "/index.html": root / "index.html",
        "/app.js": root / "app.js",
        "/styles.css": root / "styles.css",
        "/schemas/graph.schema.json": root / "schemas" / "graph.schema.json",
    }

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802
            route = urlsplit(self.path).path
            if route == "/data/graph.json":
                import json
                from .safety import locked, assert_readable
                try:
                    with locked(config):
                        assert_readable(config)
                        body = json.dumps(load_private_master(config), indent=2, ensure_ascii=False).encode("utf-8") + b"\n"
                except (OSError, RuntimeError, ValueError):
                    self.send_error(503, 'Private workflow busy or recovery required')
                    return
                content_type = "application/json"
            elif route in assets:
                body = assets[route].read_bytes()
                content_type = mimetypes.guess_type(str(assets[route]))[0] or "application/octet-stream"
            else:
                self.send_error(404)
                return
            self.send_response(200)
            self.send_header("Content-Type", f"{content_type}; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Content-Security-Policy", "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; connect-src 'self'; img-src 'self' data:; frame-ancestors 'none'; base-uri 'none'")
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, _format: str, *_args: object) -> None:
            return

    return ThreadingHTTPServer((host, port), Handler)


def serve(config: IntakeConfig, host: str = "127.0.0.1", port: int = 8765, open_browser: bool = True) -> None:
    server = make_server(config, host, port)
    url = f"http://{host}:{server.server_port}/"
    print(f"Private graph viewer: {url}")
    if open_browser:
        threading.Timer(0.2, lambda: webbrowser.open(url)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
