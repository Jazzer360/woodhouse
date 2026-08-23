FROM ghcr.io/astral-sh/uv:0.9.26-python3.12-bookworm-slim AS builder
WORKDIR /app
COPY pyproject.toml uv.lock /app/
COPY packages /app/packages
COPY services /app/services
RUN uv sync --frozen --package tpp-certificate-renewer --no-dev --no-editable

FROM python:3.12-slim-bookworm

RUN apt-get update \
    && apt-get install --no-install-recommends --yes openssl \
    && rm -rf /var/lib/apt/lists/* \
    && groupadd --system app \
    && useradd --system --gid app --home-dir /app app
WORKDIR /app
COPY --from=builder /app/.venv /app/.venv

ENV PATH=/app/.venv/bin:$PATH \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

USER app
ENTRYPOINT ["certificate-renewer"]
