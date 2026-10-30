FROM node:20-bookworm-slim AS build
RUN corepack enable
WORKDIR /repo
COPY . .
RUN pnpm install --frozen-lockfile=false && pnpm --filter @oa/web build
FROM node:20-bookworm-slim
RUN corepack enable
WORKDIR /repo
COPY --from=build /repo /repo
WORKDIR /repo/apps/web
EXPOSE 3000
CMD ["pnpm", "start"]
