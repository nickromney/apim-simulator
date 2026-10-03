"""Read-only HTTP serialization for the local advanced logging guide."""

from http import HTTPStatus
from urllib.parse import urlencode

_SENSITIVE = {
    "authorization",
    "proxy-authorization",
    "ocp-apim-subscription-key",
    "apim-debug-authorization",
    "x-apim-tenant-key",
}


def _serialize(first_line: str, headers, body: bytes, limit: int) -> str:
    if not 0 <= limit <= 200 * 1024:
        raise ValueError("HTTP log body limit must be between 0 and 200 KiB")
    lines = []
    for name, value in headers.items():
        if name.lower() in _SENSITIVE:
            continue
        values = value if isinstance(value, (list, tuple)) else [value]
        lines.append(f"{name}: {', '.join(str(item) for item in values)}")
    header_text = "\r\n".join(lines)
    return (
        first_line + (header_text + "\r\n" if lines else "") + "\r\n" + body[:limit].decode("utf-8", errors="replace")
    )


def request_http_message(method: str, path: str, headers, body: bytes, limit: int = 1024, *, query=None) -> str:
    if query:
        path += "?" + urlencode(query, doseq=True)
    return _serialize(f"{method} {path} HTTP/1.1\r\n", headers, body, limit)


def response_http_message(status_code: int, headers, body: bytes, limit: int = 1024) -> str:
    try:
        reason = HTTPStatus(status_code).phrase
    except ValueError:
        reason = ""
    return _serialize(f"HTTP/1.1 {status_code} {reason}\r\n", headers, body, limit)
