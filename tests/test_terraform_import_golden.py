"""Golden snapshot of the OpenTofu importer across its whole resource surface.

`import_from_tofu_show_json` used to be one 820-line function holding two
dispatch loops. Restructuring it is only safe if something notices when a
single imported field changes, and per-resource unit tests do not: the bugs
that matter here are the ones where resource A's import quietly stops feeding
resource B.

So this pins the entire result -- every field of the produced tenant document
plus every diagnostic -- for a fixture that exercises all 24 resource types the
importer handles. Regenerate deliberately with:

    python -m tests.test_terraform_import_golden

and read the diff before committing it.
"""

from __future__ import annotations

import dataclasses
import enum
import json
from pathlib import Path
from typing import Any

from app.terraform_import import import_from_tofu_show_json

FIXTURE = Path(__file__).resolve().parent / "fixtures" / "tofu_show" / "full_surface.json"
GOLDEN = Path(__file__).resolve().parent / "fixtures" / "tofu_show" / "full_surface.golden.json"


def _plain(value: Any) -> Any:
    """Reduce the tenant document to JSON that sorts and diffs predictably."""
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        return {f.name: _plain(getattr(value, f.name)) for f in dataclasses.fields(value)}
    if isinstance(value, enum.Enum):
        return value.value
    if isinstance(value, dict):
        return {str(k): _plain(v) for k, v in sorted(value.items(), key=lambda kv: str(kv[0]))}
    if isinstance(value, (list, tuple, set)):
        items = [_plain(v) for v in value]
        return sorted(items, key=repr) if isinstance(value, set) else items
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return repr(value)


def snapshot() -> dict[str, Any]:
    result = import_from_tofu_show_json(json.loads(FIXTURE.read_text(encoding="utf-8")))
    return {
        "service_imported": result.service_imported,
        "config": _plain(result.config),
        "diagnostics": [_plain(d) for d in result.diagnostics],
    }


def test_full_resource_surface_imports_to_the_recorded_tenant_document() -> None:
    assert GOLDEN.exists(), "golden missing; regenerate with python -m tests.test_terraform_import_golden"
    expected = json.loads(GOLDEN.read_text(encoding="utf-8"))
    assert snapshot() == expected


def test_every_handled_resource_type_appears_in_the_fixture() -> None:
    """A resource type the fixture never mentions is a type the golden cannot guard."""
    import re

    source = (Path(__file__).resolve().parents[1] / "app" / "terraform_import.py").read_text(encoding="utf-8")
    handled = set(re.findall(r'res\.type == "([a-z_]+)"', source))
    fixture = FIXTURE.read_text(encoding="utf-8")
    missing = sorted(t for t in handled if f'"{t}"' not in fixture)
    assert not missing, f"resource types handled but never imported by the golden fixture: {missing}"


if __name__ == "__main__":
    GOLDEN.write_text(json.dumps(snapshot(), indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(f"wrote {GOLDEN}")
