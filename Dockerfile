FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends build-essential gcc libpq-dev curl \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt /app/requirements.txt
RUN pip install --upgrade pip && pip install -r /app/requirements.txt || true

COPY . /app

RUN chmod +x /app/entrypoint.sh 2>/dev/null || true

EXPOSE 8000

RUN python manage.py collectstatic --noinput 2>/dev/null || true

HEALTHCHECK --interval=30s --timeout=10s --retries=3 --start-period=40s \
    CMD curl -f http://localhost:8000/api/v2/health/ || exit 1

ENTRYPOINT ["/bin/bash", "/app/entrypoint.sh"]
