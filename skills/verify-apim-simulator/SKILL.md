---
name: verify-apim-simulator
description: Bring up an isolated APIM simulator Docker stack (slot 7, gateway on 127.0.0.1:8700), send recorded HTTP requests through the published gateway port, run the repo's smoke script, and query the tenant-key management API with the apimsim CLI. Use when you need to prove a gateway, policy, subscription or management-API change against the running containers.
---

# Verify the APIM simulator

The user surface is HTTP through the published gateway port. Clients such as
curl, Bruno or browsers call `/<api-path>/...` with APIM headers like
`Ocp-Apim-Subscription-Key`. Operators use the tenant-key management API
(`/apim/management/...`) or its thin CLI, `python -m app.cli` (apimsim). This
skill follows AGENTS.md: verify Docker stacks through published localhost
ports, not inside containers.

Helper: `skills/verify-apim-simulator/scripts/control.sh`. It is built on
verify-kit. The shared library (`scripts/verify-lib.sh`), the contract tests
(`tests/contract.bats`) and the make targets (`verify.mk`) are vendored from
`harnesses-private/nickromney/verify-kit` and pinned by `verify-kit.lock`. Do
not edit the vendored files; change the kit and re-vendor. `control.sh` holds
only this app's config, hooks and recipes. Run bare or with `--dry-run`, it
prints help and a preview, and it acts only with `--execute`. It runs under
macOS `/bin/bash` (3.2). Below, `C` is a shell function that adds `--execute`.

```bash
make verify            # full proven cycle: launch hello-subscription, assert, smoke, cleanup
make verify-dry-run    # print what verify would do, change nothing
make verify-test       # shellcheck on the scripts and BATS files, then the contract and app tests, under /bin/bash 3.2
```

Tests: `tests/contract.bats` is the vendored kit contract (interface, previews,
exit codes, Bash 3.2 rules, lock drift). `tests/apim-simulator.bats` holds this
app's own cases: the slot-0 refusal for launch and verify, with docker, make and
uv stubbed to record any call; slot and stack validation; the cli and smoke
refusals on the wrong stack; and the default `.run/verify` evidence root.
`vendor.sh --check skills/verify-apim-simulator` reports drift from the kit.

`verify` asserts: `GET /api/health` without a key → 401 (body names the
missing key); `GET /api/hello?name=verify` with `hello-demo-key` → 200 with
body `{"message":"hello, verify"}` and the header `x-hello-policy: applied`;
and `scripts/smoke_hello.py` exits 0. It prints `VERIFY PASS` only when all
pass. Cleanup runs on success and failure, and evidence is kept.

## Launch

```bash
C() { skills/verify-apim-simulator/scripts/control.sh --execute "$@"; }   # bash and zsh
C launch                      # hello-subscription: gateway + hello-api, subscription required
C launch hello                # anonymous hello API
C launch gateway              # core gateway + mock backend, examples/basic.json, management API on
```

`launch` runs `STACK_SLOT=7 make up-<stack>`. That gives the compose project
`apim-simulator-<hello|core>-s7` and the gateway at `http://127.0.0.1:8700`.
Change it with `VERIFY_SLOT=N`, which uses port 8000+100·N. Slot 0, the
user's `make up`, is refused. It's ready when `/apim/health` returns
`{"status":"healthy"}`. That took about 25 s with a warm Docker cache, and
the hello stack uses about 125 MiB of RAM. Run one stack per run.

## Doctor

```bash
C doctor
```

Read-only. It checks that `apim-simulator` is running in our compose
project, that the container publishing the port is ours, and that
`/apim/health` answers. It also prints the launch revision next to the
current HEAD.

## Drive

```bash
C request GET /api/health                                             # expect 401 on hello-subscription
C request GET '/api/hello?name=verify' 'Ocp-Apim-Subscription-Key: hello-demo-key'
C smoke                                                               # scripts/smoke_hello.py in the stack's mode
C cli status                                                          # gateway stack only
C cli apis
```

Stable handles come from the stack configs:

- Hello APIs are at `/api/health` and `/api/hello?name=`. The subscription
  keys are `hello-demo-key` and `hello-demo-key-secondary`, from
  `examples/hello-api/apim.subscription.json`. Its outbound policy sets
  `x-hello-policy: applied`.
- On the gateway stack, the management API uses tenant key
  `local-dev-tenant-key` from `examples/basic.json`. `cli commands` describes
  every apimsim command's effect without contacting the gateway.

## Evidence

Everything goes under `.run/verify/<run>/evidence/`, which is git-ignored:

- `revision.txt`, `launch.log` and `health-probe.log` (each readiness probe's
  curl stderr) are written at launch.
- `compose.log` and `down.log` are written at cleanup, captured before
  `down`.
- `<HHMMSS-NNN>-request/` holds `request.txt`, `status.txt`,
  `response-headers.txt` and `response-body.txt`.
- `<HHMMSS-NNN>-smoke/` and `<HHMMSS-NNN>-cli/` hold stdout, stderr and the
  exit code (plus `command.txt` for the CLI).

Proof standards:

- Every claim cites a request made through the published port.
- For policy changes, show the status code and the policy-produced header or
  body. Pair each positive case with its negative, for example no key → 401
  and valid key → 200.
- Management mutations such as `put-api` or `set-policy` need a GET read-back
  through `cli` and a gateway request that exercises the change.

## Cleanup

```bash
C cleanup
```

Saves the compose logs, then runs `docker compose -p <our project> down
--remove-orphans`. It never touches other projects or slots. It deletes
`<run>/state` and keeps the evidence.

## Gotchas

- Without `--execute`, nothing runs and you only get a preview. The pre-push
  shell audit (`make lint-shell`) requires that interface for every executable
  `.sh` file. Use the `C()` function rather than a `C="… --execute"` variable,
  because zsh does not word-split unquoted variables.
- `make up-*` runs `--build`. Images are tagged globally
  (`apim-simulator:latest`, `apim-simulator-hello-api:latest`), so a
  verification launch also re-tags the images that the user's slot-0 stack
  uses.
- The hello configs have no `tenant_access`. The management API and `cli`
  are unavailable there, and the script refuses rather than reporting an
  error as a result.
- The first build pulls `dhi.io/python` base images. Offline or
  unauthenticated pulls fail in `launch.log`.
- Docker Desktop memory is shared with whatever else is running. Check
  `docker stats --no-stream` before launching the OTEL, OIDC (Keycloak) or
  todo stacks. This skill doesn't launch them.
