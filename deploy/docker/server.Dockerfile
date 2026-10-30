FROM python:3.12-slim
RUN apt-get update && apt-get install -y --no-install-recommends git curl ca-certificates nodejs npm \
    && npm i -g pnpm@9 && rm -rf /var/lib/apt/lists/*
COPY --from=ghcr.io/astral-sh/uv:0.5 /uv /usr/local/bin/uv
WORKDIR /app/services/server
COPY services/server/pyproject.toml ./
RUN uv sync --no-dev --no-install-project
COPY services/server ./
COPY templates /app/templates
RUN uv sync --no-dev
# Browser tools (browser_*, checkui_run, version covers) run in the api/worker process, not in sandboxes.
ENV PLAYWRIGHT_BROWSERS_PATH=/ms-playwright
RUN .venv/bin/playwright install --with-deps chromium
# Run as uid 1000 like the sandbox image's `user`: api/worker and sandboxes both write workspaces on the shared PVC,
# and sandboxes (uid 1000) must be able to write what the platform created (template copy, restores, visual edits).
RUN useradd -u 1000 -m user && mkdir -p /data && chown user:user /data
USER 1000
ENV OA_TEMPLATE_DIR=/app/templates/react-shadcn-vite PATH=/app/services/server/.venv/bin:$PATH
EXPOSE 8000
CMD ["uvicorn", "oa.main:app", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers"]
