FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PYTHONDONTWRITEBYTECODE=1

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends ca-certificates \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY tvguide ./tvguide
COPY tools ./tools
COPY config.example.toml ./config.example.toml

# config.toml, data/ und imdb_raw/ werden zur Laufzeit als Volume eingebunden.
RUN mkdir -p /app/data /app/imdb_raw /app/data/logs

EXPOSE 8085

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
  CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8085/healthz', timeout=3).status == 200 else 1)"

CMD ["python", "-m", "tvguide", "serve"]
