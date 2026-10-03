from __future__ import annotations

import ast
import base64
import datetime
import json
import re
import uuid
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any
from urllib.parse import quote_plus

import jwt

if TYPE_CHECKING:
    from app.policy import PolicyRequest


class ExpressionMap(dict[str, Any]):
    def __init__(self, data: dict[str, Any] | None = None):
        super().__init__()
        for key, value in (data or {}).items():
            self[str(key)] = value

    def __getitem__(self, key: str) -> Any:
        if key in self.keys():
            return super().__getitem__(key)
        lowered = str(key).lower()
        for existing_key, value in self.items():
            if existing_key.lower() == lowered:
                return value
        raise KeyError(key)

    def get(self, key: str, default: Any = None) -> Any:
        try:
            return self[key]
        except KeyError:
            return default

    def GetValueOrDefault(self, key: str, default: Any = "") -> Any:
        value = self.get(key, default)
        if isinstance(value, (list, tuple)):
            return ",".join(str(item) for item in value)
        return value

    def ContainsKey(self, key: str) -> bool:
        return self._find_key(key) is not None

    def _find_key(self, key: str) -> str | None:
        lowered = str(key).lower()
        return next((existing for existing in self if existing.lower() == lowered), None)

    def __contains__(self, key: object) -> bool:
        return self._find_key(str(key)) is not None


