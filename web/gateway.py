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
            response = urlopen(request, timeout=20)
        except HTTPError as error:
            response = error
        except URLError as error:
            self.send_error(502, f"Upstream unavailable: {error.reason}")
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

    def do_OPTIONS(self):
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET,POST,OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()


if __name__ == "__main__":
    port = int(os.getenv("GATEWAY_PORT", "8080"))
    server = ThreadingHTTPServer(("0.0.0.0", port), GatewayHandler)
    print(f"SmartAgri gateway listening on 0.0.0.0:{port}", flush=True)
    server.serve_forever()
