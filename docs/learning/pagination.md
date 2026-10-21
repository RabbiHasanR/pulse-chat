# Pagination in Modern Software Engineering

> A comprehensive guide covering every pagination type, database query mechanics, performance implications, and real-world optimization strategies.

---

## Table of Contents

1. [What is Pagination?](#what-is-pagination)
2. [Why Pagination Matters](#why-pagination-matters)
3. [Types of Pagination](#types-of-pagination)
   - [1. Offset-Based Pagination](#1-offset-based-pagination)
   - [2. Page-Based Pagination](#2-page-based-pagination)
   - [3. Cursor-Based Pagination](#3-cursor-based-pagination)
   - [4. Keyset / Seek Pagination](#4-keyset--seek-pagination)
   - [5. Time-Based Pagination](#5-time-based-pagination)
   - [6. Relay-Style Pagination (GraphQL)](#6-relay-style-pagination-graphql)
   - [7. Infinite Scroll / Load More](#7-infinite-scroll--load-more)
   - [8. Hybrid Pagination](#8-hybrid-pagination)
4. [Performance Comparison Table](#performance-comparison-table)
5. [Choosing the Right Strategy](#choosing-the-right-strategy)
6. [General Optimization Tips](#general-optimization-tips)

---

## What is Pagination?

Pagination is the process of dividing a large dataset into smaller, discrete chunks (pages) that can be fetched and displayed incrementally. Instead of loading 1 million rows in a single request — which would overwhelm the database, network, and client — pagination allows you to retrieve data in manageable batches.

```
Total Records: 1,000,000
Page Size:          100
Total Pages:     10,000
```

Pagination applies everywhere: REST APIs, GraphQL APIs, database queries, UI components, search engines, and messaging feeds.

---

## Why Pagination Matters

- **Performance**: Avoids scanning and transmitting huge datasets
- **User Experience**: Faster load times and responsive interfaces
- **Resource Management**: Reduces CPU, memory, and network usage
- **Scalability**: Allows systems to grow without degrading response times
- **Stability**: Prevents timeouts, out-of-memory crashes, and rate limit violations

---

## Types of Pagination

---

### 1. Offset-Based Pagination

#### Concept

The most widely used form of pagination. It skips a fixed number of rows (`OFFSET`) and then returns the next batch (`LIMIT`). The client tells the server how many records to skip.

#### API Example

```
GET /api/products?limit=10&offset=40
```
> "Skip the first 40 rows, give me the next 10."

#### SQL Query

```sql
-- Page 5, 10 records per page → offset = (5-1) * 10 = 40
SELECT id, name, price, created_at
FROM products
ORDER BY created_at DESC
LIMIT 10 OFFSET 40;
```

#### How the Database Executes This

1. The database performs a **full or index scan** of the table.
2. It reads and discards the first 40 rows.
3. It returns the next 10 rows.

The critical problem: **the database still reads and discards all rows before the offset**, even if it doesn't return them. For `OFFSET 1000000`, the DB reads 1,000,000 rows just to throw them away.

#### Code Example (Node.js + PostgreSQL)

```javascript
async function getProducts(page, pageSize) {
  const offset = (page - 1) * pageSize;

  const { rows } = await db.query(
    `SELECT id, name, price
     FROM products
     ORDER BY created_at DESC
     LIMIT $1 OFFSET $2`,
    [pageSize, offset]
  );

  const { rows: countRows } = await db.query(
    `SELECT COUNT(*) FROM products`
  );

  return {
    data: rows,
    total: parseInt(countRows[0].count),
    page,
    pageSize,
    totalPages: Math.ceil(countRows[0].count / pageSize),
  };
}
```

#### Performance Analysis

| Dataset Size | Offset      | Query Time | Notes                        |
|--------------|-------------|------------|------------------------------|
| 100,000 rows | OFFSET 100  | ~5ms       | Fast — small skip            |
| 100,000 rows | OFFSET 50000| ~120ms     | Slower — reads half the table|
| 1,000,000 rows | OFFSET 900000 | ~2s+   | Very slow — reads 90% of data|

#### Problems

- **Deep page performance**: `OFFSET 900000` forces the DB to scan 900,000 rows.
- **Data inconsistency**: If a new row is inserted during pagination, page results shift — records can be skipped or duplicated.
- **Total count cost**: `COUNT(*)` on large tables is expensive and often avoided.

#### When to Use

- Admin dashboards and internal tools
- Small-to-medium datasets (< 100,000 rows)
- When random page access is required (jump to page 47)
- When approximate counts are needed

---

### 2. Page-Based Pagination

#### Concept

A UI-friendly variant of offset-based pagination. Instead of `offset`, the client sends a `page` number. The server computes the offset internally.

#### API Example

```
GET /api/articles?page=4&per_page=20
```

#### SQL Query

```sql
-- page=4, per_page=20 → offset = (4-1) * 20 = 60
SELECT id, title, author, published_at
FROM articles
ORDER BY published_at DESC
LIMIT 20 OFFSET 60;
```

#### Code Example (Python + SQLAlchemy)

```python
def get_articles(page: int, per_page: int = 20):
    offset = (page - 1) * per_page
    
    articles = db.session.query(Article)\
        .order_by(Article.published_at.desc())\
        .limit(per_page)\
        .offset(offset)\
        .all()
    
    total = db.session.query(func.count(Article.id)).scalar()
    
    return {
        "data": articles,
        "page": page,
        "per_page": per_page,
        "total": total,
        "total_pages": math.ceil(total / per_page)
    }
```

#### Response Example

```json
{
  "data": [...],
  "page": 4,
  "per_page": 20,
  "total": 3842,
  "total_pages": 193
}
```

#### Performance Analysis

Identical to offset-based pagination since it computes the same `OFFSET` under the hood. The only difference is the UX abstraction — numbered page buttons instead of raw offsets.

#### When to Use

- E-commerce product listings
- Blog post archives
- Search result pages
- Anywhere users benefit from numbered navigation

---

### 3. Cursor-Based Pagination

#### Concept

Instead of a numeric offset, the server issues an opaque **cursor** — an encoded pointer to the last seen item. The client passes this cursor back on the next request to fetch the following batch. The cursor is typically a base64-encoded JSON object containing a unique ID and/or timestamp.

#### API Example

```
# First request
GET /api/posts?limit=20

# Subsequent request
GET /api/posts?limit=20&after=eyJpZCI6MTAwLCJ0cyI6MTcwMDAwMH0=
```

#### Cursor Encoding/Decoding

```javascript
// Encoding a cursor
function encodeCursor(id, timestamp) {
  const payload = JSON.stringify({ id, ts: timestamp });
  return Buffer.from(payload).toString('base64');
}

// Decoding a cursor
function decodeCursor(cursor) {
  const payload = Buffer.from(cursor, 'base64').toString('utf-8');
  return JSON.parse(payload);
}

// Example
const cursor = encodeCursor(100, 1700000000);
// → "eyJpZCI6MTAwLCJ0cyI6MTcwMDAwMDAwMH0="
```

#### SQL Query

```sql
-- Decode cursor → { id: 100, ts: 1700000000 }
SELECT id, title, created_at
FROM posts
WHERE (created_at, id) < ('2023-11-14 12:00:00', 100)
ORDER BY created_at DESC, id DESC
LIMIT 20;
```

> The `(created_at, id) <` composite comparison is the key: it efficiently jumps right to where the last cursor pointed, with no rows discarded.

#### Code Example (Node.js + MySQL)

```javascript
async function getPosts(limit, cursor) {
  let whereClause = '';
  let params = [limit];

  if (cursor) {
    const { id, ts } = decodeCursor(cursor);
    whereClause = `WHERE (created_at < ? OR (created_at = ? AND id < ?))`;
    params = [new Date(ts * 1000), new Date(ts * 1000), id, limit];
  }

  const posts = await db.query(
    `SELECT id, title, created_at
     FROM posts
     ${whereClause}
     ORDER BY created_at DESC, id DESC
     LIMIT ?`,
    params
  );

  const lastPost = posts[posts.length - 1];
  const nextCursor = lastPost
    ? encodeCursor(lastPost.id, Math.floor(lastPost.created_at / 1000))
    : null;

  return {
    data: posts,
    nextCursor,
    hasMore: posts.length === limit,
  };
}
```

#### Response Example

```json
{
  "data": [...],
  "nextCursor": "eyJpZCI6ODAsInRzIjoxNjk5OTk5OTAwfQ==",
  "hasMore": true
}
```

#### Performance Analysis

| Dataset Size   | Page Position | Query Time | Notes                        |
|----------------|---------------|------------|------------------------------|
| 1,000,000 rows | "Page 1"      | ~2ms       | Index seek — extremely fast  |
| 1,000,000 rows | "Page 50,000" | ~2ms       | Same! Cursor skips straight there |
| 1,000,000 rows | Deep pages    | ~2ms       | Consistent regardless of depth |

This is the **core advantage** of cursor pagination: O(1) time regardless of position.

#### Problems

- No random page access — you can't jump to "page 47"
- Cursors are opaque — users can't bookmark or share a position
- Bi-directional navigation requires both `before` and `after` cursors
- Cursor invalidation if rows are deleted

#### When to Use

- Social media feeds (Twitter, Instagram)
- Messaging applications (Slack, Discord)
- High-volume APIs where deep scrolling is common
- Any dataset > 100,000 rows

---

### 4. Keyset / Seek Pagination

#### Concept

Keyset pagination is conceptually similar to cursor-based, but uses **actual column values** (not encoded cursors) to determine where to start the next page. It leverages the database's natural ordering via index seeks.

#### API Example

```
GET /api/orders?after_id=5000&limit=25
GET /api/events?after_created_at=2024-01-15T10:30:00Z&after_id=9900&limit=25
```

#### SQL Query (Single Key)

```sql
-- Simple keyset: fetch orders after id=5000
SELECT id, order_number, total, created_at
FROM orders
WHERE id > 5000
ORDER BY id ASC
LIMIT 25;
```

#### SQL Query (Composite Key — Handles Ties)

```sql
-- Composite keyset: handles rows with identical created_at timestamps
SELECT id, event_name, created_at
FROM events
WHERE (created_at, id) > ('2024-01-15 10:30:00', 9900)
ORDER BY created_at ASC, id ASC
LIMIT 25;
```

#### How the Database Executes This

1. The query uses an **index range scan** starting from the keyset value.
2. No rows are read before the start point — the index seek jumps directly.
3. Only the next 25 rows after the key are fetched.

```
Index on (created_at, id):
[...] → [2024-01-15 10:29:55, 9897]
        [2024-01-15 10:29:58, 9898]
        [2024-01-15 10:30:00, 9900]  ← START HERE
        [2024-01-15 10:30:01, 9901]  ← returned
        [2024-01-15 10:30:02, 9902]  ← returned
        ...                           ← returned (25 total)
```

#### Code Example (Python + Raw SQL)

```python
def get_events(after_created_at=None, after_id=None, limit=25):
    if after_created_at and after_id:
        query = """
            SELECT id, event_name, payload, created_at
            FROM events
            WHERE (created_at, id) > (%s, %s)
            ORDER BY created_at ASC, id ASC
            LIMIT %s
        """
        params = (after_created_at, after_id, limit)
    else:
        query = """
            SELECT id, event_name, payload, created_at
            FROM events
            ORDER BY created_at ASC, id ASC
            LIMIT %s
        """
        params = (limit,)

    rows = db.execute(query, params)
    last = rows[-1] if rows else None

    return {
        "data": rows,
        "next_after_created_at": last["created_at"] if last else None,
        "next_after_id": last["id"] if last else None,
        "has_more": len(rows) == limit,
    }
```

#### Required Index

```sql
-- The composite index is critical for performance
CREATE INDEX idx_events_created_at_id ON events (created_at ASC, id ASC);
```

#### Keyset vs Cursor Comparison

| Aspect         | Cursor-Based              | Keyset                     |
|----------------|---------------------------|----------------------------|
| Transparency   | Opaque (base64)           | Transparent (raw values)   |
| Client caching | ✅ Easy                   | ✅ Easy                    |
| DB performance | ✅ Excellent              | ✅ Excellent               |
| URL friendliness | ❌ Ugly                 | ✅ Readable                |
| Sort flexibility| ❌ Baked into cursor     | ✅ Explicit                |

#### When to Use

- Event logs and audit trails
- Time-series data (metrics, monitoring)
- Any scenario where transparent pagination values are preferable
- APIs that need URL-bookmarkable page positions

---

### 5. Time-Based Pagination

#### Concept

Uses timestamps as the primary pagination boundary. Common in log systems, activity feeds, and analytics pipelines where data is naturally organized by time.

#### API Example

```
GET /api/logs?before=2024-01-20T00:00:00Z&limit=100
GET /api/logs?after=2024-01-19T00:00:00Z&before=2024-01-20T00:00:00Z
```

#### SQL Query

```sql
-- Fetch logs before a given timestamp
SELECT id, level, message, metadata, logged_at
FROM application_logs
WHERE logged_at < '2024-01-20 00:00:00'
ORDER BY logged_at DESC
LIMIT 100;

-- Fetch logs within a time window
SELECT id, level, message, metadata, logged_at
FROM application_logs
WHERE logged_at BETWEEN '2024-01-19 00:00:00' AND '2024-01-20 00:00:00'
ORDER BY logged_at DESC
LIMIT 100;
```

#### Handling Timestamp Ties (Critical!)

Timestamps are rarely unique. Multiple log entries can share the same millisecond. Without a tiebreaker, pagination can skip or duplicate rows.

```sql
-- Bad: ties break pagination
SELECT id, logged_at FROM logs
WHERE logged_at < '2024-01-20T00:00:05.123Z'
ORDER BY logged_at DESC
LIMIT 100;

-- Good: composite boundary handles ties
SELECT id, logged_at FROM logs
WHERE (logged_at, id) < ('2024-01-20T00:00:05.123Z', 4500)
ORDER BY logged_at DESC, id DESC
LIMIT 100;
```

#### Code Example (Go + PostgreSQL)

```go
type LogPage struct {
    Logs       []Log      `json:"logs"`
    Before     *time.Time `json:"before,omitempty"`
    After      *time.Time `json:"after,omitempty"`
    HasMore    bool       `json:"has_more"`
}

func GetLogs(before *time.Time, limit int) (*LogPage, error) {
    var rows *sql.Rows
    var err error

    if before != nil {
        rows, err = db.Query(`
            SELECT id, level, message, logged_at
            FROM application_logs
            WHERE logged_at < $1
            ORDER BY logged_at DESC
            LIMIT $2
        `, before, limit)
    } else {
        rows, err = db.Query(`
            SELECT id, level, message, logged_at
            FROM application_logs
            ORDER BY logged_at DESC
            LIMIT $1
        `, limit)
    }

    if err != nil {
        return nil, err
    }
    defer rows.Close()

    logs := scanLogs(rows)
    var nextBefore *time.Time
    if len(logs) > 0 {
        t := logs[len(logs)-1].LoggedAt
        nextBefore = &t
    }

    return &LogPage{
        Logs:    logs,
        Before:  nextBefore,
        HasMore: len(logs) == limit,
    }, nil
}
```

#### Required Index

```sql
-- Index on the time column is mandatory
CREATE INDEX idx_logs_logged_at ON application_logs (logged_at DESC);

-- For high-volume tables: partition by time range
CREATE TABLE application_logs_2024_01
    PARTITION OF application_logs
    FOR VALUES FROM ('2024-01-01') TO ('2024-02-01');
```

#### Performance Impact

| Scenario                    | Impact    | Notes                                    |
|-----------------------------|-----------|------------------------------------------|
| Indexed timestamp           | ✅ Fast   | Range scan on B-tree index               |
| No index on timestamp       | ❌ Slow   | Full table scan every query              |
| Timestamp ties without ID   | ❌ Buggy  | Missing/duplicate rows                   |
| Time-partitioned table      | ✅✅ Fast | Partition pruning skips irrelevant months|

#### When to Use

- Application and system log viewers
- Analytics dashboards (event timelines)
- IoT sensor data feeds
- Financial transaction histories

---

### 6. Relay-Style Pagination (GraphQL)

#### Concept

The [GraphQL Relay spec](https://relay.dev/graphql/connections.htm) defines a standardized cursor-based pagination model using **Connections**, **Edges**, **Nodes**, and **PageInfo**. It supports both forward (`first`/`after`) and backward (`last`/`before`) navigation.

#### GraphQL Schema

```graphql
type Query {
  products(
    first: Int
    after: String
    last: Int
    before: String
  ): ProductConnection!
}

type ProductConnection {
  edges: [ProductEdge!]!
  nodes: [Product!]!
  pageInfo: PageInfo!
  totalCount: Int
}

type ProductEdge {
  cursor: String!
  node: Product!
}

type PageInfo {
  hasNextPage: Boolean!
  hasPreviousPage: Boolean!
  startCursor: String
  endCursor: String
}

type Product {
  id: ID!
  name: String!
  price: Float!
}
```

#### GraphQL Query

```graphql
query GetProducts($after: String) {
  products(first: 20, after: $after) {
    edges {
      cursor
      node {
        id
        name
        price
      }
    }
    pageInfo {
      hasNextPage
      endCursor
    }
    totalCount
  }
}
```

#### Response

```json
{
  "data": {
    "products": {
      "edges": [
        {
          "cursor": "Y3Vyc29yOjE=",
          "node": { "id": "1", "name": "Widget A", "price": 29.99 }
        },
        {
          "cursor": "Y3Vyc29yOjI=",
          "node": { "id": "2", "name": "Widget B", "price": 49.99 }
        }
      ],
      "pageInfo": {
        "hasNextPage": true,
        "endCursor": "Y3Vyc29yOjI="
      },
      "totalCount": 842
    }
  }
}
```

#### SQL Query (Resolver Implementation)

```sql
-- Forward pagination: first=20, after=cursor(id=50)
SELECT id, name, price, created_at
FROM products
WHERE id > 50
ORDER BY id ASC
LIMIT 20;

-- Backward pagination: last=20, before=cursor(id=200)
SELECT id, name, price, created_at
FROM products
WHERE id < 200
ORDER BY id DESC
LIMIT 20;
-- Note: re-reverse in application code to restore ASC order
```

#### Resolver Implementation (JavaScript)

```javascript
const resolvers = {
  Query: {
    products: async (_, { first = 20, after, last, before }) => {
      let query = db('products').orderBy('id', 'asc');
      
      if (after) {
        const { id } = decodeCursor(after);
        query = query.where('id', '>', id);
        query = query.limit(first);
      } else if (before) {
        const { id } = decodeCursor(before);
        query = query.where('id', '<', id).orderBy('id', 'desc').limit(last);
      } else {
        query = query.limit(first);
      }

      const rows = await query;
      const edges = rows.map(row => ({
        cursor: encodeCursor({ id: row.id }),
        node: row,
      }));

      return {
        edges,
        nodes: rows,
        pageInfo: {
          hasNextPage: rows.length === first,
          hasPreviousPage: !!after,
          startCursor: edges[0]?.cursor,
          endCursor: edges[edges.length - 1]?.cursor,
        },
        totalCount: await db('products').count('id as count').first(),
      };
    },
  },
};
```

#### Performance Analysis

Relay-style pagination inherits the performance of cursor-based pagination. The spec itself doesn't dictate DB implementation, so performance depends entirely on how the resolver executes the query. When backed by a proper keyset query + index, it's O(1) per page.

#### When to Use

- Any GraphQL API
- When building client apps using Apollo, Relay, or Urql
- Systems that need standardized, interoperable pagination contracts

---

### 7. Infinite Scroll / Load More

#### Concept

Infinite scroll and "Load More" are **UX patterns**, not standalone pagination types. They are always backed by one of the above strategies — most commonly cursor-based or offset-based. The client automatically fetches the next batch as the user scrolls (infinite scroll) or after clicking a button (load more).

#### Implementation (React + Cursor-Based API)

```jsx
import { useState, useEffect, useRef, useCallback } from 'react';

function InfinitePostFeed() {
  const [posts, setPosts] = useState([]);
  const [cursor, setCursor] = useState(null);
  const [hasMore, setHasMore] = useState(true);
  const [loading, setLoading] = useState(false);
  const observerRef = useRef(null);

  const fetchMore = useCallback(async () => {
    if (loading || !hasMore) return;
    setLoading(true);

    const url = cursor
      ? `/api/posts?limit=20&after=${cursor}`
      : `/api/posts?limit=20`;

    const res = await fetch(url);
    const data = await res.json();

    setPosts(prev => [...prev, ...data.posts]);
    setCursor(data.nextCursor);
    setHasMore(data.hasMore);
    setLoading(false);
  }, [cursor, hasMore, loading]);

  // Intersection Observer for auto-trigger
  useEffect(() => {
    const observer = new IntersectionObserver(entries => {
      if (entries[0].isIntersecting) fetchMore();
    });
    if (observerRef.current) observer.observe(observerRef.current);
    return () => observer.disconnect();
  }, [fetchMore]);

  return (
    <div>
      {posts.map(post => <PostCard key={post.id} post={post} />)}
      {loading && <Spinner />}
      <div ref={observerRef} style={{ height: '1px' }} />
    </div>
  );
}
```

#### Performance Impact

| Factor            | Infinite Scroll        | Load More Button       |
|-------------------|------------------------|------------------------|
| API calls         | Automatic, frequent    | Manual, user-controlled|
| UX engagement     | High (addictive)       | Deliberate             |
| Accessibility     | ❌ Poor (no anchor)    | ✅ Better              |
| Back navigation   | ❌ Loses position      | ❌ Loses position      |
| SEO               | ❌ Bad by default      | ❌ Bad by default      |

#### SEO Considerations

Infinite scroll is problematic for SEO. Search engines cannot reliably crawl dynamically loaded content. Mitigations:

```html
<!-- 1. Use rel="next" / rel="prev" link hints -->
<link rel="next" href="/posts?page=2">

<!-- 2. Render server-side for crawlers (SSR/SSG) -->
<!-- 3. Use paginated URLs with history.pushState() -->
```

#### When to Use

- Social media feeds
- Image/video galleries
- Content discovery feeds
- When retention and engagement are prioritized over navigability

---

### 8. Hybrid Pagination

#### Concept

Hybrid pagination combines two or more strategies to balance their trade-offs. The most common approach: use offset pagination for early pages (fast, supports random access), then switch to cursor-based for deep pages (stable, performant).

#### Strategy: Offset-First, Cursor-Deep

```javascript
const CURSOR_THRESHOLD = 50; // Switch to cursor after page 50

async function getPaginatedData({ page, cursor, limit = 20 }) {
  // Deep pages → use cursor
  if (cursor || page > CURSOR_THRESHOLD) {
    return cursorBasedQuery(cursor, limit);
  }

  // Early pages → use offset
  return offsetBasedQuery(page, limit);
}

async function offsetBasedQuery(page, limit) {
  const offset = (page - 1) * limit;
  const rows = await db.query(
    `SELECT * FROM products ORDER BY id ASC LIMIT $1 OFFSET $2`,
    [limit, offset]
  );
  const lastId = rows[rows.length - 1]?.id;
  return {
    data: rows,
    nextCursor: page >= CURSOR_THRESHOLD ? encodeCursor(lastId) : null,
    nextPage: page + 1,
  };
}

async function cursorBasedQuery(cursor, limit) {
  const { id } = decodeCursor(cursor);
  const rows = await db.query(
    `SELECT * FROM products WHERE id > $1 ORDER BY id ASC LIMIT $2`,
    [id, limit]
  );
  return {
    data: rows,
    nextCursor: rows.length ? encodeCursor(rows[rows.length - 1].id) : null,
  };
}
```

#### Elasticsearch Hybrid (search_after)

Elasticsearch's `search_after` is a perfect real-world example of hybrid pagination. It uses regular pagination for early results and `search_after` (keyset-style) for deep scrolling:

```json
// First page — regular
POST /products/_search
{
  "size": 20,
  "sort": [{ "price": "asc" }, { "_id": "asc" }]
}

// Subsequent pages — search_after (deep pagination)
POST /products/_search
{
  "size": 20,
  "sort": [{ "price": "asc" }, { "_id": "asc" }],
  "search_after": [29.99, "product-id-123"]
}
```

#### When to Use

- Large platforms with both search and browsing (e.g. e-commerce)
- Systems where users occasionally need deep access but mostly browse early pages
- Elasticsearch, OpenSearch, or similar search backends

---

## Performance Comparison Table

| Pagination Type   | Random Access | Stable Results | Deep Page Perf | Count Support | Complexity |
|-------------------|:---:|:---:|:---:|:---:|:---:|
| Offset-Based      | ✅  | ❌  | ❌ Degrades   | ✅            | Low        |
| Page-Based        | ✅  | ❌  | ❌ Degrades   | ✅            | Low        |
| Cursor-Based      | ❌  | ✅  | ✅ Constant   | ❌            | Medium     |
| Keyset/Seek       | ❌  | ✅  | ✅ Constant   | ❌            | Medium     |
| Time-Based        | ⚠️ Partial | ⚠️ Partial | ✅ With index | ⚠️ Partial | Medium |
| Relay (GraphQL)   | ❌  | ✅  | ✅ Constant   | ✅ (optional) | High       |
| Infinite Scroll   | ❌  | Depends on backend | Depends | ❌ | Low (UX)  |
| Hybrid            | ⚠️ Partial | ✅  | ✅ Constant   | ⚠️ Partial | High       |

---

## Choosing the Right Strategy

```
Do you need random page access (jump to page 47)?
├── YES → Offset or Page-Based
│         └── Is the dataset large (> 100k rows)?
│             ├── YES → Consider Hybrid (offset early, cursor deep)
│             └── NO  → Offset/Page-Based is fine
└── NO  → Cursor-Based or Keyset

Is this a GraphQL API?
└── YES → Relay-Style Pagination

Is the data primarily time-series (logs, events, metrics)?
└── YES → Time-Based Pagination (with composite tiebreaker)

Is the UI a social feed or infinite list?
└── YES → Infinite Scroll (backed by Cursor-Based API)

Is the dataset massive (> 1M rows) or distributed (Elasticsearch)?
└── YES → Keyset / Hybrid
```

---

## General Optimization Tips

### 1. Always Index Your Sort Column

```sql
-- Without index: full table scan on every page
-- With index: range scan or index seek

CREATE INDEX idx_products_created_at ON products (created_at DESC);
CREATE INDEX idx_orders_id ON orders (id ASC);

-- Composite index for keyset pagination
CREATE INDEX idx_events_ts_id ON events (created_at DESC, id DESC);
```

### 2. Avoid COUNT(*) on Large Tables

```sql
-- ❌ Slow: exact count scans entire table
SELECT COUNT(*) FROM products;

-- ✅ Fast approximate count (PostgreSQL)
SELECT reltuples::BIGINT AS estimate
FROM pg_class
WHERE relname = 'products';

-- ✅ Cached count with triggers or materialized views
CREATE MATERIALIZED VIEW product_count AS SELECT COUNT(*) FROM products;
```

### 3. Use Covering Indexes

A covering index includes all columns needed by the query, so the database never touches the main table rows:

```sql
-- Query fetches id, name, price, created_at
-- A covering index avoids a second lookup ("heap fetch")
CREATE INDEX idx_products_covering
ON products (created_at DESC, id DESC)
INCLUDE (name, price);
```

### 4. Cache Page Results

```javascript
const redis = require('redis');
const client = redis.createClient();

async function getCachedPage(page, ttl = 60) {
  const cacheKey = `products:page:${page}`;
  const cached = await client.get(cacheKey);
  if (cached) return JSON.parse(cached);

  const data = await fetchFromDB(page);
  await client.setEx(cacheKey, ttl, JSON.stringify(data));
  return data;
}
```

### 5. Limit Maximum Page Size

Never let clients request unlimited data:

```javascript
const MAX_PAGE_SIZE = 100;

function validateLimit(requestedLimit) {
  const limit = parseInt(requestedLimit) || 20;
  return Math.min(limit, MAX_PAGE_SIZE);
}
```

### 6. Use Deferred Joins for Offset Queries

When using OFFSET on large tables, fetch only IDs first, then join:

```sql
-- ❌ Slow: fetches all columns for 900,000 discarded rows
SELECT id, name, description, metadata, price, stock, created_at
FROM products
ORDER BY created_at DESC
LIMIT 20 OFFSET 900000;

-- ✅ Fast: offset on ID only (thin index scan), then join
SELECT p.*
FROM products p
JOIN (
  SELECT id FROM products
  ORDER BY created_at DESC
  LIMIT 20 OFFSET 900000
) AS ids ON p.id = ids.id;
```

### 7. Partition Large Tables by Time

```sql
-- Partition a logs table by month
CREATE TABLE application_logs (
  id BIGSERIAL,
  message TEXT,
  logged_at TIMESTAMPTZ NOT NULL
) PARTITION BY RANGE (logged_at);

CREATE TABLE logs_2024_01 PARTITION OF application_logs
  FOR VALUES FROM ('2024-01-01') TO ('2024-02-01');

CREATE TABLE logs_2024_02 PARTITION OF application_logs
  FOR VALUES FROM ('2024-02-01') TO ('2024-03-01');
```

Time-based queries now only scan the relevant partition instead of the entire table.

---

*Last updated: March 2026 — Covers PostgreSQL, MySQL, MongoDB, Elasticsearch, and GraphQL ecosystems.*