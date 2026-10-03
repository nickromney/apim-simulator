"""Real HTTPS backend requiring a client certificate and a signed workload token."""

from __future__ import annotations

import hashlib
import json
import os
import ssl
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import jwt
from cryptography import x509
from cryptography.hazmat.primitives import hashes

from app.workload_identity import verify_workload_token


def server_context(directory: Path) -> ssl.SSLContext:
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.minimum_version = ssl.TLSVersion.TLSv1_2
    context.load_cert_chain(
        os.environ.get("TLS_CERT_FILE", directory / "backend.pem"),
        os.environ.get("TLS_KEY_FILE", directory / "backend-key.pem"),
    )
    context.load_verify_locations(cafile=os.environ.get("TLS_CA_FILE", str(directory / "ca.pem")))
    context.load_verify_locations(cafile=os.environ.get("TLS_CRL_FILE", str(directory / "ca.crl.pem")))
    context.verify_flags |= ssl.VERIFY_CRL_CHECK_LEAF
    context.verify_mode = ssl.CERT_REQUIRED
    return context


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def handle(self):
        try:
            super().handle()
        except (ConnectionResetError, BrokenPipeError):
            return

    def do_GET(self):
        self._authenticated_response()

    def do_POST(self):
        body = self.rfile.read(int(self.headers.get("Content-Length", "0")))
        self._authenticated_response(body)

    def _authenticated_response(self, body=b""):
        directory = Path(os.environ.get("CERTS_DIRECTORY", "/certs"))
        token = self.headers.get("Authorization", "").removeprefix("Bearer ")
        try:
            claims = verify_workload_token(
                token,
                public_key_file=os.environ.get("WORKLOAD_PUBLIC_KEY_FILE", str(directory / "workload-public.pem")),
                issuer=os.environ.get("WORKLOAD_ISSUER", "https://apim.local/workload-identity"),
                audience=os.environ.get("WORKLOAD_AUDIENCE", "https://backend.local"),
                allowed_identities=os.environ.get("WORKLOAD_ALLOWED_IDENTITIES", "system-assigned").split(","),
            )
        except (jwt.InvalidTokenError, OSError):
            self._reply(401, {"error": "Valid signed workload identity required"})
            return
        certificate = x509.load_der_x509_certificate(self.connection.getpeercert(binary_form=True))
        if self.path.startswith("/secrets/"):
            self._secret_response()
            return
        try:
            payload = json.loads(body) if body else None
        except ValueError:
            payload = body.decode("utf-8", errors="replace")
        self._reply(
            200,
            {
                "ok": True,
                "identity": claims["sub"],
                "client_thumbprint": certificate.fingerprint(hashes.SHA1()).hex().upper(),
                "path": self.path,
                "body": payload,
                "vault_secret_sha256": hashlib.sha256(self.headers.get("X-Vault-Secret", "").encode()).hexdigest(),
            },
        )

    def _secret_response(self):
        path = os.environ.get("VAULT_SECRETS_FILE")
        name = self.path.split("/", 3)[2]
        try:
            value = json.loads(Path(path).read_text())[name] if path else None
        except (OSError, ValueError, KeyError):
            value = None
        if not isinstance(value, str):
            self._reply(404, {"error": "Secret not found"})
            return
        self._reply(200, {"value": value})

    def _reply(self, status, value):
        body = json.dumps(value).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format, *args):
        return


def main():
    server = ThreadingHTTPServer(("0.0.0.0", int(os.environ.get("PORT", "8443"))), Handler)
    server.socket = server_context(Path(os.environ.get("CERTS_DIRECTORY", "/certs"))).wrap_socket(
        server.socket, server_side=True
    )
    server.serve_forever()


if __name__ == "__main__":
    main()
