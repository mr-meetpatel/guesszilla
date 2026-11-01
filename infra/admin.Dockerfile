FROM node:22.22.3-alpine AS builder
RUN npm install --global pnpm@12.4.2
WORKDIR /app
COPY package.json pnpm-lock.yaml pnpm-workspace.yaml ./
COPY apps/admin/package.json apps/admin/package.json
RUN pnpm install --frozen-lockfile
COPY apps/admin apps/admin
RUN pnpm build

FROM nginxinc/nginx-unprivileged:1.28-alpine
ENV API_UPSTREAM=web:8000 NGINX_ENTRYPOINT_LOCAL_RESOLVERS=1
COPY infra/nginx.conf.template /etc/nginx/templates/default.conf.template
COPY --from=builder /app/apps/admin/dist /usr/share/nginx/html
EXPOSE 8080
HEALTHCHECK --interval=30s --timeout=5s CMD wget -q -O /dev/null http://127.0.0.1:8080/health/live || exit 1
