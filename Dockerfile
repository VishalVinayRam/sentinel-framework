FROM python:3.11-slim

WORKDIR /app

# Install system deps needed by boto3 + requests
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Install Python deps first (layer-cached as long as requirements don't change)
COPY requirements-prod.txt ./
RUN pip install --no-cache-dir -r requirements-prod.txt

# Copy the sentinel package and dashboard service
COPY sentinel/ ./sentinel/
COPY services/dashboard/ ./services/dashboard/

# code_context_builder scans for local source code — include target services
# so RCA has real code context even when running in a container
COPY target/ ./target/

ENV PYTHONPATH=/app
ENV PORT=8501

EXPOSE 8501

# Non-root user for security
RUN adduser --disabled-password --gecos "" sentinel
USER sentinel

HEALTHCHECK --interval=30s --timeout=10s --start-period=60s --retries=3 \
  CMD curl -f http://localhost:8501/health || exit 1

CMD ["uvicorn", "services.dashboard.api:app", \
     "--host", "0.0.0.0", \
     "--port", "8501", \
     "--workers", "2", \
     "--log-level", "info"]
