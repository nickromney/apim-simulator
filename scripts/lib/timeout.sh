#!/usr/bin/env bash
# shellcheck shell=bash
#
# Bounded execution that works on every host this repo runs on.
#
# GNU coreutils `timeout` is not on stock macOS, and it is not on every Arch or
# minimal Ubuntu image either. Homebrew's coreutils installs it as `gtimeout`
# but is not a prerequisite here.
#
# Ported from the sibling `platform` repository, where a bare `timeout` made a
# bats suite skip on macOS rather than run. The same trap caught a mutation run
# here: `timeout 3000 mutmut run ...` reported "command not found" for every
# module and still exited 0, so a batch that scored nothing looked like a batch
# that passed.
#
# So: prefer timeout, then gtimeout, then a portable shell fallback. The
# fallback returns 124 on expiry, matching coreutils, so callers can branch on
# the exit status without caring which path ran.

run_with_timeout() {
  local seconds="$1"
  shift
  local pid=""
  local start=""
  local elapsed=""
  local rc=0

  if command -v timeout >/dev/null 2>&1; then
    timeout "${seconds}" "$@"
    return $?
  fi

  if command -v gtimeout >/dev/null 2>&1; then
    gtimeout "${seconds}" "$@"
    return $?
  fi

  "$@" &
  pid=$!
  start="$(date +%s)"

  while kill -0 "${pid}" >/dev/null 2>&1; do
    elapsed=$(($(date +%s) - start))
    if [ "${elapsed}" -ge "${seconds}" ]; then
      kill "${pid}" >/dev/null 2>&1 || true
      wait "${pid}" >/dev/null 2>&1 || true
      return 124
    fi
    sleep 1
  done

  wait "${pid}" || rc=$?
  return "${rc}"
}

# Report which implementation would be used, for diagnostics and tests.
timeout_implementation() {
  if command -v timeout >/dev/null 2>&1; then
    printf 'timeout\n'
  elif command -v gtimeout >/dev/null 2>&1; then
    printf 'gtimeout\n'
  else
    printf 'shell-fallback\n'
  fi
}
