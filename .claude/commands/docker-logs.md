Tail logs for a docker-compose service or a standalone container.

$ARGUMENTS: required — service or container name.
Compose services: db, redis, s3mock, server, celery_default, celery_media, celery_video, celery_beat

If $ARGUMENTS is a compose service, run:
  docker-compose logs -f $ARGUMENTS

If $ARGUMENTS is a standalone container name, run:
  docker logs -f $ARGUMENTS
