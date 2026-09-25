FROM python:3.12-slim

WORKDIR /app

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    SERVER_PORT=8080 \
    DATABASE_PATH=/data/goodbingo.db
# Set DATABASE_URL in your deployment environment to enable PostgreSQL:
#   DATABASE_URL=postgresql://user:pass@host:5432/etoobingo

RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates \
    && rm -rf /var/lib/apt/lists/* \
    && mkdir -p /data

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY bot ./bot
COPY server ./server
COPY webapp ./webapp
COPY migrations ./migrations
COPY run.py .

EXPOSE 8080

CMD ["python", "run.py"]
