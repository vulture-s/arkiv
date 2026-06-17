# arkiv — Multi-stage Dockerfile
# Stage 0: Build the Svelte SPA (frontend/dist) that server.py serves at /.
# .dockerignore excludes dist/ + node_modules, so this stage builds from clean
# source. The built dist is copied into the app stage below.
FROM node:20-slim AS ui
WORKDIR /ui
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY frontend/ ./
RUN npm run build

# Stage 1: Dependencies
FROM python:3.11-slim AS deps

RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
    curl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt .
# Install CPU whisper backend for Docker (no MLX in container)
RUN pip install --no-cache-dir -r requirements.txt faster-whisper

# Stage 2: Application
FROM deps AS app

COPY . .
# Overlay the built SPA (dist/ is gitignored + .dockerignore'd, so COPY . . brings
# the frontend source but not its build — bring it from the ui stage).
COPY --from=ui /ui/dist ./frontend/dist

# Create data directories
RUN mkdir -p thumbnails chroma_db

EXPOSE 8501

HEALTHCHECK --interval=30s --timeout=5s \
  CMD curl -f http://localhost:8501/api/stats || exit 1

CMD ["uvicorn", "server:app", "--host", "0.0.0.0", "--port", "8501"]
