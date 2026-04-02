# docker-logs

Tail logs for a compose service or standalone container.

$ARGUMENTS: service/container name plus optional flags. Examples:
  server                  — follow server logs
  server --tail 50        — last 50 lines then follow
  server --no-color       — plain output (useful for grepping)
  server --timestamps     — prefix each line with timestamp
  celery_video --tail 100 — last 100 lines of video worker

Compose services: db, redis, s3mock, server, celery_default, celery_media, celery_video, celery_beat

Common flags:
  --tail N       show last N lines before following (default: all)
  --no-color     disable color output
  --timestamps   show timestamps on each log line

If $ARGUMENTS refers to a compose service:
  Run: docker compose logs -f $ARGUMENTS

If $ARGUMENTS refers to a standalone container:
  Run: docker logs -f $ARGUMENTS
