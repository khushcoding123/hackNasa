# MOONMESH full app (FastAPI backend + built web UI) for Render / any Docker host.
# Stage 1: build the frontend bundle and Cesium runtime files.
FROM node:22-slim AS web
WORKDIR /src
COPY package.json package-lock.json tsconfig.json ./
RUN npm ci
COPY frontend ./frontend
COPY scripts/build_frontend.mjs ./scripts/build_frontend.mjs
COPY web ./web
RUN npm run build

# Stage 2: Python 3.13 runtime with the training/coverage dependencies (rasterio, numba, lightgbm).
FROM python:3.13-slim
ENV PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1 MOONMESH_DATA_ROOT=/app/data
RUN apt-get update && apt-get install -y --no-install-recommends libgomp1 && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY requirements.txt requirements-training.txt ./
RUN pip install -r requirements.txt -r requirements-training.txt
COPY pyproject.toml ./
COPY moonmesh ./moonmesh
COPY lunar_core ./lunar_core
COPY ora ./ora
COPY training ./training
COPY shared-data ./shared-data
RUN pip install --no-deps -e .
# Surface coverage terrain + trained LightGBM models from the committed split zip.
RUN python shared-data/unpack.py --data-root /app/data && rm -rf shared-data
# The app's own NASA LOLA terrain and SPICE kernels (about 535 MB, downloaded once at build time).
RUN python -m moonmesh.data
COPY --from=web /src/web ./web
EXPOSE 8765
CMD ["sh", "-c", "python -m uvicorn moonmesh.server:app --host 0.0.0.0 --port ${PORT:-8765}"]
