FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
ENV UV_LINK_MODE=copy

WORKDIR /app/backend

RUN pip install --no-cache-dir "uv>=0.6,<1"

# The lock file resolves Pipecat from this sibling local source.
COPY backend/pyproject.toml backend/uv.lock ./
COPY vendor/pipecat /app/vendor/pipecat
RUN uv sync --frozen --no-dev --no-install-project

COPY backend /app/backend
RUN uv sync --frozen --no-dev

EXPOSE 8000

CMD ["uv", "run", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
