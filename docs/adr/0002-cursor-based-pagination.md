# ADR-0002: Enforce Cursor-Based Pagination for All List Endpoints

- **Status:** accepted
- **Date:** 2026-10-21
- **Related specs:** CHAT-004, CHAT-005, USR-002, USR-003

## Context
Chat applications operate on rapidly growing append-heavy message datasets and frequently updated conversation lists. Standard `OFFSET`/`LIMIT` pagination suffers from two fatal issues at scale:
1. **O(N) database scans**: The database must scan through $N$ rows before returning the requested slice, causing severe database I/O degradation on deep page scrolls.
2. **Page drift / Duplicate items**: If a new message is inserted while a user is paginating through chat history, offsets shift, causing the client to see duplicated or skipped messages.

## Decision
All collection/list endpoints in Pulse Chat strictly implement cursor-based pagination via DRF's `CursorPagination` wrapped in `BaseCursorPagination` (`server/utils/pagination.py`).
- Cursors are opaque base64-encoded position markers based on indexed timestamp fields (`-created_at` or `-updated_at`).
- Page navigation only supports `next` and `previous` links; absolute page numbers (e.g. `?page=4`) and `offset`/`limit` are strictly prohibited.

## Alternatives considered
| Option | Pros | Cons |
| --- | --- | --- |
| Offset/Limit pagination (`PageNumberPagination`) | Simple UI jumping to page N | $O(N)$ scan performance penalty; phantom rows and skipped items on real-time writes. |
| Keyset pagination (raw SQL `WHERE id < :cursor`) | Fast, native SQL | Less flexible when secondary ordering criteria (e.g. tie-breakers) are needed. |

## Consequences
**Positive**
- Guaranteed $O(1)$ database index seek performance regardless of history depth.
- Zero duplicate or missing messages when real-time messages arrive during scrolling.

**Negative / trade-offs**
- Clients cannot jump directly to arbitrary page numbers (e.g. "jump to page 15").

**Rules this creates**
- No view or serializer may accept `offset` or `page` query parameters.
- All cursor ordering fields must be indexed in PostgreSQL.
