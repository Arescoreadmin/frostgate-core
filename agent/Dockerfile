FROM python:3.12-slim

WORKDIR /app

# Install agent deps (NOT the core deps)
COPY agent/requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt

# Copy agent code
COPY agent /app/agent

# Run agent
CMD ["python", "-m", "agent.app.main"]
