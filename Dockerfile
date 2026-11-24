# syntax=docker/dockerfile:1.7
# Default: Docker Hardened Images (DHI). The runtime has no shell or package
# manager; dependencies are installed with the matching -dev image. To use
# stock images instead, set both to python:3.13-slim@sha256:e48f59cdba69a3cbd9ed2cf0f348d90d45a8d350d98134cf1070bc106ee9ed90 (see .env.example).
ARG PYTHON_BUILD_IMAGE=dhi.io/python:3.13-debian13-dev@sha256:ea5fd1cae2463b48c7627577584e582ce3d6d99ba3789723051df9a707f658b0
ARG PYTHON_RUNTIME_IMAGE=dhi.io/python:3.13-debian13@sha256:1fe1883f0c28d90b7364e6f069437801c9e8f150c886f02f74bfcefc9ea14591

FROM ghcr.io/astral-sh/uv:0.13.7@sha256:96f40d3904aad8d0bd010539001d36c8e2188b981e1334471a90c4a1d93bb490 AS uv

# Third-party dependencies only. This stage changes only when pyproject.toml or
# uv.lock change, so app commits reuse it. Every Python image in this repo
# (hello-api, todo-api, llm-backend, bff, ...) starts with this exact stage, so
# BuildKit resolves the lockfile once and stores the layer once for all of them.
#
# uv is bind-mounted rather than copied, so it never lands in a layer. Bytecode
# is compiled with hash-based invalidation: uv's --compile-bytecode embeds
# build timestamps, so every rebuild would produce a different layer digest.
# Compiling at all matters because compose runs every service read-only, so the
# interpreter can never write a .pyc and would otherwise recompile the whole
# dependency tree in memory on every start. Compile serially: with -j 0 the
# workers' interpreter state varies, marshal output varies with it, and the
# layer digest changes between otherwise identical builds.
FROM ${PYTHON_BUILD_IMAGE} AS deps
WORKDIR /app
ENV UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never
RUN --mount=from=uv,source=/uv,target=/usr/local/bin/uv \
    --mount=type=cache,target=/root/.cache/uv \
    --mount=type=bind,source=pyproject.toml,target=pyproject.toml \
    --mount=type=bind,source=uv.lock,target=uv.lock \
    uv sync --locked --no-dev --no-install-project \
    && /app/.venv/bin/python -m compileall -q --invalidation-mode unchecked-hash /app/.venv/lib

# App source, byte-compiled here because the runtime image has no shell and its
# filesystem is read-only. checked-hash, not unchecked: if anyone mounts edited
# source over /app/app, Python notices the change instead of running stale code.
# A compile error fails the build: app/ that will not compile is a broken image.
FROM deps AS app
COPY app ./app
RUN /app/.venv/bin/python -m compileall -q --invalidation-mode checked-hash app

FROM ${PYTHON_RUNTIME_IMAGE} AS runtime

ARG APP_UID=65532
ARG APP_GID=65532

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH="/app/.venv/bin:/opt/python/bin:$PATH" \
    PORT=8000 \
    HOME=/tmp

WORKDIR /app

# Three independent layers, ordered from least to most frequently changed.
# --link makes each layer independent of the ones below it, so a base image
# update or an app-only commit does not rebuild or re-push the others.
# Files stay owned by root: the service user can read its code and tenant
# documents but cannot modify them, even when the container is not read-only.
COPY --link --from=deps /app/.venv /app/.venv
COPY --link --from=app /app/app /app/app
# examples/ holds the tenant documents the compose stacks point
# APIM_CONFIG_SOURCE_PATH at, so it is runtime input, not sample code.
COPY --link examples /app/examples

EXPOSE 8000

USER ${APP_UID}:${APP_GID}

# The gateway serves /apim/startup only once the lifespan has finished wiring
# the management plane, which is the point at which it can actually take traffic.
# --start-interval probes every second while starting, so services that wait on
# `condition: service_healthy` start as soon as the gateway is ready instead of
# up to one full --interval later. Needs Docker Engine 25 or later.
HEALTHCHECK --interval=10s --timeout=3s --start-period=30s --start-interval=1s --retries=3 \
    CMD ["/app/.venv/bin/python", "-c", \
         "import urllib.request,sys,os; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:'+os.environ.get('PORT','8000')+'/apim/startup', timeout=2).status==200 else 1)"]

CMD ["/app/.venv/bin/python", "-m", "app.run_server"]
