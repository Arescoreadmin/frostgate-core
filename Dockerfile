FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# System deps (minimal)
RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates \
 && rm -rf /var/lib/apt/lists/*

# Python deps
COPY requirements.txt .
RUN pip install --upgrade pip \
    && pip install -r requirements.txt

# App code
COPY api ./api
COPY engine ./engine
COPY tools ./tools
COPY jobs ./jobs
COPY state ./state

EXPOSE 8080

ENV FG_ENV=dev \
    FG_ENFORCEMENT_MODE=enforce

CMD ["uvicorn", "api.main:app", "--host", "0.0.0.0", "--port", "8080"]
