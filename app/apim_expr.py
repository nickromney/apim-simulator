from __future__ import annotations

import ast
import json
import re
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

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
        return self.get(key, default)

    def ContainsKey(self, key: str) -> bool:
        return key in self


class CalloutBody:
    def __init__(self, content: bytes):
        self._content = content

    def AsJObject(self) -> dict[str, Any]:
        try:
            payload = json.loads(self._content.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            # Microsoft documents the runtime exception, but not its exact message.
            raise ValueError("The body is not valid JSON.") from None
        if not isinstance(payload, dict):
            # A JSON value that is not an object cannot be returned as JObject.
            raise ValueError("The body is not valid JSON.")
        return payload

    def AsString(self) -> str:
        return self._content.decode("utf-8", errors="replace")


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


@dataclass(frozen=True)
class _ExpressionRequest:
    method: str
    path: str
    headers: ExpressionMap
    query: ExpressionMap
    matched_parameters: ExpressionMap
    original_host: str
    ip_address: str

    def headers_get(self, key: str, default: Any = "") -> Any:
        return self.headers.get(key, default)

    def query_get(self, key: str, default: Any = "") -> Any:
        return self.query.get(key, default)

    def matched_parameters_get(self, key: str, default: Any = "") -> Any:
        return self.matched_parameters.get(key, default)


@dataclass(frozen=True)
class _ExpressionResponse:
    status_code: int
    headers: ExpressionMap

    def headers_get(self, key: str, default: Any = "") -> Any:
        return self.headers.get(key, default)


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
}


def _normalize_request(req: PolicyRequest) -> _ExpressionRequest:
    incoming_host = str(req.variables.get("forwarded_host") or req.variables.get("incoming_host") or "")
    host = incoming_host.split(",", 1)[0].strip()
    if ":" in host and not host.startswith("["):
        host = host.rsplit(":", 1)[0]
    request_headers = req.variables.get("_request_headers")
    if not isinstance(request_headers, dict):
        request_headers = req.headers
    request_query = req.variables.get("_request_query")
    if not isinstance(request_query, dict):
        request_query = req.query
    matched_parameters = req.variables.get("_matched_parameters")
    if not isinstance(matched_parameters, dict):
        matched_parameters = {}
    return _ExpressionRequest(
        method=req.method,
        path=req.path,
        headers=ExpressionMap(request_headers),
        query=ExpressionMap(request_query),
        matched_parameters=ExpressionMap(matched_parameters),
        original_host=host,
        ip_address=str(req.variables.get("client_ip") or ""),
    )


def build_expression_context(req: PolicyRequest) -> ExpressionContext:
    error = req.variables.get("_last_error")
    error_values = error if isinstance(error, dict) else {}
    return ExpressionContext(
        request=_normalize_request(req),
        response=_ExpressionResponse(
            status_code=req.response_status_code or 0,
            headers=ExpressionMap(req.response_headers or req.variables.get("_response_headers") or {}),
        ),
        subscription=_ExpressionSubscription(id=str(req.variables.get("subscription_id") or "")),
        variables=ExpressionMap(req.variables),
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
    translated = re.sub(r"(?<![\w.)])(-?\d+)\.ToString\(\)", r"str(\1)", translated)
    translated = re.sub(
        r"\b(GetValueOrDefault|As)<([A-Za-z][\w.]*)>",
        _replace_generic_call,
        translated,
    )
    translated = re.sub(r"\btrue\b", "True", translated, flags=re.IGNORECASE)
    translated = re.sub(r"\bfalse\b", "False", translated, flags=re.IGNORECASE)
    translated = translated.replace("&&", " and ").replace("||", " or ")
    translated = translated.replace("!=", " != ")
    translated = re.sub(r"(?<![=!<>])!(?!=)", " not ", translated)
    replacements = (
        ("context.Request.Headers.GetValueOrDefault", "context.request.headers_get"),
        ("context.Request.Url.Query.GetValueOrDefault", "context.request.query_get"),
        ("context.Request.MatchedParameters.GetValueOrDefault", "context.request.matched_parameters_get"),
        ("context.Request.MatchedParameters", "context.request.matched_parameters"),
        ("context.Request.OriginalUrl.Host", "context.request.original_host"),
        ("context.Request.IpAddress", "context.request.ip_address"),
        ("context.Request.Url.Path", "context.request.path"),
        ("context.Request.Method", "context.request.method"),
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
    translated = _rewrite_outside_strings(expr, _translate_code_fragment)
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
