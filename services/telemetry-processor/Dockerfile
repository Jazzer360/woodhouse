FROM python:3.12-slim-bookworm

RUN groupadd --system app && useradd --system --gid app --home-dir /app app
WORKDIR /app

COPY services/telemetry-processor/src /app/src

ENV HOST=0.0.0.0 \
    PORT=8080 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONPATH=/app/src \
    PYTHONUNBUFFERED=1

USER app
EXPOSE 8080

HEALTHCHECK --interval=30s --timeout=3s --start-period=5s --retries=3 \
  CMD ["python", "-c", "from urllib.request import urlopen; urlopen('http://127.0.0.1:8080/healthz', timeout=2).read()"]

CMD ["python", "-m", "tesla_personal_platform.telemetry_processor.main"]
