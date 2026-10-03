"""Optional discovery metadata stays small and points at actual app routes."""

import tomllib
from pathlib import Path
from urllib.parse import urlsplit

import pytest
import yaml

from app.main import create_app

pytestmark = pytest.mark.repo
ROOT = Path(__file__).resolve().parents[1]


def test_catalog_component_matches_project_and_route_inventory():
    from tests.test_app_composition import _flatten_routes

    docs = list(yaml.safe_load_all((ROOT / "catalog-info.yaml").read_text()))
    assert len(docs) == 1  # No copied API definitions or release versions to drift.
    component = docs[0]
    project = tomllib.loads((ROOT / "pyproject.toml").read_text())["project"]
    assert component["kind"] == "Component"
    assert component["metadata"]["name"] == project["name"]
    paths = {route.path for route in _flatten_routes(create_app().routes) if hasattr(route, "path")}
    for link in component["metadata"]["links"]:
        assert urlsplit(link["url"]).path in paths
