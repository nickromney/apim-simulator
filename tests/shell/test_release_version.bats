#!/usr/bin/env bats

setup() {
  export REPO_ROOT
  REPO_ROOT="$(cd "$(dirname "${BATS_TEST_FILENAME}")/../.." && pwd)"
  export SCRIPT="${REPO_ROOT}/scripts/release_version.sh"

  export RELEASE_COMMIT
  RELEASE_COMMIT="$(git -C "$REPO_ROOT" rev-parse HEAD)"

  export RELEASE_VERSION
  RELEASE_VERSION="$(
    uv run --project "$REPO_ROOT" python - "$REPO_ROOT" "$RELEASE_COMMIT" <<'PY'
import subprocess
import sys
import tomllib

repo = sys.argv[1]
commit = sys.argv[2]
payload = subprocess.check_output(
    ["git", "-C", repo, "show", f"{commit}:pyproject.toml"],
    text=True,
)
print(tomllib.loads(payload)["project"]["version"])
PY
  )"

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

@test "release_version resolves a linked worktree, whose .git is a file" {
  local worktree="$BATS_TEST_TMPDIR/linked-worktree"
  git -C "$REPO_ROOT" worktree add --quiet --detach "$worktree" "$RELEASE_COMMIT"

  run "$SCRIPT" --execute --source "$worktree" --commit "$RELEASE_COMMIT"
  git -C "$REPO_ROOT" worktree remove --force "$worktree"

  [ "$status" -eq 0 ]
  [[ "$output" == "v${RELEASE_VERSION}" ]]
}

@test "release_version rejects a directory that is not a git checkout" {
  run "$SCRIPT" --execute --source "$BATS_TEST_TMPDIR" --commit "$RELEASE_COMMIT"

  [ "$status" -ne 0 ]
  [[ "$output" == *"source is not a git checkout"* ]]
}
