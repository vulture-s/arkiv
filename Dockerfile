# arkiv — Multi-stage Dockerfile
# Stage 0: Build the Svelte SPA (frontend/dist) that server.py serves at /.
# .dockerignore excludes dist/ + node_modules, so this stage builds from clean
# source. The built dist is copied into the app stage below.
# Digest-pinned for reproducible rebuilds (Wave C); the tag stays for readability,
# the digest is authoritative. Refresh via dependabot (docker ecosystem).
FROM node:22-slim@sha256:c3de60bf2f9dd0ac6370e6117950ff62d6e339527e7472301c9c78a017978392 AS ui
WORKDIR /ui
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY frontend/ ./
RUN npm run build

# Stage 1: Dependencies
# Digest-pinned for reproducible rebuilds (Wave C); refresh via dependabot.
FROM python:3.12-slim@sha256:a6e34c598f2467ed0e9a8d349809fcd8b5c603269512df273a0bb1784edc11b1 AS deps

RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
    curl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt constraints.txt ./
# faster-whisper (the CPU backend) comes from requirements.txt's non-Darwin marker;
# versions from constraints.txt, same as every other install path.
RUN pip install --no-cache-dir -r requirements.txt -c constraints.txt

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

CMD ["uvicorn", "server:app", "--host", "0.0.0.0", "--port", "8501", "--no-proxy-headers"]
