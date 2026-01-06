#!/bin/sh
# set -e
# until nc -z $DATABASE_HOST $DATABASE_PORT; do
#   echo "Waiting for Postgres at $DATABASE_HOST:$DATABASE_HOST..."
#   sleep 1
# done

# python manage.py makemigrations users
# python manage.py migrate users

# python manage.py makemigrations
# python manage.py migrate --noinput

# exec "$@"

set -e

until nc -z $DATABASE_HOST $DATABASE_PORT; do
  echo "Waiting for Postgres at $DATABASE_HOST:$DATABASE_PORT..."
  sleep 1
done

# REMOVE 'makemigrations' lines. 
# We only apply existing migrations now.
python manage.py migrate --noinput

exec "$@"