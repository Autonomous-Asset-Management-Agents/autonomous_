# Dockerfile.backend
# Python 3.12 slim image for the AI Trading Bot
FROM public.ecr.aws/docker/library/python:3.12-slim

# Set working directory
WORKDIR /app

# Install system dependencies (build tools)
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    git \
    && rm -rf /var/lib/apt/lists/*

# Run Qt in offscreen mode (no display server needed)
ENV QT_QPA_PLATFORM=offscreen
# Bind Uvicorn to all interfaces so Docker port mapping works
ENV ENGINE_HOST=0.0.0.0

# Copy requirements first for better caching
COPY ["AI Trading Bot/requirements.oss.txt", "./requirements.oss.txt"]

# Copy local pandas-ta library (pandas-ta-classic fork, installs as pandas_ta_classic)
COPY ["AI Trading Bot/pandas-ta", "./pandas-ta"]

# Install Python dependencies
# 1. Install uv globally
# 2. Install CPU-only PyTorch first (avoids downloading ~3GB of CUDA packages)
# 3. Install core requirements (now locked with cryptographic hashes via uv)
# 4. Install cloud deps (supabase first, then google-genai --no-deps to avoid websockets conflict)
RUN pip install --no-cache-dir --upgrade pip uv && \
    uv pip install --system --no-cache-dir "torch==2.11.0+cpu" "torchvision==0.26.0+cpu" "torchaudio==2.11.0+cpu" --index-url https://download.pytorch.org/whl/cpu && \
    uv pip install --system --no-cache-dir -r requirements.oss.txt --index-strategy unsafe-best-match && \
    cd pandas-ta && \
    pip install --no-cache-dir . && \
    pip list | grep -i pandas

# Create unprivileged user with fixed UID to avoid volume mount permission issues
RUN groupadd -g 1000 appuser && useradd -u 1000 -g appuser -m appuser

# Give appuser ownership of the working directory so runtime code
# can create directories (logs, caches) without PermissionError.
RUN chown appuser:appuser /app

# Copy the application code with new user ownership
COPY --chown=appuser:appuser ["AI Trading Bot", "./app"]
# COPY ["src", "./src"]  <-- Removed as unused by engine
# Note: We need src if there are shared components, but mainly we need the python code.
# Adjusting copy structure to match import logic in engine.py
# The engine expects to run from the root where "core" is a module.

WORKDIR /app/app

# Expose port (default 8001)
EXPOSE 8001

USER appuser

# ARCHON STANDARD: Dry-Run Gate
# Evaluates module imports, Pydantic/Config schema logic, and basic dependency resolution
# before the Docker image is finalized and pushed to the registry.
RUN python -c "from config import *; from core.kill_switch import kill_switch; print('\n[ARCHON STANDARD] Dry-Run OK: Core Modules loaded successfully.')"

# Command to run the engine
# We use -m core.engine to run it as a module
CMD ["python", "-m", "core.engine"]
