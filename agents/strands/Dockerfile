# Strands BidiAgent container for AgentCore Runtime
# Platform: linux/arm64 (required by AgentCore)
FROM --platform=linux/arm64 python:3.12-slim-bookworm

WORKDIR /app

# Install dependencies first for better layer caching
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy agent code
COPY entrypoint.py .

# AgentCore Runtime requires port 8080
EXPOSE 8080

# Run with OpenTelemetry auto-instrumentation for observability
CMD ["opentelemetry-instrument", "python", "entrypoint.py"]