class CalloutBody:
    def __init__(self, content: bytes, consume: Any = None):
        self._content = content
        self._consume = consume
        self._consumed = False

    def _read(self, preserve_content: bool) -> bytes:
        if self._consumed:
            return b""
        if not preserve_content:
            if self._consume is not None:
                self._consume()
            self._consumed = True
        return self._content

    def AsJObject(self, preserve_content: bool = False) -> dict[str, Any]:
        try:
            payload = json.loads(self._read(preserve_content).decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            # Microsoft documents the runtime exception, but not its exact message.
            raise ValueError("The body is not valid JSON.") from None
        if not isinstance(payload, dict):
            # A JSON value that is not an object cannot be returned as JObject.
            raise ValueError("The body is not valid JSON.")
        return payload

    def AsString(self, preserve_content: bool = False) -> str:
        return self._read(preserve_content).decode("utf-8", errors="replace")


class CalloutResponse:
    def __init__(self, *, status_code: int, headers: dict[str, str], content: bytes, reason: str | None = None):
        self.StatusCode = status_code
        self.Headers = ExpressionMap(headers)
        self.Body = CalloutBody(content)
        self.ReasonPhrase = reason or ""


class JwtValue:
    def __init__(self, claims: dict[str, Any], token: str):
        self.Raw = token
        self.Claims = ExpressionMap(
            {
                key: [str(item) for item in value] if isinstance(value, list) else [str(value)]
                for key, value in claims.items()
                if value is not None
            }
        )
        self.Subject = str(claims.get("sub", ""))
        self.Issuer = str(claims.get("iss", ""))
        audience = claims.get("aud")
        if isinstance(audience, list):
            self.Audiences = [str(item) for item in audience]
        elif audience is None:
            self.Audiences = []
        else:
            self.Audiences = [str(audience)]


def _as_jwt(value: str) -> JwtValue | None:
    """AsJwt is an inspection helper, not token authentication."""
    token = value.removeprefix("Bearer ").strip()
    try:
        claims = jwt.decode(token, options={"verify_signature": False, "verify_exp": False, "verify_aud": False})
    except jwt.InvalidTokenError:
        return None
    return JwtValue(claims, token)


def _nullable_jwt_subject(value: str) -> str | None:
    parsed = _as_jwt(value)
    return parsed.Subject if parsed is not None else None


@dataclass(frozen=True)
class _ExpressionMetadata:
    """Readonly API-shaped metadata with public member names."""

    Id: str = ""
    Name: str = ""
    Path: str = ""
    Method: str = ""
    UrlTemplate: str = ""
    Version: str = ""
    Revision: str = ""
    Email: str = ""
    FirstName: str = ""
    LastName: str = ""
    Note: str = ""


@dataclass(frozen=True)
class _ExpressionDeployment:
    Region: str = "local"
    GatewayId: str = "local"
    ServiceId: str = "apim-simulator"
    ServiceName: str = "apim-simulator"


@dataclass(frozen=True)
class _ExpressionGraphQL:
    Arguments: dict[str, Any]
    Parent: Any


class _DateTimeNamespace:
    @property
    def Now(self) -> datetime.datetime:
        return datetime.datetime.now().astimezone()

    @property
    def UtcNow(self) -> datetime.datetime:
        return datetime.datetime.now(datetime.UTC)


class _WebUtility:
    @staticmethod
    def UrlEncode(value: Any) -> str:
        return quote_plus(str(value), safe="")


class _RegexGroup:
    def __init__(self, value: str):
        self.Value = value


class _RegexNamespace:
    @staticmethod
    def Replace(value: str, pattern: str, replacement: str) -> str:
        compiled = re.compile(re.sub(r"\(\?<([A-Za-z_]\w*)>", r"(?P<\1>", pattern))

        def replace_match(match):
            def substitute(token):
                key = token.group(1)
                special = {
                    "$": "$",
                    "&": match.group(0),
                    "`": value[: match.start()],
                    "'": value[match.end() :],
                    "_": value,
                    "+": (match.group(compiled.groups) or "") if compiled.groups else match.group(0),
                }
                if key in special:
                    return special[key]
                group = key[1:-1] if key.startswith("{") else key
                group = int(group) if group.isdigit() else group
                try:
                    return match.group(group) or ""
                except (IndexError, KeyError):
                    return token.group(0)

            return re.sub(r"\$(\d+|\{\w+\}|[$&`'_+])", substitute, replacement)

        return compiled.sub(replace_match, value)

    @staticmethod
    def Match(value: str, pattern: str) -> Any:
        compiled = re.compile(re.sub(r"\(\?<([A-Za-z_]\w*)>", r"(?P<\1>", pattern))
        match = compiled.search(value)
        groups = {name: _RegexGroup((match.group(name) if match else "") or "") for name in compiled.groupindex}
        return type("RegexMatch", (), {"Groups": groups})()


@dataclass(frozen=True)
class _JsonProperty:
    name: str
    value: Any


class _JsonObject(dict):
    def __init__(self, *properties: _JsonProperty):
        super().__init__((property.name, property.value) for property in properties)


class _ConvertNamespace:
    @staticmethod
    def FromBase64String(value: str) -> bytes:
        return base64.b64decode("".join(value.split()), validate=True)


class _EncodingUTF8:
    @staticmethod
    def GetString(value: bytes) -> str:
        return value.decode("utf-8", errors="replace")


class _EncodingNamespace:
    UTF8 = _EncodingUTF8()


@dataclass(frozen=True)
class _ExpressionRequest:
    method: str
    path: str
    headers: ExpressionMap
    query: ExpressionMap
    matched_parameters: ExpressionMap
    original_host: str
    ip_address: str
    body: CalloutBody | None = None

    def headers_get(self, key: str, default: Any = "") -> Any:
        return self.headers.GetValueOrDefault(key, default)

    def query_get(self, key: str, default: Any = "") -> Any:
        return self.query.GetValueOrDefault(key, default)

    def matched_parameters_get(self, key: str, default: Any = "") -> Any:
        return self.matched_parameters.get(key, default)

    def ToHttpMessage(self, limit: int = 1024) -> str:
        from app.http_message import request_http_message

        body = self.body.AsString(True).encode("utf-8") if self.body is not None else b""
        return request_http_message(self.method, self.path, self.headers, body, limit, query=self.query)


@dataclass(frozen=True)
class _ExpressionResponse:
    status_code: int
    headers: ExpressionMap
    body: CalloutBody | None = None

    def headers_get(self, key: str, default: Any = "") -> Any:
        return self.headers.GetValueOrDefault(key, default)

    def ToHttpMessage(self, limit: int = 1024) -> str:
        from app.http_message import response_http_message

        body = self.body.AsString(True).encode("utf-8") if self.body is not None else b""
        return response_http_message(self.status_code, self.headers, body, limit)


@dataclass(frozen=True)
class _ExpressionSubscription:
    id: str


@dataclass(frozen=True)
class _ExpressionLastError:
    Source: str = ""
    Reason: str = ""
    Message: str = ""
    Scope: str = ""
    Section: str = ""
    Path: str = ""
    PolicyId: str = ""


@dataclass(frozen=True)
class ExpressionContext:
    request: _ExpressionRequest
    response: _ExpressionResponse
    subscription: _ExpressionSubscription
    variables: ExpressionMap
    LastError: _ExpressionLastError
    User: _ExpressionMetadata | None = None
    Deployment: _ExpressionDeployment = _ExpressionDeployment()
    Api: _ExpressionMetadata = _ExpressionMetadata()
    Operation: _ExpressionMetadata = _ExpressionMetadata()
    Product: _ExpressionMetadata | None = None
    GraphQL: _ExpressionGraphQL | None = None
    RequestId: str = ""

    @property
    def Request(self) -> _ExpressionRequest:
        return self.request

    @property
    def Response(self) -> _ExpressionResponse:
        return self.response

    def variables_get(self, key: str, default: Any = "") -> Any:
        return self.variables.get(key, default)


ALLOWED_AST_NODES = (
    ast.Expression,
    ast.IfExp,
    ast.BoolOp,
    ast.BinOp,
    ast.UnaryOp,
    ast.Compare,
    ast.Call,
    ast.Name,
    ast.Load,
    ast.Attribute,
    ast.Subscript,
    ast.Constant,
    ast.List,
    ast.Tuple,
    ast.Dict,
    ast.And,
    ast.Or,
    ast.Not,
    ast.Eq,
    ast.NotEq,
    ast.Gt,
    ast.GtE,
    ast.Lt,
    ast.LtE,
    ast.In,
    ast.NotIn,
    ast.Add,
    ast.Sub,
    ast.Mult,
    ast.Div,
    ast.Mod,
    ast.USub,
    ast.UAdd,
)

ALLOWED_FUNCTIONS = {
    "split_last",
    "str",
    "len",
    "_csharp_contains",
    "_csharp_equals",
    "_csharp_length",
    "_csharp_tostring",
    "_csharp_divide",
    "_dict_contains_key",
    "_as_jwt",
    "_nullable_jwt_subject",
    "_try_get_value",
    "_parse_int",
    "DateTime",
    "WebUtility",
    "Regex",
    "Convert",
    "Encoding",
    "request",
    "Guid",
    "JObject",
    "JProperty",
}


def _normalize_request(req: PolicyRequest) -> _ExpressionRequest:
    incoming_host = str(req.variables.get("forwarded_host") or req.variables.get("incoming_host") or "")
    host = incoming_host.split(",", 1)[0].strip()
    if ":" in host and not host.startswith("["):
        host = host.rsplit(":", 1)[0]
    request_headers = req.variables.get("_request_headers")
    if request_headers is None or (
        not isinstance(request_headers, dict) and not hasattr(request_headers, "as_dict_lists")
    ):
        request_headers = req.headers
    request_query = req.variables.get("_request_query")
    if request_query is None or (not isinstance(request_query, dict) and not hasattr(request_query, "as_dict_lists")):
        request_query = req.query
    matched_parameters = req.variables.get("_matched_parameters")
    if not isinstance(matched_parameters, dict):
        matched_parameters = {}
    return _ExpressionRequest(
        method=req.method,
        path=str(req.variables.get("_request_path") or req.path),
        headers=ExpressionMap(_as_value_lists(request_headers)),
        query=ExpressionMap(_as_value_lists(request_query)),
        matched_parameters=ExpressionMap(matched_parameters),
        original_host=host,
        ip_address=str(req.variables.get("client_ip") or ""),
        body=CalloutBody(req.body, lambda: setattr(req, "body", b"")) if req.body else None,
    )


def _as_value_lists(values: Any) -> dict[str, Any]:
    if hasattr(values, "as_dict_lists"):
        return values.as_dict_lists()
    if not isinstance(values, dict):
        return {}
    return {str(key): value for key, value in values.items()}


def build_expression_context(req: PolicyRequest) -> ExpressionContext:
    error = req.variables.get("_last_error")
    error_values = error if isinstance(error, dict) else {}
    return ExpressionContext(
        request=_normalize_request(req),
        response=_ExpressionResponse(
            status_code=req.response_status_code or 0,
            headers=ExpressionMap(
                _as_value_lists(req.response_headers or req.variables.get("_response_headers") or {})
            ),
            body=(
                CalloutBody(req.response_body, lambda: setattr(req, "response_body", b""))
                if req.response_body
                else None
            ),
        ),
        subscription=_ExpressionSubscription(id=str(req.variables.get("subscription_id") or "")),
        variables=ExpressionMap(req.variables),
        User=_ExpressionMetadata(**req.variables["_expression_user"])
        if req.variables.get("_expression_user")
        else None,
        Api=_ExpressionMetadata(**req.variables.get("_expression_api", {})),
        Operation=_ExpressionMetadata(**req.variables.get("_expression_operation", {})),
        Product=_ExpressionMetadata(**req.variables["_expression_product"])
        if req.variables.get("_expression_product")
        else None,
        Deployment=_ExpressionDeployment(
            Region=str(req.variables.get("location") or "local"),
            GatewayId=str(req.variables.get("gateway_id") or "local"),
            ServiceId=str(req.variables.get("service_id") or "apim-simulator"),
            ServiceName=str(req.variables.get("service_name") or "apim-simulator"),
        ),
        GraphQL=_ExpressionGraphQL(req.variables["_graphql_arguments"], req.variables.get("_graphql_parent"))
        if "_graphql_arguments" in req.variables
        else None,
        RequestId=str(req.variables.get("request_id") or ""),
        LastError=_ExpressionLastError(
            Source=str(error_values.get("Source") or ""),
            Reason=str(error_values.get("Reason") or ""),
            Message=str(error_values.get("Message") or ""),
            Scope=str(error_values.get("Scope") or ""),
            Section=str(error_values.get("Section") or ""),
            Path=str(error_values.get("Path") or ""),
            PolicyId=str(error_values.get("PolicyId") or ""),
        ),
    )


def split_last(value: Any, separator: str) -> str:
    text = str(value)
    parts = text.split(separator)
    return parts[-1]


def _strip_outer_expression(text: str) -> str:
    stripped = text.strip()
    if not stripped.startswith("@"):
        return stripped
    stripped = stripped[1:].strip()
    if stripped.startswith("(") and stripped.endswith(")"):
        return stripped[1:-1].strip()
    if stripped.startswith("{") and stripped.endswith("}"):
        return stripped[1:-1].strip()
    return stripped


def _advance_quoted(text: str, position: int, quote: str) -> tuple[int, str]:
    if text[position] == "\\":
        return position + 2, quote
    if text[position] == quote:
        return position + 1, ""
    return position + 1, quote


def _interpolation_end(text: str, start: int) -> int:
    depth = 1
    quote = ""
    i = start
    while i < len(text):
        char = text[i]
        if quote:
            i, quote = _advance_quoted(text, i, quote)
            continue
        elif char in {'"', "'"}:
            quote = char
        elif char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return i
        i += 1
    raise ValueError("Unclosed interpolation expression")


def _render_interpolated(text: str, context: ExpressionContext, locals_: dict[str, Any] | None = None) -> str:
    out: list[str] = []
    i = 0
    while i < len(text):
        if text.startswith("{{", i):
            out.append("{")
            i += 2
        elif text.startswith("}}", i):
            out.append("}")
            i += 2
        elif text[i] == "{":
            end = _interpolation_end(text, i + 1)
            out.append(str(_evaluate_expression(text[i + 1 : end], context, locals_)))
            i = end + 1
        else:
            out.append(text[i])
            i += 1
    return "".join(out)


def _rewrite_outside_strings(text: str, rewrite: Any) -> str:
    pieces: list[str] = []
    outside: list[str] = []
    quote = ""
    i = 0
    while i < len(text):
        char = text[i]
        if quote:
            pieces.append(char)
            if char == "\\" and i + 1 < len(text):
                pieces.append(text[i + 1])
                i += 2
                continue
            if char == quote:
                quote = ""
        elif char in {'"', "'"}:
            if outside:
                pieces.append(rewrite("".join(outside)))
                outside = []
            pieces.append(char)
            quote = char
        else:
            outside.append(char)
        i += 1
    if outside:
        pieces.append(rewrite("".join(outside)))
    return "".join(pieces)


def _replace_generic_call(match: re.Match[str]) -> str:
    method = match.group(1)
    type_name = match.group(2)
    if method == "As" and type_name == "JObject":
        return "AsJObject"
    if method == "As" and type_name == "string":
        return "AsString"
    return method


def _translate_code_fragment(fragment: str) -> str:
    translated = re.sub(r"\((?:string|bool|IResponse|Jwt|JObject)\)", "", fragment)
    translated = re.sub(r"\bnew\s+(?=JObject\b|JProperty\b)", "", translated)
    translated = re.sub(r"(?<![\w.)])(-?\d+)\.ToString\(\)", r"str(\1)", translated)
    translated = re.sub(
        r"\b(GetValueOrDefault|As)<([A-Za-z][\w.]*)>",
        _replace_generic_call,
        translated,
    )
    translated = re.sub(
        r"\b(AsString|AsJObject)\(\s*preserveContent\s*:\s*(true|false)\b", r"\1(\2", translated, flags=re.IGNORECASE
    )
    translated = re.sub(r"\btrue\b", "True", translated, flags=re.IGNORECASE)
    translated = re.sub(r"\bfalse\b", "False", translated, flags=re.IGNORECASE)
    translated = re.sub(r"\bnull\b", "None", translated)
    translated = re.sub(r"\bout\s+([A-Za-z_]\w*)", r'"\1"', translated)
    translated = translated.replace(".AsJwt()?.Subject", ".NullableJwtSubject()")
    translated = translated.replace("]?.Value", "].Value")
    translated = translated.replace("int.Parse(", "_parse_int(")
    translated = translated.replace("System.Net.WebUtility.", "WebUtility.")
    translated = translated.replace("&&", " and ").replace("||", " or ")
    translated = translated.replace("!=", " != ")
    translated = re.sub(r"(?<![=!<>])!(?!=)", " not ", translated)
    replacements = (
        ("context.Request.Body", "context.request.body"),
        ("context.Request.Headers.GetValueOrDefault", "context.request.headers_get"),
        ("context.Request.Headers", "context.request.headers"),
        ("request.Headers.GetValueOrDefault", "request.headers_get"),
        ("context.Request.Url.Query.GetValueOrDefault", "context.request.query_get"),
        ("context.Request.Url.Query", "context.request.query"),
        ("context.Request.MatchedParameters.GetValueOrDefault", "context.request.matched_parameters_get"),
        ("context.Request.MatchedParameters", "context.request.matched_parameters"),
        ("context.Request.OriginalUrl.Host", "context.request.original_host"),
        ("context.Request.IpAddress", "context.request.ip_address"),
        ("context.Request.Url.Path", "context.request.path"),
        ("context.Request.Method", "context.request.method"),
        ("context.Response.Body", "context.response.body"),
        ("context.Response.Headers.GetValueOrDefault", "context.response.headers_get"),
        ("context.Response.StatusCode", "context.response.status_code"),
        ("context.Subscription.Id", "context.subscription.id"),
        ("context.Variables.GetValueOrDefault", "context.variables_get"),
        ("context.Variables", "context.variables"),
        (".Split(", ".split("),
        (".Last()", "[-1]"),
    )
    for source, target in replacements:
        translated = translated.replace(source, target)
    return translated.strip()


def _translate_expression(expr: str) -> str:
    translated = _rewrite_outside_strings(expr.replace('@"', 'r"'), _translate_code_fragment)
    return _translate_ternary(translated).strip()


def _update_levels(char: str, levels: dict[str, int]) -> bool:
    closing = {")": "(", "]": "[", "}": "{"}
    if char in levels:
        levels[char] += 1
        return True
    if char in closing:
        levels[closing[char]] -= 1
        return True
    return False


def _find_top_level_question(expression: str) -> int:
    levels = {"(": 0, "[": 0, "{": 0}
    quote = ""
    position = 0
    while position < len(expression):
        char = expression[position]
        if quote:
            position, quote = _advance_quoted(expression, position, quote)
            continue
        if char in {'"', "'"}:
            quote = char
        elif not _update_levels(char, levels) and char == "?" and not any(levels.values()):
            return position
        position += 1
    return -1


def _find_ternary_colon(expression: str, question: int) -> int:
    nested = 0
    quote = ""
    position = question + 1
    while position < len(expression):
        char = expression[position]
        if quote:
            position, quote = _advance_quoted(expression, position, quote)
            continue
        if char in {'"', "'"}:
            quote = char
        elif char == "?":
            nested += 1
        elif char == ":":
            if nested == 0:
                return position
            nested -= 1
        position += 1
    return -1


def _find_ternary_parts(expression: str) -> tuple[str, str, str] | None:
    question = _find_top_level_question(expression)
    if question < 0:
        return None
    colon = _find_ternary_colon(expression, question)
    if colon < 0:
        raise SyntaxError("Conditional operator is missing ':'")
    return expression[:question], expression[question + 1 : colon], expression[colon + 1 :]


def _translate_ternary(expression: str) -> str:
    parts = _find_ternary_parts(expression)
    if parts is None:
        return expression
    condition, consequent, alternative = parts
    return (
        f"({_translate_ternary(consequent)} if {_translate_ternary(condition)} else {_translate_ternary(alternative)})"
    )


def _csharp_contains(value: Any, needle: Any) -> bool:
    return needle in value


def _csharp_equals(value: Any, other: Any) -> bool:
    return value == other


def _csharp_length(value: Any) -> int:
    return len(value)


def _csharp_tostring(value: Any) -> str:
    if isinstance(value, _JsonObject):
        return json.dumps(value, ensure_ascii=False)
    return str(value)


def _csharp_divide(left: Any, right: Any) -> Any:
    if isinstance(left, int) and not isinstance(left, bool) and isinstance(right, int) and not isinstance(right, bool):
        return int(left / right)
    return left / right


def _dict_contains_key(value: Any, key: Any) -> bool:
    return key in value


class _CSharpAstTransformer(ast.NodeTransformer):
    _methods = {
        "Contains": "_csharp_contains",
        "Equals": "_csharp_equals",
        "ToString": "_csharp_tostring",
        "ContainsKey": "_dict_contains_key",
        "AsJwt": "_as_jwt",
        "NullableJwtSubject": "_nullable_jwt_subject",
        "TryGetValue": "_try_get_value",
    }
    _aliases = {
        "StartsWith": "startswith",
        "EndsWith": "endswith",
        "ToUpper": "upper",
        "ToLower": "lower",
        "Trim": "strip",
    }

    def visit_Attribute(self, node: ast.Attribute) -> ast.AST:
        node = self.generic_visit(node)
        if node.attr != "Length":
            return node
        return ast.copy_location(
            ast.Call(func=ast.Name(id="_csharp_length", ctx=ast.Load()), args=[node.value], keywords=[]),
            node,
        )

    def visit_Call(self, node: ast.Call) -> ast.AST:
        node = self.generic_visit(node)
        if not isinstance(node.func, ast.Attribute):
            return node
        helper = self._methods.get(node.func.attr)
        if helper is not None:
            args = [node.func.value, *node.args]
            return ast.copy_location(
                ast.Call(func=ast.Name(id=helper, ctx=ast.Load()), args=args, keywords=[]),
                node,
            )
        alias = self._aliases.get(node.func.attr)
        if alias is not None:
            node.func.attr = alias
        return node

    def visit_BinOp(self, node: ast.BinOp) -> ast.AST:
        node = self.generic_visit(node)
        if not isinstance(node.op, ast.Div):
            return node
        return ast.copy_location(
            ast.Call(
                func=ast.Name(id="_csharp_divide", ctx=ast.Load()),
                args=[node.left, node.right],
                keywords=[],
            ),
            node,
        )


def _validate_ast(expression: str, allowed_names: set[str] | None = None) -> ast.Expression:
    tree = _CSharpAstTransformer().visit(ast.parse(expression, mode="eval"))
    ast.fix_missing_locations(tree)
    for node in ast.walk(tree):
        if not isinstance(node, ALLOWED_AST_NODES):
            raise ValueError(f"Unsupported expression syntax: {type(node).__name__}")
        if isinstance(node, ast.Attribute) and node.attr.startswith("_"):
            raise ValueError("Private expression members are not accessible")
        if isinstance(node, ast.Name):
            # "True" and "False" parse as ast.Constant, never as ast.Name, so
            # they do not need naming here.
            names = ALLOWED_FUNCTIONS | (allowed_names or set())
            if node.id != "context" and node.id not in names:
                raise ValueError(f"Unsupported expression name: {node.id}")
    return tree


@dataclass(frozen=True)
class _CSharpSimple:
    text: str


@dataclass(frozen=True)
class _CSharpBlock:
    statements: tuple[Any, ...]


@dataclass(frozen=True)
class _CSharpIf:
    condition: str
    when_true: _CSharpBlock
    when_false: _CSharpBlock | None


@dataclass(frozen=True)
class _CSharpReturn:
    value: Any


def _skip_space(text: str, position: int) -> int:
    while position < len(text) and text[position].isspace():
        position += 1
    return position


def _balanced_end(text: str, start: int, opening: str, closing: str) -> int:
    depth = 1
    quote = ""
    position = start + 1
    while position < len(text):
        char = text[position]
        if quote:
            position, quote = _advance_quoted(text, position, quote)
            continue
        elif char in {'"', "'"}:
            quote = char
        elif char == opening:
            depth += 1
        elif char == closing:
            depth -= 1
            if depth == 0:
                return position
        position += 1
    raise SyntaxError(f"Unclosed '{opening}' in multi-statement expression")


def _parse_statement(text: str, position: int) -> tuple[Any, int]:
    position = _skip_space(text, position)
    if text.startswith("if", position) and (position + 2 == len(text) or not text[position + 2].isalnum()):
        condition_start = _skip_space(text, position + 2)
        if condition_start >= len(text) or text[condition_start] != "(":
            raise SyntaxError("if statement requires a condition")
        condition_end = _balanced_end(text, condition_start, "(", ")")
        branch, position = _parse_statement(text, condition_end + 1)
        position = _skip_space(text, position)
        otherwise: _CSharpBlock | None = None
        if text.startswith("else", position):
            alternate, position = _parse_statement(text, position + 4)
            otherwise = alternate if isinstance(alternate, _CSharpBlock) else _CSharpBlock((alternate,))
        selected = branch if isinstance(branch, _CSharpBlock) else _CSharpBlock((branch,))
        return _CSharpIf(text[condition_start + 1 : condition_end], selected, otherwise), position
    if position < len(text) and text[position] == "{":
        statements, position = _parse_sequence(text, position + 1, "}")
        return _CSharpBlock(tuple(statements)), position
    end = _simple_statement_end(text, position)
    return _CSharpSimple(text[position:end].strip()), end + (end < len(text) and text[end] == ";")


def _simple_statement_end(text: str, start: int) -> int:
    levels = {"(": 0, "[": 0, "{": 0}
    quote = ""
    position = start
    while position < len(text):
        char = text[position]
        if quote:
            position, quote = _advance_quoted(text, position, quote)
            continue
        elif char in {'"', "'"}:
            quote = char
        elif char == "}" and not any(levels.values()):
            break
        elif _update_levels(char, levels):
            pass
        elif char == ";" and not any(levels.values()):
            break
        position += 1
    return position


def _parse_sequence(text: str, position: int, stop: str | None) -> tuple[list[Any], int]:
    statements: list[Any] = []
    while True:
        position = _skip_space(text, position)
        if position >= len(text):
            if stop is not None:
                raise SyntaxError(f"Unclosed '{stop}' in multi-statement expression")
            return statements, position
        if stop is not None and text[position] == stop:
            return statements, position + 1
        statement, position = _parse_statement(text, position)
        statements.append(statement)


def _evaluate_expression(expression: str, context: ExpressionContext, locals_: dict[str, Any] | None = None) -> Any:
    stripped = expression.strip()
    if stripped.startswith('$"') and stripped.endswith('"'):
        return _render_interpolated(stripped[2:-1], context, locals_)
    translated = _translate_expression(stripped)
    tree = _validate_ast(translated, set(locals_ or {}))
    scope = locals_ if locals_ is not None else {}

    def try_get_value(mapping: ExpressionMap, key: str, target: str) -> bool:
        exists = mapping.ContainsKey(key)
        scope[target] = mapping[key] if exists else None
        return exists

    environment = {
        "context": context,
        "split_last": split_last,
        "str": str,
        "len": len,
        "_csharp_contains": _csharp_contains,
        "_csharp_equals": _csharp_equals,
        "_csharp_length": _csharp_length,
        "_csharp_tostring": _csharp_tostring,
        "_csharp_divide": _csharp_divide,
        "_dict_contains_key": _dict_contains_key,
        "_as_jwt": _as_jwt,
        "_nullable_jwt_subject": _nullable_jwt_subject,
        "_try_get_value": try_get_value,
        "_parse_int": int,
        "DateTime": _DateTimeNamespace(),
        "WebUtility": _WebUtility(),
        "Regex": _RegexNamespace(),
        "Convert": _ConvertNamespace(),
        "Encoding": _EncodingNamespace(),
        "request": context.request,
        "Guid": type("Guid", (), {"NewGuid": staticmethod(uuid.uuid4)}),
        "JObject": _JsonObject,
        "JProperty": _JsonProperty,
        **(locals_ or {}),
    }
    return eval(compile(tree, "<apim-expression>", "eval"), {"__builtins__": {}}, environment)


def _execute_simple(
    statement: _CSharpSimple, context: ExpressionContext, locals_: dict[str, Any]
) -> _CSharpReturn | None:
    text = statement.text
    if text.startswith("return"):
        value = text[6:].strip()
        return _CSharpReturn(_evaluate_expression(value, context, locals_) if value else None)
    declaration = re.match(
        r"^(?:var|[A-Za-z_]\w*(?:\s*<[^>]+>)?(?:\[\])?)\s+([A-Za-z_]\w*)\s*(?:=\s*(.*))?$",
        text,
    )
    if declaration:
        locals_[declaration.group(1)] = (
            _evaluate_expression(declaration.group(2), context, locals_) if declaration.group(2) else None
        )
        return None
    assignment = re.match(r"^([A-Za-z_]\w*)\s*=\s*(.*)$", text)
    if assignment and assignment.group(1) in locals_:
        locals_[assignment.group(1)] = _evaluate_expression(assignment.group(2), context, locals_)
        return None
    raise SyntaxError(f"Unsupported statement: {text}")


def _execute_statements(
    statements: tuple[Any, ...], context: ExpressionContext, locals_: dict[str, Any]
) -> _CSharpReturn | None:
    for statement in statements:
        if isinstance(statement, _CSharpSimple):
            result = _execute_simple(statement, context, locals_)
        elif isinstance(statement, _CSharpBlock):
            result = _execute_statements(statement.statements, context, locals_)
        else:
            condition = _evaluate_expression(statement.condition, context, locals_)
            if not isinstance(condition, bool):
                raise ValueError("C# condition must evaluate to Boolean.")
            branch = statement.when_true if condition else statement.when_false
            result = _execute_statements(branch.statements, context, locals_) if branch else None
        if result is not None:
            return result
    return None


def _guarantees_return(statement: Any) -> bool:
    if isinstance(statement, _CSharpSimple):
        return statement.text.startswith("return")
    if isinstance(statement, _CSharpBlock):
        return any(_guarantees_return(item) for item in statement.statements)
    return (
        statement.when_false is not None
        and _guarantees_return(statement.when_true)
        and _guarantees_return(statement.when_false)
    )


def _evaluate_multistatement(body: str, context: ExpressionContext) -> Any:
    if ";" not in body and not re.match(r"\s*(?:if|return|(?:var|[A-Za-z_]\w*)\s+[A-Za-z_]\w*)\b", body):
        return _evaluate_expression(body, context)
    statements, position = _parse_sequence(body, 0, None)
    if _skip_space(body, position) != len(body):
        raise SyntaxError("Unexpected text after multi-statement expression")
    if not any(_guarantees_return(statement) for statement in statements):
        raise ValueError("Multi-statement expression must return on all code paths.")
    result = _execute_statements(tuple(statements), context, {})
    if result is None:
        raise ValueError("Multi-statement expression must return on all code paths.")
    return result.value


def evaluate_apim_expression(expression: str, context: ExpressionContext) -> Any:
    original = expression.strip()
    stripped = _strip_outer_expression(expression)
    if original.startswith("@{") and original.endswith("}"):
        return _evaluate_multistatement(stripped, context)
    return _evaluate_expression(stripped, context)


def evaluate_apim_condition(expression: str, context: ExpressionContext) -> bool:
    value = evaluate_apim_expression(expression, context)
    if not isinstance(value, bool):
        raise ValueError("C# condition must evaluate to Boolean.")
    return value


def is_apim_expression(value: str) -> bool:
    stripped = value.strip()
    return stripped.startswith("@(") or stripped.startswith("@{")
