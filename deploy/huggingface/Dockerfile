# Hugging Face Space (free CPU, 16 GB RAM). Clones the private GitHub repo at build time.
# Needs a Space secret named GH_TOKEN: a GitHub token with read access to khushcoding123/hackNasa.
FROM alpine/git AS src
RUN --mount=type=secret,id=GH_TOKEN,mode=0444,required=true \
    git clone --depth 1 "https://x-access-token:$(cat /run/secrets/GH_TOKEN)@github.com/khushcoding123/hackNasa.git" /src \
    && rm -rf /src/.git

FROM node:22-slim AS web
WORKDIR /src
COPY --from=src /src/package.json /src/package-lock.json /src/tsconfig.json ./
RUN npm ci
COPY --from=src /src/frontend ./frontend
COPY --from=src /src/scripts/build_frontend.mjs ./scripts/build_frontend.mjs
COPY --from=src /src/web ./web
RUN npm run build

FROM python:3.13-slim
ENV PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1 MOONMESH_DATA_ROOT=/app/data
RUN apt-get update && apt-get install -y --no-install-recommends libgomp1 && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY --from=src /src/requirements.txt /src/requirements-training.txt ./
RUN pip install -r requirements.txt -r requirements-training.txt
COPY --from=src /src/pyproject.toml ./
COPY --from=src /src/moonmesh ./moonmesh
COPY --from=src /src/lunar_core ./lunar_core
COPY --from=src /src/ora ./ora
COPY --from=src /src/training ./training
COPY --from=src /src/shared-data ./shared-data
RUN pip install --no-deps -e .
RUN python shared-data/unpack.py --data-root /app/data && rm -rf shared-data
RUN python -m moonmesh.data
COPY --from=web /src/web ./web
# Spaces run as a non-root user; the app writes caches next to its code.
RUN chmod -R a+rwX /app
EXPOSE 7860
CMD ["python", "-m", "uvicorn", "moonmesh.server:app", "--host", "0.0.0.0", "--port", "7860"]
