# migrate

Create and apply Django migrations.

$ARGUMENTS: optional app name to scope makemigrations. Examples:
  (empty)   — makemigrations for all apps
  chats     — makemigrations for chats only
  users     — makemigrations for users only

Run:
  cd server/
  python manage.py makemigrations $ARGUMENTS
  python manage.py migrate
  python manage.py showmigrations
