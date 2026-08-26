FROM ghcr.io/astral-sh/uv:0.11.28@sha256:0f36cb9361a3346885ca3677e3767016687b5a170c1a6b88465ec14aefec90aa AS uv
FROM python:3.14.6-slim-bookworm@sha256:4c92ffcde4dd6f1ff72a24518f49fd4990b27134987dfa31a733badde66df9f8

COPY --from=uv /uv /uvx /bin/
WORKDIR /app

ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy

COPY pyproject.toml uv.lock README.md LICENSE ./
COPY src ./src
COPY alembic.ini ./
COPY migrations ./migrations
COPY deployment/run-with-database-secret.sh ./deployment/run-with-database-secret.sh

RUN uv sync --frozen --no-dev --no-editable \
    && useradd --create-home --uid 10001 evidence \
    && chown -R evidence:evidence /app

USER evidence
EXPOSE 4318
CMD ["wsr-evidence"]
