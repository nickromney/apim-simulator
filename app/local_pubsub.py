"""Small HTTP pub/sub service for the local policy lab; runs in its own container."""

from __future__ import annotations

import hmac
import json
import os
import re
import sqlite3
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, unquote, urlsplit


class BrokerStore:
    def __init__(self, path: str = ":memory:") -> None:
        self.lock = threading.RLock()
        self.db = sqlite3.connect(path, check_same_thread=False)
        self.db.executescript("""
            CREATE TABLE IF NOT EXISTS secrets (name TEXT PRIMARY KEY, value TEXT);
            CREATE TABLE IF NOT EXISTS entities (kind TEXT, name TEXT, PRIMARY KEY(kind,name));
            CREATE TABLE IF NOT EXISTS subscriptions (topic TEXT, name TEXT, PRIMARY KEY(topic,name));
            CREATE TABLE IF NOT EXISTS messages (id INTEGER PRIMARY KEY, kind TEXT, entity TEXT,
                consumer TEXT, expires REAL, payload TEXT);
        """)

    @staticmethod
    def name(value: str) -> str:
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,199}", value):
            raise ValueError("Use a name containing letters, digits, dots, underscores or hyphens")
        return value

    def create(self, kind: str, name: str, subscription: str | None = None) -> None:
        self.name(name)
        with self.lock, self.db:
            if subscription is None:
                self.db.execute("INSERT OR IGNORE INTO entities VALUES (?,?)", (kind, name))
            else:
                self.name(subscription)
                self.require("topics", name)
                self.db.execute("INSERT OR IGNORE INTO subscriptions VALUES (?,?)", (name, subscription))

    def require(self, kind: str, name: str) -> None:
        if self.db.execute("SELECT 1 FROM entities WHERE kind=? AND name=?", (kind, name)).fetchone() is None:
            raise LookupError("Queue or topic does not exist; create it before publishing")

    def publish(self, kind: str, name: str, message: dict) -> int:
        self.name(name)
        if not isinstance(message.get("payload"), str):
            raise ValueError("Message payload must be a string")
        ttl = message.get("ttl_seconds")
        if ttl is not None and (not isinstance(ttl, (float, int)) or ttl <= 0):
            raise ValueError("Message TTL must be positive")
        with self.lock, self.db:
            self.require(kind, name)
            self.db.execute("DELETE FROM messages WHERE expires IS NOT NULL AND expires <= ?", (time.time(),))
            consumers = (
                [""]
                if kind == "queues"
                else [r[0] for r in self.db.execute("SELECT name FROM subscriptions WHERE topic=?", (name,))]
            )
            count = self.db.execute("SELECT COUNT(*) FROM messages").fetchone()[0]
            if count + len(consumers) > 10000:
                raise OverflowError("Local broker is full; consume pending messages")
            expiry = time.time() + ttl if ttl is not None else None
            for consumer in consumers:
                self.db.execute(
                    "INSERT INTO messages(kind,entity,consumer,expires,payload) VALUES(?,?,?,?,?)",
                    (kind, name, consumer, expiry, json.dumps(message)),
                )
            return len(consumers)

    def secret(self, name: str, value: str | None = None) -> str:
        self.name(name)
        with self.lock, self.db:
            if value is not None:
                if not isinstance(value, str) or not 1 <= len(value) <= 4096:
                    raise ValueError("Secret value must contain 1 to 4096 characters")
                self.db.execute("INSERT OR REPLACE INTO secrets VALUES (?,?)", (name, value))
            row = self.db.execute("SELECT value FROM secrets WHERE name=?", (name,)).fetchone()
            if row is None:
                raise LookupError("Secret does not exist")
            return row[0]

    def consume(self, kind: str, name: str, consumer: str = "") -> dict | None:
        with self.lock, self.db:
            self.require(kind, name)
            if (
                kind == "topics"
                and not self.db.execute(
                    "SELECT 1 FROM subscriptions WHERE topic=? AND name=?", (name, consumer)
                ).fetchone()
            ):
                raise LookupError("Subscription does not exist")
            self.db.execute("DELETE FROM messages WHERE expires IS NOT NULL AND expires <= ?", (time.time(),))
            row = self.db.execute(
                "SELECT id,payload FROM messages WHERE kind=? AND entity=? AND consumer=? ORDER BY id LIMIT 1",
                (kind, name, consumer),
            ).fetchone()
            if row is None:
                return None
            self.db.execute("DELETE FROM messages WHERE id=?", (row[0],))
            return json.loads(row[1])


