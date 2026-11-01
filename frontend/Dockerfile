# Artemis dashboard (Vite build served by nginx). Build context: frontend/.
# VITE_* values are inlined at build time and must be browser-reachable URLs.
FROM node:22-alpine AS build
WORKDIR /app
ARG VITE_STUDIO_UI_URL=http://localhost:3000
ARG VITE_STUDIO_API_URL=http://localhost:8000
ENV VITE_STUDIO_UI_URL=${VITE_STUDIO_UI_URL} \
    VITE_STUDIO_API_URL=${VITE_STUDIO_API_URL}
COPY package*.json ./
RUN npm ci --no-audit --no-fund
COPY . .
RUN npm run build

FROM nginx:1.27-alpine
COPY nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=build /app/dist /usr/share/nginx/html
EXPOSE 80
HEALTHCHECK --interval=10s --timeout=3s --start-period=5s --retries=6 \
  CMD wget -q -O /dev/null http://127.0.0.1/ || exit 1
