"""Programmable in-process fake of the WMS v1 HTTP surface.

Zero dependencies (stdlib http.server) and it exercises the real requests
socket path — timeouts and connection-refused behave like the real network,
which adapter-level mocking cannot show. Tests enqueue canned responses FIFO
and assert on the recorded requests afterwards.
"""

from __future__ import annotations

import http.server
import json
import threading
import time
from dataclasses import dataclass, field


@dataclass
class Received:
    method: str
    path: str
    headers: dict
    body: dict | None


@dataclass
class Canned:
    status: int
    body: bytes = b""
    content_type: str = "application/json"
    delay_s: float = 0.0


@dataclass
class FakeWms:
    responses: list[Canned] = field(default_factory=list)
    requests: list[Received] = field(default_factory=list)

    def __post_init__(self):
        fake = self

        class Handler(http.server.BaseHTTPRequestHandler):
            def _serve(self):
                length = int(self.headers.get("Content-Length") or 0)
                raw = self.rfile.read(length) if length else b""
                fake.requests.append(Received(
                    self.command, self.path, dict(self.headers),
                    json.loads(raw) if raw else None,
                ))
                if not fake.responses:
                    self.send_response(500)
                    self.end_headers()
                    return
                canned = fake.responses.pop(0)
                if canned.delay_s:
                    time.sleep(canned.delay_s)
                self.send_response(canned.status)
                if canned.body:
                    self.send_header("Content-Type", canned.content_type)
                self.send_header("Content-Length", str(len(canned.body)))
                self.end_headers()
                self.wfile.write(canned.body)

            do_GET = do_POST = _serve

            def log_message(self, *args):
                pass

        self._httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self._thread = threading.Thread(target=self._httpd.serve_forever, daemon=True)
        self._thread.start()

    @property
    def base_url(self) -> str:
        return f"http://127.0.0.1:{self._httpd.server_address[1]}"

    def enqueue(self, status: int, doc: dict | None = None, *,
                problem: bool = False, delay_s: float = 0.0) -> None:
        body = json.dumps(doc).encode() if doc is not None else b""
        content_type = "application/problem+json" if problem else "application/json"
        self.responses.append(Canned(status, body, content_type, delay_s))

    def enqueue_problem(self, status: int, code: str, detail: str = "") -> None:
        self.enqueue(status, {
            "type": f"https://warehouse.example/problems/{code.lower()}",
            "title": code.replace("_", " ").title(),
            "status": status,
            "code": code,
            "detail": detail or code,
            "correlationId": "00000000-0000-0000-0000-000000000000",
        }, problem=True)

    def close(self) -> None:
        self._httpd.shutdown()
        self._httpd.server_close()
        self._thread.join(timeout=2)
