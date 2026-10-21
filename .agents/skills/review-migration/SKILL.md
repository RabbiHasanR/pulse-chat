---
name: review-migration
description: >-
  Review Django database migrations before application — safety analysis for destructive operations,
  missing indexes, table locks on large tables, and zero-downtime compliance.
---

# review-migration — Pulse Chat Migration Safety Review

Analyze Django migration operations before running `python manage.py migrate`.

## Safety Checklist

1. **Destructive Operations**:
   - `RemoveField`: Verify the field is no longer referenced anywhere in application code, serializers, or admin.
   - `DeleteModel`: Verify the model is completely removed from imports, foreign keys, and Celery tasks.
   - `AlterField` (type or nullability change): Ensure data conversion is safe on existing production records (e.g., varchar to int fails if non-numeric characters exist).

2. **Large Table Lock Risks (`chats_chatmessage`, `chats_conversation`, `users_chatuser`)**:
   - Adding a `NOT NULL` column without a default value causes an exclusive table lock while rewriting all rows. Use `null=True` or supply `default=...`.
   - Adding indexes: Index creation on massive tables may lock writes. Prefer concurrent indexing or off-peak deployment where supported.

3. **Index Completeness**:
   - Every foreign key used in list filters or ordering needs `db_index=True`.
   - Composite indexes defined in model `Meta.indexes` must be reflected in the migration.

4. **Data Migrations (`migrations.RunPython`)**:
   - Must provide `reverse_code` for rollbacks.
   - Must batch updates (e.g. `iterator(chunk_size=1000)` or `bulk_update`) to prevent memory exhaustion and prolonged transaction locks.
   - Set `atomic = False` if performing millions of row updates.

5. **Dependency Integrity**:
   - Verify migration `dependencies` list points to the latest applied migration in the app.
   - Ensure no circular dependencies between app migrations.

## Output Format

Group findings:
- **BLOCKING** (Must fix before running migration; risk of data loss or outage)
- **WARNING** (Potential performance impact; review required)
- **INFO** (Non-blocking observation)

For each issue:
- `migration_file:line` — operation — risk — recommended resolution.

Conclude with verdict:
`Safe to apply? [Yes / No / Yes with caution]`
