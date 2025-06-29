#!/bin/sh
set -e
until nc -z $DATABASE_HOST $DATABASE_PORT; do
  echo "Waiting for Postgres at $DATABASE_HOST:$DATABASE_HOST..."
  sleep 1
done

python manage.py migrate --noinput

exec "$@"
