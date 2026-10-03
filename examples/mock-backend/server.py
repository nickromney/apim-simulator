from __future__ import annotations

import base64
import json
import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlsplit

PETS = [
    {"id": 1, "name": "local-cat", "photoUrls": [], "status": "pending", "tags": [{"id": 1, "name": "cat"}]},
    {"id": 2, "name": "local-dog", "photoUrls": [], "status": "available", "tags": [{"id": 2, "name": "dog"}]},
    {"id": 3, "name": "local-adopted-cat", "photoUrls": [], "status": "sold", "tags": [{"id": 1, "name": "cat"}]},
]


def _decode_body(data: bytes) -> str:
    if not data:
        return ""
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        return base64.b64encode(data).decode("ascii")


class Handler(BaseHTTPRequestHandler):
    server_version = "apim-simulator-mock-backend/0.1"

    def log_message(self, format: str, *args) -> None:
        return

    def _read_body(self) -> bytes:
        length = int(self.headers.get("content-length", "0") or "0")
        if length <= 0:
            return b""
        return self.rfile.read(length)

    def _write_json(self, status_code: int, payload: dict | list[dict]) -> None:
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status_code)
        self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _payload(self, body: bytes) -> dict:
        return {
            "ok": True,
            "method": self.command,
            "path": self.path,
            "headers": dict(self.headers.items()),
            "body": _decode_body(body),
        }

    def _handle(self) -> None:
        body = self._read_body()
        if urlsplit(self.path).path.endswith("/fail"):
            self._write_json(503, {"status": "unavailable"})
            return
        if self.command == "GET" and (self._metrics_get() or self._graphql_get() or self._petstore_get()):
            return
        if self.path.endswith("/health") or self.path.endswith("/startup"):
            self._write_json(200, {"status": "ok", "path": self.path})
            return
        self._write_json(200, self._payload(body))

    def _metrics_get(self) -> bool:
        url = urlsplit(self.path)
        if not url.path.startswith("/api/metrics/"):
            return False
        metric = url.path.rsplit("/", 1)[-1]
        values = {"salesdata": 1200, "materiallevels": 40, "throughput": 75, "accidentdata": 0}
        if metric not in values:
            self._write_json(404, {"error": "Unknown metric"})
        else:
            query = parse_qs(url.query)
            self._write_json(
                200,
                {
                    "metric": metric,
                    "from": query.get("from", [""])[-1],
                    "to": query.get("to", [""])[-1],
                    "value": values[metric],
                },
            )
        return True

    def _graphql_get(self) -> bool:
        path = urlsplit(self.path).path
        if path.startswith("/api/comment/"):
            self._write_json(200, {"id": path.rsplit("/", 1)[-1], "text": "Local comment", "private": "hidden"})
        elif path.startswith("/api/blog/"):
            self._write_json(200, [{"id": "7", "text": "Local comment"}])
        elif path.startswith("/api/blog-record/"):
            self._write_json(200, {"id": path.rsplit("/", 1)[-1], "title": "Local blog"})
        else:
            return False
        return True

    def _petstore_get(self) -> bool:
        url = urlsplit(self.path)
        query = parse_qs(url.query)
        if url.path == "/api/v3/pet/findByStatus":
            statuses = query.get("status", [])
            self._write_json(200, [pet for pet in PETS if pet["status"] in statuses])
        elif url.path == "/api/v3/pet/findByTags":
            tags = set(query.get("tags", []))
            self._write_json(200, [pet for pet in PETS if tags & {tag["name"] for tag in pet["tags"]}])
        elif url.path.startswith("/api/v3/pet/"):
            pet = next((pet for pet in PETS if str(pet["id"]) == url.path.rsplit("/", 1)[-1]), None)
            self._write_json(200 if pet else 404, pet or {"message": "Pet not found"})
        else:
            return False
        return True

    def do_GET(self) -> None:
        self._handle()

    def do_POST(self) -> None:
        self._handle()

    def do_PUT(self) -> None:
        self._handle()

    def do_PATCH(self) -> None:
        self._handle()

    def do_DELETE(self) -> None:
        self._handle()

    def do_OPTIONS(self) -> None:
        self.send_response(204)
        self.send_header("access-control-allow-origin", "*")
        self.send_header("access-control-allow-methods", "GET,POST,PUT,PATCH,DELETE,OPTIONS")
        self.send_header("access-control-allow-headers", "*")
        self.end_headers()


def main() -> None:
    port = int(os.getenv("PORT", "8080"))
    server = ThreadingHTTPServer(("0.0.0.0", port), Handler)
    server.serve_forever()


if __name__ == "__main__":
    main()
