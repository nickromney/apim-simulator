from __future__ import annotations

import re
import subprocess
from pathlib import Path

import pytest
import yaml

# Marked `repo`: asserts on build and CI configuration, not on app behaviour.
pytestmark = pytest.mark.repo

REPO_ROOT = Path(__file__).resolve().parents[1]
DIGEST = re.compile(r"@sha256:[0-9a-f]{64}$")
# Images this repo builds itself; they have no registry digest to pin.
LOCAL_IMAGE = re.compile(r"^apim-simulator[\w-]*:(latest|local)$")

IMAGE_REFERENCES = (
    re.compile(r"^FROM\s+(?:--platform=\S+\s+)?(\S+)", re.MULTILINE),
    re.compile(r"^[ \t]*image:[ \t]*(\S+)", re.MULTILINE),
    # ARG defaults, compose build args, workflow env and .env.example lines.
    re.compile(r"\b[A-Z_]*_IMAGE[ \t]*(?:=|:(?!-))[ \t]*\"?([^\s\"]+)"),
)


def _tracked(*patterns: str) -> list[Path]:
    result = subprocess.run(["git", "ls-files", *patterns], cwd=REPO_ROOT, capture_output=True, text=True, check=True)
    return [REPO_ROOT / line for line in result.stdout.splitlines()]


def _image_references() -> list[tuple[str, str]]:
    files = _tracked(
        "Dockerfile", "*/Dockerfile", "*.Dockerfile", "compose*.yml", "*/compose*.yml", ".github/workflows/*.yml"
    )
    files.append(REPO_ROOT / ".env.example")
    stages: set[str] = set()
    references: list[tuple[str, str]] = []
    for path in files:
        text = path.read_text()
        stages |= set(re.findall(r"^FROM\s.+\sAS\s+(\S+)", text, re.MULTILINE))
        for pattern in IMAGE_REFERENCES:
            for value in pattern.findall(text):
                # ${VAR:-default}: the default is what runs when nothing overrides it.
                if default := re.fullmatch(r"\$\{\w+:-(.+)\}", value):
                    value = default.group(1)
                if value.startswith(("$", "#")):
                    continue
                references.append((str(path.relative_to(REPO_ROOT)), value))
    return [(f, v) for f, v in references if v not in stages and v != "scratch" and not LOCAL_IMAGE.match(v)]


def test_scan_finds_the_images_it_is_meant_to_police() -> None:
    images = {value.split("@")[0] for _, value in _image_references()}

    assert {"dhi.io/python:3.13-debian13", "python:3.13-slim", "quay.io/keycloak/keycloak:26.4.7"} <= images


def test_every_external_image_is_pinned_by_digest() -> None:
    unpinned = [f"{path}: {value}" for path, value in _image_references() if not DIGEST.search(value)]

    assert not unpinned, "Pin these as tag@sha256:<digest>:\n" + "\n".join(unpinned)


def test_each_image_tag_is_pinned_to_one_digest_everywhere() -> None:
    digests: dict[str, set[str]] = {}
    for _, value in _image_references():
        tag, _, digest = value.partition("@")
        digests.setdefault(tag, set()).add(digest)

    assert {tag: d for tag, d in digests.items() if len(d) > 1} == {}


def test_local_python_matches_ci_and_the_images() -> None:
    pinned = (REPO_ROOT / ".python-version").read_text().strip()
    ci = yaml.safe_load((REPO_ROOT / ".github/workflows/ci.yml").read_text())["env"]["PYTHON_VERSION"]
    dockerfile = (REPO_ROOT / "Dockerfile").read_text()

    assert pinned == ci
    assert f"dhi.io/python:{pinned}-debian13@" in dockerfile
    assert f"python:{pinned}-slim" in dockerfile


def test_every_dependabot_update_waits_seven_days() -> None:
    config = yaml.safe_load((REPO_ROOT / ".github/dependabot.yml").read_text())
    ecosystems = {update["package-ecosystem"]: update for update in config["updates"]}

    assert {"uv", "npm", "github-actions", "docker", "docker-compose"} <= set(ecosystems)
    assert {name: u.get("cooldown", {}).get("default-days") for name, u in ecosystems.items()} == dict.fromkeys(
        ecosystems, 7
    )
