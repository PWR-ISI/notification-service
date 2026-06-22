#!/bin/sh
set -e

python manage.py makemigrations --noinput
python manage.py migrate --noinput

# Start SQS event consumer in background
python manage.py consume_events &

exec gunicorn config.wsgi:application --bind 0.0.0.0:8000 --workers 2 --timeout 120
