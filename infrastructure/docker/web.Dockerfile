# syntax=docker/dockerfile:1
# Next.js standalone server. The API origin is fixed at build time (rewrites are compiled into server.js).
FROM node:24-alpine AS deps
WORKDIR /web
COPY frontend/package.json frontend/package-lock.json ./
RUN --mount=type=cache,target=/root/.npm npm ci --no-audit --no-fund

FROM deps AS build
ARG TRIPSCOPE_API_ORIGIN=http://api:8000
ENV TRIPSCOPE_API_ORIGIN=${TRIPSCOPE_API_ORIGIN} NEXT_TELEMETRY_DISABLED=1
COPY frontend/ ./
RUN npm run build

FROM node:24-alpine
ENV NODE_ENV=production NEXT_TELEMETRY_DISABLED=1 PORT=8080 HOSTNAME=0.0.0.0
WORKDIR /app
RUN addgroup -S web && adduser -S web -G web
COPY --from=build --chown=web:web /web/.next/standalone ./
COPY --from=build --chown=web:web /web/.next/static ./.next/static
COPY --from=build --chown=web:web /web/public ./public
USER web
EXPOSE 8080
HEALTHCHECK --interval=10s --timeout=3s --retries=6 CMD wget -q -O /dev/null http://127.0.0.1:8080/login || exit 1
CMD ["node", "server.js"]
