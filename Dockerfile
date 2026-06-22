FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends build-essential gcc libpq-dev \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt /app/requirements.txt
RUN pip install --upgrade pip && pip install -r /app/requirements.txt || true

COPY . /app

RUN chmod +x /app/entrypoint.sh

ENV DJANGO_SETTINGS_MODULE=settings
EXPOSE 8000
ENTRYPOINT ["/app/entrypoint.sh"]
