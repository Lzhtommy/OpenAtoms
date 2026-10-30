SHELL := /bin/bash
.PHONY: install build sandboxd gateway bridge web image dev dev-full infra-up infra-down test test-server test-go protocol e2e

install:
	pnpm install
	cd services/server && uv sync
	cd templates/react-shadcn-vite && pnpm install

sandboxd:
	cd services/sandboxd && go build -o bin/sandboxd .

bridge:
	pnpm --filter @oa/bridge build

gateway: bridge
	cd apps/gateway && go build -o bin/gateway .

web:
	pnpm --filter @oa/web build

build: sandboxd gateway web

image:
	docker build -f images/sandbox/Dockerfile -t openatoms/sandbox:latest .

protocol:
	cd services/server && uv run python -m oa.protocol_gen ../../packages/protocol/src/generated.ts

# lite mode: zero external deps (SQLite, in-memory bus, local sandbox)
dev: sandboxd gateway
	./scripts/dev.sh

# full mode: Postgres + Redis + MinIO + Docker sandboxes (+ arq worker)
infra-up:
	docker compose -f deploy/docker-compose.yml up -d postgres redis s3 mailpit

infra-down:
	docker compose -f deploy/docker-compose.yml down

dev-full: sandboxd gateway infra-up
	OA_MODE=full OA_STORAGE=s3 OA_SANDBOX_PROVIDER=docker OA_QUEUE=arq OA_SMTP_HOST=localhost ./scripts/dev.sh

test-server:
	cd services/server && uv run pytest -q

test-go:
	cd services/sandboxd && go test ./...
	cd apps/gateway && go test ./...

test: test-go test-server

e2e:
	./scripts/e2e.sh
