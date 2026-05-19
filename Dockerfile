FROM python:3.12-slim

WORKDIR /app

# Install dependencies first for better layer caching
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application code
COPY nova_sonic_demo/ nova_sonic_demo/

# Expose the application port
EXPOSE 8000

# Run the web server
CMD ["uvicorn", "nova_sonic_demo.web.app:app", "--host", "0.0.0.0", "--port", "8000"]
