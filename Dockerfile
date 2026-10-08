# ARK — multi-stage production image.
# Stage 1 builds the frontend; stage 2 installs runtime deps and ships both.
FROM node:22-slim AS frontend
WORKDIR /build/frontend
COPY ark/frontend/package*.json ./
RUN npm ci
COPY ark/frontend/ .
RUN npm run build

FROM python:3.12-slim
ENV ARK_HOME=/app \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY ark ./ark
COPY migrations ./migrations
COPY alembic.ini .
COPY scripts ./scripts
COPY --from=frontend /build/frontend/dist ./ark/frontend/dist
EXPOSE 8080
VOLUME ["/app/data"]
HEALTHCHECK --interval=30s --timeout=3s --retries=3 \
  CMD python -c "import urllib.request;urllib.request.urlopen('http://127.0.0.1:8080/healthz',timeout=2)" || exit 1
CMD ["python", "-m", "ark", "serve", "--host", "0.0.0.0"]