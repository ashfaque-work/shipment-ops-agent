# One image for both Python services (agent API and mock carrier); compose picks the command.
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PROJECT_ENVIRONMENT=/opt/venv \
    PATH="/opt/venv/bin:$PATH"

COPY --from=ghcr.io/astral-sh/uv:0.11 /uv /usr/local/bin/uv

WORKDIR /app
# Dependencies first for layer caching
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --all-extras --no-install-project

COPY mock_carrier ./mock_carrier
COPY shipops ./shipops
COPY data/documents/pdf ./data/documents/pdf

RUN useradd --create-home --uid 10001 app
USER app

EXPOSE 8000
# Render and similar hosts inject $PORT
CMD ["sh", "-c", "uvicorn shipops.api.main:app --host 0.0.0.0 --port ${PORT:-8000}"]
