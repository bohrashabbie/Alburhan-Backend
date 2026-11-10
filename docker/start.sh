#!/bin/sh
# Container start: apply migrations (also syncs the permission catalogue), then serve.
set -e
python manage.py migrate --noinput
exec gunicorn config.wsgi:application \
  --bind 0.0.0.0:8000 \
  --workers "${WEB_CONCURRENCY:-3}" \
  --timeout "${GUNICORN_TIMEOUT:-120}" \
  --access-logfile - \
  --forwarded-allow-ips "*"
