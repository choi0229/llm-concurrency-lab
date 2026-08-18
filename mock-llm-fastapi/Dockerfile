# Pinned by digest for reproducibility (linux/arm64 manifest verified 2026-08-10,
# see docs/decisions/version-compatibility.md).
FROM python:3.12-slim@sha256:229a2c5bfa27522db7815ea81f9bed70af17ccb9de9fc7ad142b1877b5830d36

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app

ENV MOCK_LLM_LOG_LEVEL=INFO \
    MOCK_LLM_MAX_CONCURRENT_PROCESSING=0 \
    MOCK_LLM_MAX_WAITING=0

EXPOSE 8000

HEALTHCHECK --interval=10s --timeout=3s --start-period=5s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/healthz')" || exit 1

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
