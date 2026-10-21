---
name: review-django-patterns
description: >-
  Review Django code in Pulse Chat for project-specific pattern violations —
  pagination, Redis key construction, S3 helpers, WebSocket dispatch, service layer separation, ViewSets, and ORM queries.
---

# review-django-patterns — Pulse Chat Django Pattern Review

Review new or modified Django code against Pulse Chat architectural standards.

## Rules to Check

1. **Pagination**:
   - All list endpoints must use cursor pagination from `utils/pagination.py` (`CustomCursorPagination`).
   - Flag any `PageNumberPagination`, `LimitOffsetPagination`, or ad-hoc slicing.

2. **Redis Key Construction**:
   - All Redis keys must be constructed via `RedisKeys` in `utils/redis_client.py`.
   - Flag any hardcoded key strings or raw f-strings (e.g. `f"user:{id}:..."`) outside `RedisKeys`.

3. **S3 Storage Operations**:
   - All S3 uploads, downloads, and presigned URLs must use `utils/s3.py` helpers (`s3_client`).
   - Flag any direct `boto3.client('s3')` instantiation or raw boto3 calls in views/services.

4. **WebSocket & Real-time Events**:
   - REST views and services must NOT call `channel_layer.group_send` directly.
   - All real-time event dispatching must route through `ChatRedisService` methods (or specified service abstraction).

5. **Service Layer Separation**:
   - Views must be thin HTTP controllers (input parsing, validation, calling service, returning response).
   - Core business logic belongs in `<app>/services.py` or `<app>/services/`.
   - Flag views with inline database transactions, business calculations, or complex orchestration.

6. **ViewSets vs APIView**:
   - Pulse Chat uses `rest_framework.views.APIView` consistently.
   - Flag any `ViewSet`, `GenericViewSet`, or `ModelViewSet` usage.

7. **ORM & Query Hygiene**:
   - Flag missing `select_related` / `prefetch_related` on foreign key / many-to-many fields in list queries.
   - Flag unbounded `.all()` queries without filtering or ordering on `ChatMessage` or `Conversation`.
   - Ensure queries utilize existing database indexes (e.g., `(user, conversation, created_at)`).

## Output Format

For each violation identified:
`file:line` — **[Severity]** rule broken — recommended fix.

Group findings by severity:
- **Critical**: Data loss, security risk, N+1 query on high-throughput path.
- **High**: Pattern violation (e.g., raw Redis key, direct boto3, direct group_send).
- **Low**: Code style or minor improvement.

Conclude review with:
- Count per severity.
- Final verdict: **PASS** or **FAIL**.
