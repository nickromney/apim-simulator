from __future__ import annotations

import json

import pytest

from app.openapi_import import parse_api_import

pytestmark = pytest.mark.contract("MGMT-OPENAPI-IMPORT")


def _import(document: dict, *, format_name: str = "openapi+json", **kwargs):
    return parse_api_import(content_format=format_name, content_value=json.dumps(document), **kwargs)


def test_openapi_30_projects_normalized_ids_metadata_and_nested_schemas() -> None:
    result = _import(
        {
            "openapi": "3.0.3",
            "servers": [{"url": "http://ignored.example"}, {"url": "https://api.example/v2"}],
            "components": {
                "schemas": {
                    "Widget": {"type": "object", "properties": {"label": {"$ref": "#/components/schemas/Label"}}},
                    "Label": {"type": "object", "properties": {"value": {"type": "string"}}},
                }
            },
            "paths": {
                "/widgets/{widgetId}": {
                    "parameters": [{"name": "widgetId", "in": "path", "required": True, "schema": {"type": "integer"}}],
                    "get": {
                        "operationId": "GET /Widgets/{widgetId}",
                        "summary": "Get widget",
                        "parameters": [
                            {
                                "name": "region",
                                "in": "query",
                                "required": True,
                                "schema": {"type": "string", "enum": ["east", "west"]},
                            },
                            {"name": "x-trace", "in": "header", "required": False, "schema": {"type": "string"}},
                        ],
                        "responses": {
                            "200": {
                                "description": "A widget",
                                "content": {
                                    "application/json": {
                                        "schema": {"$ref": "#/components/schemas/Widget"},
                                        "examples": {
                                            "sample": {"summary": "Example", "value": {"label": {"value": "ok"}}}
                                        },
                                    }
                                },
                            }
                        },
                    },
                }
            },
        }
    )

    operation = result.operations[0]
    assert result.upstream_base_url == "https://api.example/v2"
    assert operation.name == "get-widgets-widgetid"
    assert operation.display_name == "Get widget"
    assert operation.url_template == "/widgets/{widgetId}?region={region}"
    assert {item.name for item in operation.template_parameters} == {"widgetId", "region"}
    assert operation.request is not None
    assert operation.request.query_parameters[0].values == ["east", "west"]
    assert operation.request.headers[0].name == "x-trace"
    representation = operation.responses[0].representations[0]
    assert representation.schema_id == "Widget"
    assert representation.examples[0].name == "sample"
    assert result.schemas["Widget"].components["schemas"]["Label"]["properties"]["value"]["type"] == "string"


def test_swagger_json_projects_base_url_and_discards_get_body() -> None:
    result = _import(
        {
            "swagger": "2.0",
            "host": "api.example",
            "basePath": "/v1",
            "schemes": ["https"],
            "consumes": ["application/json"],
            "produces": ["application/json"],
            "paths": {
                "/widgets": {
                    "get": {
                        "operationId": "List_Widgets",
                        "parameters": [
                            {
                                "name": "body",
                                "in": "body",
                                "schema": {"type": "object", "properties": {"x": {"type": "string"}}},
                            }
                        ],
                        "responses": {
                            "200": {
                                "description": "ok",
                                "schema": {"type": "string"},
                                "examples": {"application/json": "ok"},
                            }
                        },
                    }
                }
            },
        },
        format_name="swagger-json",
    )

    assert result.upstream_base_url == "https://api.example/v1"
    assert result.operations[0].name == "list-widgets"
    assert result.operations[0].request is None
    assert result.operations[0].responses[0].representations[0].examples[0].value == "ok"


def test_rejects_unsupported_versions_refs_recursion_serialization_and_inline_schemas() -> None:
    with pytest.raises(ValueError, match="3.1"):
        _import({"openapi": "3.1.0", "paths": {}})

    base = {"openapi": "3.0.1", "paths": {"/x": {"post": {"responses": {"200": {"description": "ok"}}}}}}
    with pytest.raises(ValueError, match="External or invalid"):
        _import({**base, "components": {"schemas": {"X": {"$ref": "https://example.com/schema.json"}}}})
    with pytest.raises(ValueError, match="Recursive"):
        _import({**base, "components": {"schemas": {"X": {"$ref": "#/components/schemas/X"}}}})

    serialized = json.loads(json.dumps(base))
    serialized["paths"]["/x"]["post"]["parameters"] = [
        {
            "name": "tags",
            "in": "query",
            "schema": {"type": "array", "items": {"type": "string"}},
            "style": "pipeDelimited",
            "explode": False,
        }
    ]
    with pytest.raises(ValueError, match="serialization"):
        _import(serialized)

    serialized["paths"]["/x"]["post"].pop("parameters")
    serialized["paths"]["/x"]["post"]["requestBody"] = {
        "content": {"application/json": {"schema": {"type": "object", "properties": {"x": {"type": "string"}}}}}
    }
    with pytest.raises(ValueError, match="Inline complex"):
        _import(serialized)


