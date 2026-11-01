# VoiceFlow Studio API. Build context: voiceflow-studio/ (see ../.dockerignore).
# Requires the Pipecat submodule: git submodule update --init voiceflow-studio/vendor/pipecat
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_LINK_MODE=copy \
    UV_COMPILE_BYTECODE=1 \
    UV_HTTP_TIMEOUT=120

WORKDIR /app/backend

# Official uv binary (no PyPI round-trip); pinned to the version that wrote uv.lock.
COPY --from=ghcr.io/astral-sh/uv:0.7.13 /uv /uvx /bin/

# The lock file resolves Pipecat from this sibling local source.
COPY vendor/pipecat /app/vendor/pipecat
RUN test -f /app/vendor/pipecat/pyproject.toml || { \
      echo "ERROR: voiceflow-studio/vendor/pipecat is empty."; \
      echo "Run: git submodule update --init voiceflow-studio/vendor/pipecat  (or: make setup)"; \
      exit 1; }

COPY backend/pyproject.toml backend/uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project

COPY backend /app/backend
RUN uv sync --frozen --no-dev && mkdir -p /data/artifacts

ENV PATH="/app/backend/.venv/bin:${PATH}" \
    ARTIFACTS_DIR=/data/artifacts

EXPOSE 8000

HEALTHCHECK --interval=10s --timeout=3s --start-period=30s --retries=6 \
  CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/api/health', timeout=2).status == 200 else 1)"

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
