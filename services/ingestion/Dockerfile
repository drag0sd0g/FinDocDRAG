FROM python:3.12-slim AS base

WORKDIR /app

# System deps for confluent-kafka (librdkafka) + curl for health checks
RUN apt-get update && \
    apt-get install -y --no-install-recommends gcc librdkafka-dev curl && \
    rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY src/ src/

EXPOSE 8001

CMD ["uvicorn", "src.main:app", "--host", "0.0.0.0", "--port", "8001"]