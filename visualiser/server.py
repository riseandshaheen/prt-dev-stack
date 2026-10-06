#!/usr/bin/env python3
"""PRT Dev Stack visualiser: one read-only view of the chain and every node on it.

    python3 server.py                       # env-var defaults, same as the old script
    VIS_CONFIG=visualiser.json python3 server.py

API (all JSON, read-only):
    GET /api/v1/overview                    chain, nodes, apps with recent epochs, alerts
    GET /api/v1/apps/{address}              app contracts and every epoch
    GET /api/v1/apps/{address}/epochs/{n}   inputs, three-way checks, dispute tree, transactions
    GET /api/v1/apps/{address}/transactions every transaction sent to the app's contracts
    GET /api/v1/stream                      server-sent events: {"version": n} on each change
    GET /healthz                            200 when the collector and chain are current
"""

from __future__ import annotations

import json
import re
import sys
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

from vis import config as config_mod
from vis.collector import Collector

STATIC = Path(__file__).resolve().parent / "static"
TYPES = {".html": "text/html; charset=utf-8", ".js": "text/javascript; charset=utf-8",
         ".css": "text/css; charset=utf-8", ".svg": "image/svg+xml"}
SECURITY = {
    "Content-Security-Policy": "default-src 'self'; img-src 'self' data:; style-src 'self'; "
                               "script-src 'self'; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'",
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "no-referrer",
}
ADDRESS = r"0x[0-9a-fA-F]{40}"
ROUTES = [
    (re.compile(rf"^/api/v1/apps/({ADDRESS})/epochs/(\d+)$"), "epoch"),
    (re.compile(rf"^/api/v1/apps/({ADDRESS})/transactions$"), "ledger"),
    (re.compile(rf"^/api/v1/apps/({ADDRESS})$"), "app"),
]


def make_handler(collector: Collector):
    class Handler(BaseHTTPRequestHandler):
        server_version = "prt-visualiser"
        protocol_version = "HTTP/1.1"

        def log_message(self, fmt, *args):  # quiet; errors surface in the UI
            return

        def do_GET(self):  # noqa: N802
            path = urlparse(self.path).path
            if path == "/healthz":
                ok, body = collector.healthy()
                return self._json(body, 200 if ok else 503)
            if path == "/api/v1/stream":
                return self._stream()
            if path == "/api/v1/overview":
                snap = collector.snapshot["overview"]
                return self._json(snap) if snap else self._json({"error": "warming up"}, 503)
            for pattern, kind in ROUTES:
                match = pattern.match(path)
                if not match:
                    continue
                address = match.group(1).lower()
                snap = collector.snapshot
                if kind == "app":
                    body = snap["apps"].get(address)
                elif kind == "ledger":
                    body = snap["ledgers"].get(address)
                else:
                    body = snap["epochs"].get((address, int(match.group(2))))
                if body is None:
                    return self._json({"error": "not found"}, 404)
                return self._json(body)
            if path in ("/api/status", "/api/reference"):
                return self._json({"error": "moved", "use": "/api/v1/overview"}, 410)
            return self._static(path)

        def _headers(self, status: int, ctype: str, length: int | None = None, cache: str = "no-store"):
            self.send_response(status)
            self.send_header("Content-Type", ctype)
            self.send_header("Cache-Control", cache)
            for key, value in SECURITY.items():
                self.send_header(key, value)
            if length is not None:
                self.send_header("Content-Length", str(length))
            self.end_headers()

        def _json(self, payload, status: int = 200):
            body = json.dumps(payload, default=str).encode()
            self._headers(status, "application/json", len(body))
            self.wfile.write(body)

        def _static(self, path: str):
            name = "index.html" if path in ("", "/") else path.lstrip("/")
            target = (STATIC / name).resolve()
            if STATIC not in target.parents or not target.is_file() or target.suffix not in TYPES:
                # Unknown paths get the app shell so deep links like /dispute still land somewhere.
                target = STATIC / "index.html"
            body = target.read_bytes()
            self._headers(200, TYPES[target.suffix], len(body), "no-cache")
            self.wfile.write(body)

        def _stream(self):
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Accel-Buffering", "no")
            for key, value in SECURITY.items():
                self.send_header(key, value)
            self.end_headers()
            seen = -1
            try:
                while True:
                    version = collector.wait_for_change(seen, timeout=15)
                    if version != seen:
                        seen = version
                        self.wfile.write(f"data: {json.dumps({'version': version})}\n\n".encode())
                    else:
                        self.wfile.write(b": keep-alive\n\n")
                    self.wfile.flush()
            except (BrokenPipeError, ConnectionResetError, TimeoutError):
                return

    return Handler


def main() -> None:
    cfg = config_mod.load(sys.argv[1] if len(sys.argv) > 1 else None)
    collector = Collector(cfg)
    collector.start()
    server = ThreadingHTTPServer((cfg.host, cfg.port), make_handler(collector))
    server.daemon_threads = True
    nodes = ", ".join(f"{n.label} ({n.kind})" for n in cfg.nodes) or "none"
    print(f"visualiser on http://{cfg.host}:{cfg.port}  chain {cfg.chain_rpc}  nodes: {nodes}", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        collector.stop()


if __name__ == "__main__":
    main()
