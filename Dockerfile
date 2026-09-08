# syntax=docker/dockerfile:1.7
ARG PYTHON_BUILD_IMAGE=dhi.io/python:3.13-debian13-dev
ARG PYTHON_RUNTIME_IMAGE=dhi.io/python:3.13-debian13
FROM ${PYTHON_BUILD_IMAGE} AS builder

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

COPY --from=ghcr.io/astral-sh/uv:0.10.4 /uv /usr/local/bin/uv

COPY pyproject.toml uv.lock ./

# --compile-bytecode matters more here than it usually does. Every compose
# service runs with `read_only: true`, so the container can never write a .pyc:
# without this the interpreter recompiles the whole dependency tree in memory on
# every single start. Baking the bytecode in trades image size for startup time.
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --link-mode=copy --no-dev --no-install-project --compile-bytecode

FROM ${PYTHON_RUNTIME_IMAGE}

ARG APP_UID=65532
ARG APP_GID=65532

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH="/app/.venv/bin:/opt/python/bin:$PATH" \
    PORT=8000 \
    HOME=/tmp

WORKDIR /app

COPY --chown=${APP_UID}:${APP_GID} --from=builder /app/.venv /app/.venv
COPY --chown=${APP_UID}:${APP_GID} app ./app
# examples/ holds the tenant documents the compose stacks point
# APIM_CONFIG_SOURCE_PATH at, so it is runtime input, not sample code.
COPY --chown=${APP_UID}:${APP_GID} examples ./examples

# Same reason as the dependency tree above: the app's own modules cannot be
# cached at runtime on a read-only filesystem. This is deliberately not
# best-effort -- app/ that will not compile is a broken image, not a warning.
#
# Exec form, not shell form. The hardened runtime image ships no /bin/sh, so a
# shell-form RUN fails with `stat /bin/sh: no such file or directory`.
RUN ["/app/.venv/bin/python", "-m", "compileall", "-q", "/app/app"]

EXPOSE 8000

USER ${APP_UID}:${APP_GID}

# The gateway serves /apim/startup only once the lifespan has finished wiring
# the management plane, which is the point at which it can actually take traffic.
HEALTHCHECK --interval=10s --timeout=3s --start-period=15s --retries=3 \
    CMD ["/app/.venv/bin/python", "-c", \
         "import urllib.request,sys,os; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:'+os.environ.get('PORT','8000')+'/apim/startup', timeout=2).status==200 else 1)"]

CMD ["/app/.venv/bin/python", "-m", "app.run_server"]
