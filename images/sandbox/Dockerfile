# syntax=docker/dockerfile:1.7
# OpenAtoms sandbox image (docs/CONTRACT.md §5, §8).
# Build from the repo root:
#   docker build -f images/sandbox/Dockerfile -t openatoms/sandbox:latest .

# ---------------------------------------------------------------- sandboxd
FROM golang:1.23-bookworm AS sandboxd
WORKDIR /src
COPY services/sandboxd/go.mod ./
COPY services/sandboxd/*.go services/sandboxd/*.py ./
RUN CGO_ENABLED=0 go build -trimpath -ldflags="-s -w" -o /out/sandboxd .

# ---------------------------------------------------------------- runtime
# Node 22 LTS: pnpm 11 requires Node >= 22.13.
FROM node:22-bookworm-slim

ARG PNPM_VERSION=11.9.0
ENV DEBIAN_FRONTEND=noninteractive \
    LANG=C.UTF-8 \
    VIRTUAL_ENV=/opt/venv \
    PATH=/opt/venv/bin:/home/user/.local/share/pnpm/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin \
    UV_LINK_MODE=copy \
    PNPM_HOME=/home/user/.local/share/pnpm \
    CI=1

RUN apt-get update \
 && apt-get install -y --no-install-recommends \
      python3 python3-venv ca-certificates curl git tini tree procps less unzip \
 && rm -rf /var/lib/apt/lists/*

COPY --from=ghcr.io/astral-sh/uv:0.5.11 /uv /uvx /usr/local/bin/

# Non-root user `user` (reuse uid 1000 of the base image's `node` user).
RUN usermod -l user -d /home/user -m node && groupmod -n user node \
 && mkdir -p /workspace /opt/venv && chown -R user:user /workspace /opt/venv

RUN npm install -g pnpm@${PNPM_VERSION} && npm cache clean --force

# Python data stack in a user-writable venv; `pip` is a thin wrapper over `uv pip`.
USER user
RUN uv venv /opt/venv --python /usr/bin/python3 \
 && uv pip install --no-cache numpy pandas matplotlib requests \
 && printf '#!/bin/sh\necho "pip is deprecated in this environment, using '"'"'uv pip'"'"' as replacement" >&2\nexec uv pip "$@"\n' > /opt/venv/bin/pip \
 && chmod +x /opt/venv/bin/pip && cp /opt/venv/bin/pip /opt/venv/bin/pip3

# Pin the store so installs into the bind-mounted /workspace (a different
# filesystem) reuse it instead of creating /workspace/.pnpm-store.
RUN pnpm config set store-dir /home/user/.local/share/pnpm/store --global \
 && pnpm config set package-import-method clone-or-copy --global

# Pre-warm the pnpm store with the default template's dependencies so new
# workspaces install offline in about a second.
COPY --chown=user:user templates/react-shadcn-vite /tmp/tpl
RUN cd /tmp/tpl && pnpm install --frozen-lockfile && pnpm build && cd / && rm -rf /tmp/tpl

# Same for the mobile (Expo / React Native Web) template: a new mobile project installs offline and
# `expo export --platform web` works without network access.
COPY --chown=user:user templates/expo-app /tmp/tpl-expo
RUN cd /tmp/tpl-expo && pnpm install --frozen-lockfile && pnpm build && cd / && rm -rf /tmp/tpl-expo

COPY --from=sandboxd /out/sandboxd /usr/local/bin/sandboxd

WORKDIR /workspace
EXPOSE 49999
ENTRYPOINT ["/usr/bin/tini", "--", "sandboxd", "--listen", ":49999", "--root", "/workspace"]
