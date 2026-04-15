#!/usr/bin/env bats

setup() {
  export REPO_ROOT
  REPO_ROOT="$(cd "$(dirname "${BATS_TEST_FILENAME}")/../.." && pwd)"
  export SCRIPT="${REPO_ROOT}/scripts/release_version.sh"

  export RELEASE_VERSION
  RELEASE_VERSION="$(
    uv run --project "$REPO_ROOT" python - <<'PY'
import tomllib
from pathlib import Path

print(tomllib.loads(Path("pyproject.toml").read_text(encoding="utf-8"))["project"]["version"])
PY
  )"

  export RELEASE_COMMIT
  RELEASE_COMMIT="$(git -C "$REPO_ROOT" rev-parse HEAD)"

  export METADATA_FILE="$BATS_TEST_TMPDIR/apim-simulator.vendor.json"
  cat >"$METADATA_FILE" <<EOF
{"upstream":{"resolved_commit":"${RELEASE_COMMIT}"}}
EOF
}

@test "release_version resolves the current checkout" {
  run "$SCRIPT" --execute --source "$REPO_ROOT" --commit "$RELEASE_COMMIT"

  [ "$status" -eq 0 ]
  [[ "$output" == "v${RELEASE_VERSION}" ]]
}

@test "release_version resolves vendored metadata" {
  run "$SCRIPT" --execute --source "$REPO_ROOT" --metadata "$METADATA_FILE"

  [ "$status" -eq 0 ]
  [[ "$output" == "v${RELEASE_VERSION}" ]]
}
