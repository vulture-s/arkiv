# arkiv — Multi-stage Dockerfile
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

# Create data directories
RUN mkdir -p thumbnails chroma_db

EXPOSE 8501

HEALTHCHECK --interval=30s --timeout=5s \
  CMD curl -f http://localhost:8501/api/stats || exit 1

CMD ["uvicorn", "server:app", "--host", "0.0.0.0", "--port", "8501"]
