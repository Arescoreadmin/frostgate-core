# agent/Dockerfile
FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt

COPY agent /app/agent

# If your agent runs as a module/script, adjust as needed:
CMD ["python", "-m", "agent.app.main"]
