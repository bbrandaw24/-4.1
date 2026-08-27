#!/usr/bin/env python3
"""Serve the dashboard and proxy API/AI calls through the same origin."""

from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen
import shutil
import os


ROOT = Path(__file__).resolve().parent
UPSTREAMS = {
    "/api": os.getenv("API_UPSTREAM", "http://127.0.0.1:8010"),
    "/ai": os.getenv("AI_UPSTREAM", "http://127.0.0.1:8001"),
}


class GatewayHandler(SimpleHTTPRequestHandler):
    protocol_version = "HTTP/1.0"

    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(ROOT), **kwargs)

    def _upstream(self):
        for prefix, target in UPSTREAMS.items():
            if self.path == prefix or self.path.startswith(prefix + "/"):
                return target + (self.path[len(prefix):] if prefix == "/ai" else self.path)
        return None

    def _proxy(self, method):
        target = self._upstream()
        if not target:
            self.send_error(404, "Unknown gateway route")
            return
        body = None
        if method in {"POST", "PUT", "PATCH"}:
            length = int(self.headers.get("Content-Length", "0"))
            body = self.rfile.read(length) if length else None
        request = Request(target, data=body, method=method)
        for name, value in self.headers.items():
            if name.lower() not in {"host", "content-length", "connection"}:
                request.add_header(name, value)
        try:
            # Luna / LLM answers can take 20-45s with medium reasoning, so the
            # proxy timeout is generous and configurable (default 60s).
            response = urlopen(request, timeout=int(os.getenv("GATEWAY_PROXY_TIMEOUT", "60")))
        except HTTPError as error:
            response = error
        except (URLError, TimeoutError) as error:
            self.send_error(502, f"Upstream unavailable: {getattr(error, 'reason', error)}")
            return
        self.send_response(response.status)
        for name, value in response.headers.items():
            if name.lower() not in {"connection", "transfer-encoding", "content-encoding"}:
                self.send_header(name, value)
        self.end_headers()
        shutil.copyfileobj(response, self.wfile)
        response.close()

    def do_GET(self):
        if self._upstream():
            self._proxy("GET")
        else:
            super().do_GET()

    def do_POST(self):
        self._proxy("POST")

    def do_PUT(self):
        self._proxy("PUT")

    def do_PATCH(self):
        self._proxy("PATCH")

    def do_DELETE(self):
        self._proxy("DELETE")

    def do_OPTIONS(self):
        # For upstream routes, proxy the preflight to the API so cross-origin
        # callers (e.g. GitHub Pages) get the same CORS headers the API emits.
        if self._upstream():
            self._proxy("OPTIONS")
            return
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET,POST,PUT,PATCH,DELETE,OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type, Authorization")
        self.end_headers()

    def end_headers(self):
        # Prevent the browser from caching HTML/JS so freshly-deployed fixes
        # are picked up immediately without a hard refresh.
        self.send_header("Cache-Control", "no-store, no-cache, must-revalidate")
        self.send_header("Pragma", "no-cache")
        self.send_header("Expires", "0")
        super().end_headers()


if __name__ == "__main__":
    port = int(os.getenv("GATEWAY_PORT", "8080"))
    server = ThreadingHTTPServer(("0.0.0.0", port), GatewayHandler)
    print(f"SmartAgri gateway listening on 0.0.0.0:{port}", flush=True)
    server.serve_forever()
