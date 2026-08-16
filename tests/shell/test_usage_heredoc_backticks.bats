#!/usr/bin/env bats
#
# `cat <<EOF` (unquoted) is needed wherever usage or note text interpolates a
# variable, but it also makes every backtick in that text live command
# substitution. scripts/generate_walkthroughs.sh wrote a documentation sentence
# containing `make up-otel` inside an unquoted heredoc, so generating the docs
# brought up the LGTM stack and spliced 128 lines of docker build log into
# docs/walkthrough-core-stacks.md and the doc split from it.
#
# Every other note in that generator uses <<'EOF' and is safe. The one that
# needed $GRAFANA_BASE_URL switched to <<EOF and silently gained execution.
#
# shellcheck reports this as SC2006, a style code, and the pre-commit hook only
# runs shellcheck over staged files -- so an untouched file never gets rechecked.

setup() {
  export REPO_ROOT
  REPO_ROOT="$(cd "$(dirname "${BATS_TEST_FILENAME}")/../.." && pwd)"
}

@test "no unquoted heredoc contains an unescaped backtick" {
  local offenders=""

  while IFS= read -r file; do
    [ -n "${file}" ] || continue
    local hits
    hits="$(
      awk '
        /<<EOF$/ { inh = 1; next }
        /^EOF$/  { inh = 0 }
        inh {
          line = $0
          gsub(/\\`/, "", line)
          if (line ~ /`/) { print FILENAME ":" FNR }
        }
      ' "${REPO_ROOT}/${file}"
    )"
    [ -z "${hits}" ] || offenders="${offenders}${hits}"$'\n'
  done < <(cd "${REPO_ROOT}" && git ls-files -- '*.sh')

  if [ -n "${offenders}" ]; then
    printf 'unescaped backtick inside an unquoted heredoc (runs as a command):\n%s\n' "${offenders}" >&2
    printf 'Escape as \\` or switch the heredoc to <<%s.\n' "'EOF'" >&2
  fi

  [ -z "${offenders}" ]
}

@test "generated walkthroughs carry no spliced build-log output" {
  # The symptom the bug left behind: docker buildx progress lines sitting in
  # prose, outside any fenced code block. Captured command output belongs inside
  # a fence; anything else means a heredoc executed something.
  local offenders=""

  while IFS= read -r file; do
    [ -n "${file}" ] || continue
    local hits
    hits="$(
      awk '
        /^```/ { infence = !infence; next }
        !infence && /^#[0-9]+ (DONE|CACHED) / { print FILENAME ":" FNR }
      ' "${REPO_ROOT}/${file}"
    )"
    [ -z "${hits}" ] || offenders="${offenders}${hits}"$'\n'
  done < <(cd "${REPO_ROOT}" && git ls-files -- 'docs/walkthrough-*.md')

  if [ -n "${offenders}" ]; then
    printf 'docker build output found outside a code fence:\n%s\n' "${offenders}" >&2
  fi

  [ -z "${offenders}" ]
}
