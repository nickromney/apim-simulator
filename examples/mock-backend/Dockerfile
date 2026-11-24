ARG PYTHON_RUNTIME_IMAGE=dhi.io/python:3.13-debian13@sha256:1fe1883f0c28d90b7364e6f069437801c9e8f150c886f02f74bfcefc9ea14591
FROM ${PYTHON_RUNTIME_IMAGE}

ARG APP_UID=65532
ARG APP_GID=65532

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PORT=8080 \
    HOME=/tmp

WORKDIR /app

# Root-owned, so the service user can read but not modify it.
COPY server.py .

EXPOSE 8080

USER ${APP_UID}:${APP_GID}

CMD ["python", "server.py"]
