# docker-down

Stop services using docker compose (V2).

$ARGUMENTS: optional flags. Examples:
  (empty)            — stop and remove containers
  -v                 — also remove named volumes (wipes db_data — use with caution)
  --rmi local        — also remove locally built images (server, db)
  --remove-orphans   — remove containers not defined in docker-compose.yml
  -v --rmi local     — full teardown: containers + volumes + images

Common flags:
  -v               remove named volumes (database data will be lost)
  --rmi local      remove images built from local Dockerfiles
  --remove-orphans remove containers for services no longer in compose file

Steps:
1. If -v flag is present, warn: "This will delete all volume data including the database."
2. Run: docker compose down $ARGUMENTS
