Start docker-compose services from the project root.

$ARGUMENTS: optional — a single service name to start only that service (e.g. "server", "celery_video", "db").
Omit to start all services.

Steps:
1. Run: docker-compose up -d $ARGUMENTS
2. Run: docker-compose ps
