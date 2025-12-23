# syntax=docker/dockerfile:1

########################################
# Base builder image
########################################
FROM python:3.12-slim AS builder

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=on \
    PIP_NO_CACHE_DIR=on

WORKDIR /app

# System deps (gcc for any native wheels, curl for health/debug if needed)
RUN apt-get update && \
    apt-get install -y --no-install-recommends build-essential curl && \
    rm -rf /var/lib/apt/lists/*

# Install Python deps
# Adjust if you’re using poetry/uv; this assumes requirements.txt in repo root.
COPY requirements.txt .
RUN pip install --prefix=/install -r requirements.txt

########################################
# Runtime image
########################################
FROM python:3.12-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=on \
    PIP_NO_CACHE_DIR=on

WORKDIR /app

# Create unprivileged user
RUN useradd -m frostgate
# Copy installed deps from builder
COPY --from=builder /install /usr/local

# Copy app code
COPY . /app
RUN mkdir -p /var/lib/frostgate/pycache /var/lib/frostgate/state /var/lib/frostgate/agent_queue \
 && chown -R frostgate:frostgate /var/lib/frostgate
ENV PYTHONPYCACHEPREFIX=/var/lib/frostgate/pycache
# Default envs; override in real deployments
ENV FROSTGATE_ENV=prod \
    FROSTGATE_ENFORCEMENT_MODE=block \
    FROSTGATE_LOG_LEVEL=INFO

EXPOSE 8080

# Healthcheck for orchestrators
HEALTHCHECK --interval=30s --timeout=5s --retries=3 CMD \
  curl -fsS http://127.0.0.1:8080/health || exit 1

# Use uvicorn as the entrypoint
CMD ["python", "-m", "uvicorn", "api.main:app", "--host", "0.0.0.0", "--port", "8080"]

USER frostgate
