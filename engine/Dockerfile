# APEX Quant Engine - production container.
# Builds the FastAPI service (regime / signal / risk / validation / research) for
# any Docker host (Render, Railway, Fly, Cloud Run, ...). Binds $PORT (default 8000).
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# libgomp1 = the OpenMP runtime LightGBM needs at import time.
RUN apt-get update \
    && apt-get install -y --no-install-recommends libgomp1 \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY apex_quant ./apex_quant
COPY config.yaml ./config.yaml
COPY data_store ./data_store

EXPOSE 8000
# Hosts inject $PORT; default to 8000 for a local `docker run`.
CMD ["sh", "-c", "uvicorn apex_quant.api.app:app --host 0.0.0.0 --port ${PORT:-8000}"]
