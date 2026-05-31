#!/bin/bash
set -e

echo "Starting notification-service entrypoint..."

# Wait for database to be ready (if using PostgreSQL)
if [ "$DB_ENGINE" = "postgresql" ]; then
    echo "Waiting for PostgreSQL to be ready..."
    max_attempts=30
    attempt=0
    while [ $attempt -lt $max_attempts ]; do
        if python manage.py dbshell < /dev/null 2>&1; then
            echo "Database is ready"
            break
        fi
        attempt=$((attempt + 1))
        sleep 1
    done
fi

# Run migrations
echo "Running Django migrations..."
python manage.py migrate --noinput

echo "Entrypoint setup complete, starting application..."

# Start the application
gunicorn --bind 0.0.0.0:8000 --workers 2 --timeout 120 --access-logfile - --error-logfile - notification.wsgi:application
