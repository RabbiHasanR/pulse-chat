# docker-up

Start services using docker compose (V2).

$ARGUMENTS: optional flags and/or service name(s). Examples:
  (empty)                  — start all services detached
  server                   — start only the server service
  --build                  — rebuild images before starting
  --build server           — rebuild and start only server
  --scale celery_media=2   — scale a service to N replicas
  --no-deps server         — start server without its dependencies

Common flags:
  --build            rebuild images before starting
  --no-deps          don't start linked/dependent services
  --scale svc=N      run N replicas of a service
  --force-recreate   recreate containers even if config unchanged

Steps:
1. Run: docker compose up -d $ARGUMENTS
2. Run: docker compose ps
