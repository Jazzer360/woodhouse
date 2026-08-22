FROM ghcr.io/astral-sh/uv:0.9.26-python3.12-bookworm-slim AS builder

WORKDIR /app
COPY pyproject.toml uv.lock /app/
COPY packages /app/packages
COPY services /app/services
RUN uv sync --frozen --package tpp-mcp-gateway --no-dev --no-editable

FROM python:3.12-slim-bookworm

RUN groupadd --system app && useradd --system --gid app --home-dir /app app
WORKDIR /app
COPY --from=builder /app/.venv /app/.venv

ENV HOST=0.0.0.0 \
    PATH=/app/.venv/bin:$PATH \
    PORT=8080 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

USER app
EXPOSE 8080

HEALTHCHECK --interval=30s --timeout=3s --start-period=5s --retries=3 \
  CMD ["python", "-c", "from urllib.request import urlopen; urlopen('http://127.0.0.1:8080/healthz', timeout=2).read()"]

CMD ["python", "-m", "tesla_personal_platform.mcp_gateway.main"]
