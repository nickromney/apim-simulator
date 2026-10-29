ARG NGINX_RUNTIME_IMAGE=dhi.io/nginx:1.29.5-debian13
# Node 22.23.4 includes the 2026-10-21 security fixes (including CVE-2026-111497
# and CVE-2026-105482). This Node image is build-only; Nginx serves static output.
# Release notes: https://nodejs.org/en/blog/release/v22.23.4/
# The official Alpine tag was published 2026-10-22T02:39:08Z; the isolated
# container proof is pending its seven-day cooldown or a reviewed digest exception.
ARG TODO_FRONTEND_BUILD_IMAGE=node:22.23.4-alpine3.24@sha256:fe4f6d74e20370acb04ad889077d14d0f64a9c8ea6a42bc60cddf1e844699914
FROM ${TODO_FRONTEND_BUILD_IMAGE} AS builder

# Node is build-only; the served artifact runs in the nginx stage below.
WORKDIR /app

COPY package.json package-lock.json .npmrc ./
COPY scripts/check-node-security-floor.mjs ./scripts/check-node-security-floor.mjs
RUN ["node", "scripts/check-node-security-floor.mjs"]
RUN npm ci

COPY . .
RUN npm run build

FROM --platform=$BUILDPLATFORM golang:1.26.1-alpine3.23 AS runtime-config-builder

WORKDIR /src
ARG TARGETOS
ARG TARGETARCH
COPY scripts/runtime-config-entrypoint.go ./
RUN CGO_ENABLED=0 GOOS=${TARGETOS:-linux} GOARCH=${TARGETARCH} go build -trimpath -ldflags='-s -w' -o /out/runtime-config-entrypoint runtime-config-entrypoint.go

FROM ${NGINX_RUNTIME_IMAGE}

ARG NGINX_UID=101
ARG NGINX_GID=101

COPY --chown=${NGINX_UID}:${NGINX_GID} nginx-main.conf /etc/nginx/nginx.conf
COPY --chown=${NGINX_UID}:${NGINX_GID} nginx.conf /etc/nginx/conf.d/default.conf
COPY --chown=${NGINX_UID}:${NGINX_GID} --from=builder /app/dist /usr/share/nginx/html
COPY --from=runtime-config-builder /out/runtime-config-entrypoint /usr/local/bin/runtime-config-entrypoint

EXPOSE 8080

USER ${NGINX_UID}:${NGINX_GID}

ENTRYPOINT ["/usr/local/bin/runtime-config-entrypoint"]
CMD ["nginx", "-g", "daemon off;"]
