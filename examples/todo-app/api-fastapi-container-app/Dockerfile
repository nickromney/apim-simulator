# syntax=docker/dockerfile:1.7
# The uv and deps stages match the gateway's Dockerfile at the repository root
# line for line, so BuildKit resolves the lockfile once and every Python image
# shares one dependency layer. Keep them in step when you change either file.
ARG PYTHON_BUILD_IMAGE=dhi.io/python:3.13-debian13-dev@sha256:ea5fd1cae2463b48c7627577584e582ce3d6d99ba3789723051df9a707f658b0
ARG PYTHON_RUNTIME_IMAGE=dhi.io/python:3.13-debian13@sha256:1fe1883f0c28d90b7364e6f069437801c9e8f150c886f02f74bfcefc9ea14591

FROM ghcr.io/astral-sh/uv:0.13.7@sha256:96f40d3904aad8d0bd010539001d36c8e2188b981e1334471a90c4a1d93bb490 AS uv

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

# Source, byte-compiled for the read-only runtime filesystem.
FROM deps AS app
COPY app ./app
COPY examples/todo-app/api-fastapi-container-app/main.py ./main.py
RUN /app/.venv/bin/python -m compileall -q --invalidation-mode checked-hash app main.py

FROM ${PYTHON_RUNTIME_IMAGE} AS runtime

ARG APP_UID=65532
ARG APP_GID=65532

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH="/app/.venv/bin:/opt/python/bin:$PATH" \
    PORT=8000 \
    HOME=/tmp

WORKDIR /app

# Root-owned, so the service user can read but not modify its code.
COPY --link --from=deps /app/.venv /app/.venv
COPY --link --from=app /app/app /app/app
COPY --link --from=app /app/main.py /app/main.py
COPY --link --from=app /app/__pycache__ /app/__pycache__

EXPOSE 8000

USER ${APP_UID}:${APP_GID}

CMD ["/app/.venv/bin/uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000", "--no-access-log"]
