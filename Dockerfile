FROM python:3.12-slim AS builder

WORKDIR /app

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

COPY --from=ghcr.io/astral-sh/uv:0.6.16 /uv /usr/local/bin/uv

COPY pyproject.toml uv.lock /app/

RUN uv sync --frozen --no-dev --no-install-project


FROM python:3.12-slim AS runtime

WORKDIR /app

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PORT=8080 \
    PYTHONPATH=/app/src \
    PATH="/app/.venv/bin:$PATH"

COPY --from=builder /app/.venv /app/.venv
COPY src /app/src

# proxy-headers because Cloud Run terminates TLS and forwards plain HTTP.
CMD ["sh", "-c", "uvicorn wishlist_mcp.main:app --host 0.0.0.0 --port ${PORT} --proxy-headers --forwarded-allow-ips '*'"]
