FROM python:3.12-slim AS backend

WORKDIR /app
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
COPY pyproject.toml ./
COPY backend ./backend
RUN pip install --no-cache-dir .
RUN mkdir -p /app/data
ENV ARTEMIS_DATABASE_PATH=/app/data/artemis_voice.db
EXPOSE 8000
CMD ["uvicorn", "artemisVoice.Evals.api.app:app", "--host", "0.0.0.0", "--port", "8000"]
