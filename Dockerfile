FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PYTHONDONTWRITEBYTECODE=1

WORKDIR /app

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY tvguide ./tvguide
COPY config.example.toml ./config.example.toml

# config.toml und data/ werden zur Laufzeit als Volume eingebunden.
RUN mkdir -p /app/data

EXPOSE 8085

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
  CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8085/healthz', timeout=3).status == 200 else 1)"

CMD ["python", "-m", "tvguide", "serve"]
