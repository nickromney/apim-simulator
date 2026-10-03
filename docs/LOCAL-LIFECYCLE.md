# The cheap local test loop and state lifecycle

The default examples run on your Docker host without an Azure account or Azure resource charges. They still use local CPU, memory and disk. Start with `make up-gateway` for the gateway and mock backend, or `make up` to include the operator console. Add OIDC or telemetry when the test needs them.

## Reuse the running stack

```bash
make up
curl -i http://localhost:8000/api/echo
```

The `make up` targets request a build. Once images exist, starting the core stack without requesting another build uses:

```bash
docker compose -f compose.yml -f compose.public.yml -f compose.ui.yml up -d
```

This command is for the default core stack. When using `STACK_SLOT`, alternate overlays or an example Makefile, keep the same project name, environment and Compose files used to start that stack. `make -n up` prints the corresponding command without running it.

Edit APIs and policies in the console, make requests through the gateway, and inspect traces. Management writes take effect in the running process, so this loop does not need a container rebuild. Changes to Python code, bundled assets or baked-in example source files do need a rebuild.

## What survives?

| State | Default location | Lifecycle |
| --- | --- | --- |
| Initial fixture | `APIM_CONFIG_SOURCE_PATH`, baked into the image | Seeds a missing runtime file at startup; rebuilding incorporates fixture edits |
| Management changes, subscriptions, portal customization | `APIM_CONFIG_PATH`, normally `/tmp/apim-config.json` | Written to the running container's `tmpfs`; lost on container stop/restart or replacement |
| Request traces, policy caches and runtime counters | Process memory | Lost when the process restarts |
| Keycloak accounts and provider state | `keycloak-data` named volume in OIDC overlays | Survives container recreation and ordinary `make down` |
| LGTM telemetry state | Named volumes in OTEL overlays | Survives ordinary `make down`; inspect the chosen overlay for its volumes |

The startup helper [run_server.py](../app/run_server.py) seeds only when the runtime file is absent. A process restart while the same filesystem remains mounted can reuse that file; Docker stopping the container unmounts its `tmpfs`. A configured persistent writable location has different behaviour. See [compose.yml](../compose.yml) and [the OIDC overlay](../compose.oidc.yml).

## Save edits before stopping

For the default core stack, copy the runtime document out before a restart:

```bash
umask 077
mkdir -p .run
docker compose -f compose.yml -f compose.public.yml -f compose.ui.yml \
  cp apim-simulator:/tmp/apim-config.json .run/saved-apim-config.json
```

The document can contain credentials, subscription keys and resolved named values. `.run/` is gitignored; keep this backup local. Alternate examples use different runtime paths. This is a config backup, not a backup of traces, counters, backend data or provider volumes. To reuse it as a seed, supply it as `APIM_CONFIG_SOURCE_PATH` at a readable container path using a local Compose bind mount; preserve any environment placeholders and backend service names the saved configuration expects.

## Reset deliberately

To reset the default core gateway's temporary config and process state, without removing named provider/telemetry volumes:

```bash
docker compose -f compose.yml -f compose.public.yml -f compose.ui.yml \
  up -d --force-recreate --no-deps apim-simulator
```

This discards unsaved management edits and reseeds from the fixture in the existing image. Rebuild first if that fixture changed on disk. It leaves the mock backend running. It is not a reset of every example or backend database.

Use the lifecycle commands for the chosen example when you need a broader reset. Adding `--volumes` to `docker compose down` also deletes that project's declared named volumes: use it only when you intend to discard that stack's provider or telemetry data. Avoid host-wide volume pruning for a local test reset.

## Network expectations

Initial builds and installs can contact image registries, Python/npm registries and upstream tooling. Docker Hardened Images may require registry authentication. With images built and local containers running, the basic fixture's request path is local; the bundled Scalar client needs no CDN. A configured remote backend, OIDC discovery URL, callout or telemetry exporter can still make outbound calls. `sslip.io` names also depend on DNS unless a local mapping is available. Local-first does not imply network isolation.
