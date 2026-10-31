# syntax=docker/dockerfile:1
ARG PYTHON_VERSION=3.14.8
FROM python:${PYTHON_VERSION}-slim-bookworm AS builder
RUN python -m pip install --no-cache-dir uv==0.9.26
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_PYTHON_DOWNLOADS=never
WORKDIR /app
COPY pyproject.toml uv.lock README.md ./
RUN --mount=type=cache,target=/root/.cache/uv uv sync --frozen --no-dev --no-install-project
COPY src ./src
COPY migrations ./migrations
RUN --mount=type=cache,target=/root/.cache/uv uv sync --frozen --no-dev --no-editable

FROM python:${PYTHON_VERSION}-slim-bookworm AS runtime
ENV PATH="/app/.venv/bin:$PATH" PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 APP_ENV=production
RUN groupadd --gid 10001 wordle && useradd --uid 10001 --gid wordle --no-create-home wordle
WORKDIR /app
COPY --from=builder --chown=wordle:wordle /app/.venv /app/.venv
USER wordle
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=60s --retries=3 CMD python -c "import os,urllib.request; urllib.request.urlopen('http://127.0.0.1:'+os.environ.get('PORT','8000')+'/health/ready',timeout=3)"
CMD ["wordle-bot", "serve"]
