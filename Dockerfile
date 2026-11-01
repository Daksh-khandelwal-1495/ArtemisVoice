# Artemis API (causal debugger). Build context: repo root (see .dockerignore,
# which excludes voiceflow-studio/, frontend/ and all env files).
FROM python:3.12-slim

WORKDIR /app
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_DEFAULT_TIMEOUT=120 PIP_RETRIES=5
COPY pyproject.toml ./
COPY backend ./backend
RUN pip install --no-cache-dir . && mkdir -p /app/data
ENV ARTEMIS_DATABASE_PATH=/app/data/artemis_voice.db
EXPOSE 8000
HEALTHCHECK --interval=10s --timeout=3s --start-period=10s --retries=6 \
  CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=2).status == 200 else 1)"
CMD ["uvicorn", "artemisVoice.Evals.api.app:app", "--host", "0.0.0.0", "--port", "8000"]
