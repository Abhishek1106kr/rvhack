# ARC control room: production Next.js build, run from the standalone server.
# Built from the repo root:  docker build -f frontend/Dockerfile .

FROM node:24-slim AS build
RUN npm install -g pnpm@12.3.4
WORKDIR /repo
COPY package.json pnpm-workspace.yaml pnpm-lock.yaml ./
COPY frontend/package.json frontend/
RUN pnpm install --frozen-lockfile --filter frontend
COPY frontend frontend
ENV NEXT_TELEMETRY_DISABLED=1
RUN pnpm --filter frontend build

FROM node:24-slim
WORKDIR /srv
ENV NODE_ENV=production \
    NEXT_TELEMETRY_DISABLED=1 \
    PORT=3000 \
    HOSTNAME=0.0.0.0
COPY --from=build /repo/frontend/.next/standalone ./
COPY --from=build /repo/frontend/.next/static ./frontend/.next/static
COPY --from=build /repo/frontend/public ./frontend/public
USER node
EXPOSE 3000
CMD ["node", "frontend/server.js"]