@pytest.mark.parametrize(
    "location,style,explode",
    [("path", "label", False), ("path", "matrix", False), ("path", "simple", True), ("header", "label", False)],
)
def test_rejects_unsupported_path_and_header_parameter_serialization(location, style, explode) -> None:
    result = {
        "openapi": "3.0.3",
        "paths": {
            "/pets/{petId}": {
                "get": {
                    "parameters": [
                        {
                            "name": "petId" if location == "path" else "x-pet-id",
                            "in": location,
                            "required": location == "path",
                            "style": style,
                            "explode": explode,
                            "schema": {"type": "string"},
                        }
                    ]
                }
            }
        },
    }

    with pytest.raises(ValueError, match=f"Unsupported {location} parameter serialization"):
        _import(result)


def test_required_query_parameter_can_remain_a_query_parameter() -> None:
    result = _import(
        {
            "openapi": "3.0.1",
            "paths": {
                "/x": {
                    "get": {
                        "parameters": [{"name": "q", "in": "query", "required": True, "schema": {"type": "string"}}]
                    }
                }
            },
        },
        translate_required_query_parameters="query",
    )
    operation = result.operations[0]
    assert operation.url_template == "/x"
    assert operation.template_parameters == []
    assert operation.request.query_parameters[0].required is True


def test_relative_server_resolves_against_link_source_and_http_only_servers_are_empty() -> None:
    linked = parse_api_import(
        content_format="openapi-link",
        content_value="https://docs.example/specs/openapi.yaml",
        fetcher=lambda _: "openapi: 3.0.1\nservers:\n  - url: ../backend\npaths: {}\n",
    )
    http_only = _import({"openapi": "3.0.1", "servers": [{"url": "http://backend.example"}], "paths": {}})

    assert linked.upstream_base_url == "https://docs.example/backend"
    assert http_only.upstream_base_url is None


def test_ids_are_length_limited_and_deduplicated_without_collisions() -> None:
    long_id = "x" * 90
    result = _import(
        {
            "openapi": "3.0.1",
            "paths": {
                "/a": {"get": {"operationId": long_id}},
                "/b": {"get": {"operationId": long_id}},
                "/c": {"get": {"operationId": f"{long_id}-1"}},
            },
        }
    )
    ids = [operation.name for operation in result.operations]
    assert len(set(ids)) == 3
    assert len(ids[0]) == 76
    assert ids[1].endswith("-1")
    assert ids[2].endswith("-2")
    short = _import(
        {
            "openapi": "3.0.1",
            "paths": {
                "/a": {"get": {"operationId": "foo"}},
                "/b": {"get": {"operationId": "foo"}},
                "/c": {"get": {"operationId": "foo-1"}},
            },
        }
    )
    assert [operation.name for operation in short.operations] == ["foo", "foo-1", "foo-1-1"]


def test_rejects_unprojected_parameter_schema_fields_and_json_format_yaml() -> None:
    doc = {
        "openapi": "3.0.1",
        "paths": {
            "/x": {"get": {"parameters": [{"name": "q", "in": "query", "schema": {"type": "string", "pattern": "a+"}}]}}
        },
    }
    with pytest.raises(ValueError, match="parameter schema fields"):
        parse_api_import(content_format="openapi+json", content_value=json.dumps(doc))
    doc["paths"]["/x"]["get"]["parameters"][0]["schema"] = {
        "type": "array",
        "items": {"type": "string"},
    }
    with pytest.raises(ValueError, match="Array parameter serialization"):
        parse_api_import(content_format="openapi+json", content_value=json.dumps(doc))
    for schema in ({"type": "object"}, {"$ref": "#/components/schemas/Q"}):
        doc["components"] = {"schemas": {"Q": {"type": "object"}}}
        doc["paths"]["/x"]["get"]["parameters"][0]["schema"] = schema
        with pytest.raises(ValueError, match="unsupported"):
            parse_api_import(content_format="openapi+json", content_value=json.dumps(doc))
    with pytest.raises(ValueError, match="requires JSON"):
        parse_api_import(content_format="swagger-json", content_value="swagger: '2.0'\npaths: {}\n")
    with pytest.raises(ValueError, match="valid JSON or YAML"):
        parse_api_import(content_format="openapi", content_value="openapi: [broken")
