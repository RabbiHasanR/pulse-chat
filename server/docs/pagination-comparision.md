# Pagination

Pagination controls how large lists are split across multiple API responses.
Choosing the wrong strategy causes slow queries, duplicate results, or broken UX at scale.
This project implements all four common strategies so you can compare them side by side.

---

## Variants

| Variant | DB complexity | Random access | Handles insertions | Total count | Endpoint |
| --- | --- | --- | --- | --- | --- |
| Cursor | O(log n) | No | Yes | No | `GET /api/chat/list/`, `GET /api/chat/user/<id>/messages/` |
| Offset | O(n) | Yes | No (drift) | Yes | `GET /api/chat/messages/offset/` |
| Page-based | O(n) | Yes | No (drift) | Yes + total_pages | `GET /api/auth/users/page/` |
| Keyset | O(log n) | No | Yes | No | `GET /api/auth/contacts/keyset/` |

---

## Cursor Pagination

The default strategy in this project. Encodes a position (timestamp) into an opaque token. Each response includes a `next` cursor; the next request passes it to fetch the following page.

**Query generated:**
```sql
WHERE created_at < '2024-01-15 10:30:00' ORDER BY created_at DESC LIMIT 20
```

The index on `created_at` is used directly — no rows are scanned and discarded.

**Implementation:** `chats/pagination.py` → `MessageCursorPagination`, `ChatListCursorPagination`, backed by `utils/pagination.py`.

**When to use:** Infinite scroll, real-time feeds, any list that changes frequently.
**When NOT to use:** "Jump to page 5", total count display, random access by page number.

---

## Offset Pagination

Skips a fixed number of rows and returns the next N. The simplest strategy to understand.

**Query generated:**
```sql
SELECT * FROM chats_chatmessage
WHERE (sender_id = 1 OR receiver_id = 1)
ORDER BY created_at DESC
LIMIT 20 OFFSET 40
```

The database reads and discards 40 rows before returning 20. At `offset=10000`, it reads 10,020 rows to return 20.

**Page drift problem:** If a new message arrives between page 1 and page 2 requests, all rows shift — page 2 returns a duplicate item.

**Implementation:** `chats/views.py` → `MessageOffsetListView`
**Request:** `GET /api/chat/messages/offset/?offset=40&limit=20`
**Response includes:** `total`, `offset`, `limit`, `has_next`

**When to use:** Admin panels, small datasets, anywhere users need "go to row N".
**When NOT to use:** Large tables, high-traffic APIs, real-time data.

---

## Page-based Pagination

A user-friendly wrapper over offset. Accepts `page` and `page_size` instead of raw offset values. Identical to offset at the database level.

**Query generated:**
```sql
-- offset = (page - 1) * page_size = (3 - 1) * 20 = 40
SELECT * FROM users_chatuser
WHERE id != 1
ORDER BY full_name ASC
LIMIT 20 OFFSET 40;

-- Additional COUNT(*) for total_pages:
SELECT COUNT(*) FROM users_chatuser WHERE id != 1
```

The `COUNT(*)` is what distinguishes page-based from raw offset — it enables `total_pages` in the response, but adds an extra query on every request.

**Implementation:** `users/views.py` → `UserPageListView`
**Request:** `GET /api/auth/users/page/?page=3&page_size=20`
**Response includes:** `total`, `page`, `page_size`, `total_pages`, `has_next`, `has_prev`

**When to use:** Content sites, search results, user directories — anywhere "page 3 of 10" is meaningful UX.
**When NOT to use:** Same as offset. Has the same drift and scan cost problems.

---

## Keyset Pagination

Filters instead of skips. Uses the last seen value of an indexed column (`id`) as a `WHERE` clause. No `COUNT(*)`, no row scanning.

**Query generated:**
```sql
-- First page (no before_id):
SELECT * FROM users_contact
WHERE owner_id = 1
ORDER BY id DESC LIMIT 20

-- Next page (before_id=1050):
SELECT * FROM users_contact
WHERE owner_id = 1 AND id < 1050
ORDER BY id DESC LIMIT 20
```

The index on `id` jumps directly to `1050` — the database never touches rows above that value.

**`has_next` without COUNT(*):** Fetches `limit + 1` rows. If 21 rows come back when limit is 20, there is a next page. The 21st row is discarded; its `id` becomes `next_before_id`.

**Implementation:** `users/views.py` → `ContactKeysetListView`
**Request:** `GET /api/auth/contacts/keyset/?before_id=1050&limit=20`
**Response includes:** `limit`, `has_next`, `next_before_id`

**When to use:** Large tables with a stable sort key, high-performance APIs, anywhere you cache the last seen ID.
**When NOT to use:** Multi-column sort, random page access, when total count is required.

---

## Comparison

| | Cursor | Offset | Page-based | Keyset |
| --- | --- | --- | --- | --- |
| DB rows scanned | Only returned | offset + limit | offset + limit | Only returned |
| Extra COUNT(*) | No | Yes | Yes (for total_pages) | No |
| Safe with insertions | Yes | No (drift) | No (drift) | Yes |
| Client can bookmark page | No | Yes | Yes | Partially (save last id) |
| Total count available | No | Yes | Yes | No |
| Implementation complexity | Medium | Low | Low | Low |

---

## Further Reading

- B-tree index seeks vs full scans
- MVCC and how PostgreSQL handles `OFFSET`
- Deferred joins as an offset optimization
- Cursor stability in PostgreSQL
- Relay-style cursor pagination (GraphQL)