class Handler(BaseHTTPRequestHandler):
    store: BrokerStore
    admin_key: str
    sender_key: str
    reader_key: str

    def log_message(self, *args) -> None:
        return

    def respond(self, status: int, body: dict) -> None:
        content = json.dumps(body).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(content)))
        self.end_headers()
        self.wfile.write(content)

    def dispatch(self) -> None:
        parts = [unquote(p) for p in urlsplit(self.path).path.split("/") if p]
        if parts == ["health"]:
            self.respond(200, {"status": "healthy"})
            return
        if parts == ["introspection"] and self.command == "POST":
            self.introspection()
            return
        publishing = self.command == "POST" and len(parts) == 3 and parts[-1] == "messages"
        key = self.headers.get("Authorization", "").removeprefix("Bearer ")
        reading_secret = self.command == "GET" and len(parts) in {2, 3} and parts[0] == "secrets"
        allowed = (
            hmac.compare_digest(key, self.admin_key)
            or (publishing and parts[0] in {"queues", "topics"} and hmac.compare_digest(key, self.sender_key))
            or (reading_secret and hmac.compare_digest(key, self.reader_key))
        )
        if not allowed:
            self.respond(403, {"detail": "A local broker access key is required"})
            return
        try:
            status, body = self.operation(parts)
        except (ValueError, json.JSONDecodeError) as exc:
            status, body = 400, {"detail": str(exc)}
        except LookupError as exc:
            status, body = 404, {"detail": str(exc)}
        except OverflowError as exc:
            status, body = 429, {"detail": str(exc)}
        self.respond(status, body)

    def introspection(self) -> None:
        if self.headers.get("Authorization") != "Basic dXNlcm5hbWU6cGFzc3dvcmQ=":
            self.respond(401, {"active": False})
            return
        length = int(self.headers.get("Content-Length", "0"))
        if not 0 <= length <= 8192:
            self.respond(400, {"detail": "Invalid introspection body size"})
            return
        token = parse_qs(self.rfile.read(length).decode("utf-8", errors="replace")).get("token", [""])[0]
        self.respond(200, {"active": token == "active-local-token"})

    def operation(self, parts: list[str]) -> tuple[int, dict]:
        if parts and parts[0] == "secrets":
            return self.secret_operation(parts)
        if not parts or parts[0] not in {"queues", "topics"}:
            return 404, {"detail": "Unknown broker endpoint"}
        return self.entity_operation(parts)

    def read_object(self, maximum: int, size_error: str) -> dict:
        length = int(self.headers.get("Content-Length", "0"))
        if not 0 < length <= maximum:
            raise ValueError(size_error)
        body = json.loads(self.rfile.read(length))
        if not isinstance(body, dict):
            raise ValueError("Request body must be an object")
        return body

    def secret_operation(self, parts: list[str]) -> tuple[int, dict]:
        if self.command == "GET" and len(parts) in {2, 3}:
            return 200, {"value": self.store.secret(parts[1])}
        if self.command == "PUT" and len(parts) == 2:
            body = self.read_object(32768, "Secret request must be between 1 byte and 32 KiB")
            if not isinstance(body.get("value"), str):
                raise ValueError("Supply a string secret value")
            self.store.secret(parts[1], body["value"])
            return 200, {"updated": True}
        return 404, {"detail": "Unknown secret endpoint"}

    def create_operation(self, parts: list[str]) -> tuple[int, dict]:
        if len(parts) == 2:
            self.store.create(*parts)
            return 200, {"created": True}
        if len(parts) == 4 and parts[0] == "topics" and parts[2] == "subscriptions":
            self.store.create(parts[0], parts[1], parts[3])
            return 200, {"created": True}
        return 404, {"detail": "Unknown broker endpoint"}

    def entity_operation(self, parts: list[str]) -> tuple[int, dict]:
        if self.command == "PUT":
            return self.create_operation(parts)
        if self.command == "POST" and len(parts) == 3 and parts[2] == "messages":
            message = self.read_object(2 * 1024 * 1024, "Message must be between 1 byte and 2 MiB")
            return 201, {"delivered": self.store.publish(parts[0], parts[1], message)}
        if self.command == "POST" and parts[-1] == "consume":
            return self.consume_operation(parts)
        return 404, {"detail": "Unknown broker endpoint"}

    def consume_operation(self, parts: list[str]) -> tuple[int, dict]:
        if len(parts) == 3 and parts[0] == "queues":
            return 200, {"message": self.store.consume(parts[0], parts[1])}
        if len(parts) == 5 and parts[0] == "topics" and parts[2] == "subscriptions":
            return 200, {"message": self.store.consume(parts[0], parts[1], parts[3])}
        return 404, {"detail": "Unknown broker endpoint"}

    do_GET = dispatch
    do_PUT = dispatch
    do_POST = dispatch


def build_handler(store: BrokerStore, admin_key: str, sender_key: str, reader_key: str = "local-vault-reader"):
    if not admin_key or not sender_key or not reader_key:
        raise ValueError("Configure nonempty broker admin and sender access keys")
    return type(
        "ConfiguredBrokerHandler",
        (Handler,),
        {"store": store, "admin_key": admin_key, "sender_key": sender_key, "reader_key": reader_key},
    )


if __name__ == "__main__":
    store = BrokerStore(os.environ.get("BROKER_DATABASE", "/tmp/pubsub.sqlite"))
    handler = build_handler(
        store, os.environ["BROKER_ADMIN_KEY"], os.environ["BROKER_SENDER_KEY"], os.environ["BROKER_SECRET_READER_KEY"]
    )
    ThreadingHTTPServer(("0.0.0.0", int(os.environ.get("PORT", "8081"))), handler).serve_forever()
