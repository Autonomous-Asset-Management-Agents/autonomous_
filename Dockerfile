# Dockerfile for ai_trading_bot (Engine, API, Web UI)
# Uses CPU-only PyTorch to keep image small (~500MB vs ~5GB with CUDA)

# --- Stage 1: Builder ---
FROM mirror.gcr.io/library/python:3.12-slim AS builder

WORKDIR /app

# Install system dependencies for build
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

# Copy requirement files
COPY requirements.txt .
COPY requirements-cloud.txt .
# Install core dependencies (torch gets CPU wheels via --extra-index-url)
RUN pip install --user --no-cache-dir -r requirements.txt --extra-index-url https://download.pytorch.org/whl/cpu

# Install cloud dependencies individually to handle websockets conflict
RUN pip install --user --no-cache-dir \
    "google-cloud-aiplatform>=1.38" \
    "google-cloud-storage>=2.14" \
    "cloud-sql-python-connector[asyncpg]>=1.11.0"


# Install pandas-ta and python-dotenv explicitly (pip may skip them during bulk install)
RUN pip install --user --no-cache-dir "pandas-ta>=0.3.14b" "python-dotenv>=1.0"

# --- Stage 2: Runtime ---
FROM mirror.gcr.io/library/python:3.12-slim

WORKDIR /app

# Install runtime system dependencies (PyQt6 needs OpenGL, fontconfig, xcb, dbus)
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    libgl1 \
    libglib2.0-0 \
    libxkbcommon0 \
    libegl1 \
    libdbus-1-3 \
    libfontconfig1 \
    libfreetype6 \
    tini \
    libxcb1 \
    libxcb-icccm4 \
    libxcb-image0 \
    libxcb-keysyms1 \
    libxcb-randr0 \
    libxcb-render-util0 \
    libxcb-shape0 \
    libxcb-xfixes0 \
    libxcb-xinerama0 \
    libxcb-cursor0 \
    && rm -rf /var/lib/apt/lists/*

# Create non-root user FIRST so COPY --chown can target it directly.
# Defense-in-depth: container breakout → unprivileged shell, not root.
RUN groupadd -g 1000 appuser && \
    useradd -u 1000 -g appuser -m -s /bin/bash appuser

# Give appuser ownership of the working directory so runtime code
# can create directories (logs, caches) without PermissionError.
RUN chown appuser:appuser /app

# Copy installed python packages from builder into appuser's home (not /root).
# Single COPY --chown avoids the layer bloat that comes from a post-copy
# `chown -R` duplicating every file into a new image layer.
COPY --from=builder --chown=appuser:appuser /root/.local /home/appuser/.local
ENV PATH=/home/appuser/.local/bin:$PATH

# Ensure critical runtime deps are present (guards against Docker layer cache misses).
# System-wide install so PATH resolution works for appuser too.
RUN pip install --no-cache-dir python-dotenv>=1.0

# Copy application code with correct ownership from the start
COPY --chown=appuser:appuser . .

# Pre-create runtime directories required by the application before dropping privileges
RUN mkdir -p /app/cloud_fallback_logs && chown appuser:appuser /app/cloud_fallback_logs

# Environment variables
ENV PYTHONUNBUFFERED=1
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONPATH=/app

# Cloud Run setzt $PORT automatisch (default 8080).
# Die Engine liest ENGINE_PORT aus ENV (config.py → os.getenv('ENGINE_PORT', '8001')).
# cloudbuild.yaml setzt ENGINE_PORT=8080 beim Deploy → kein Code-Change nötig.
# Lokaler Betrieb: docker-compose setzt ENGINE_PORT=8001 weiterhin (unverändert).
EXPOSE 8080

USER appuser

ENTRYPOINT ["/usr/bin/tini", "--"]

# Run GCS sync, then start engine.
# Alembic migrations are now handled externally via Cloud Run Jobs to prevent race conditions.
CMD ["/bin/bash", "-c", "python scripts/gcs_sync_on_start.py && if [ -n \"$CLOUD_SQL_CONNECTION_NAME\" ]; then echo 'Waiting for Cloud SQL Proxy socket...'; while [ ! -S /cloudsql/$CLOUD_SQL_CONNECTION_NAME/.s.PGSQL.5432 ]; do sleep 0.5; done; echo 'Socket ready!'; fi && python -m core.engine"]
