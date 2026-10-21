# Pulse Chat — System Design & Learning Modules

This directory contains deep-dive educational guides and system-design case studies implemented in the Pulse Chat codebase. Anyone studying backend scalability can use these guides to understand trade-offs and real-world implementations.

## Learning Guides

| Guide | Topic | Key Concepts |
|:---|:---|:---|
| [caching.md](caching.md) | Distributed Caching Strategies | Cache-Aside, Write-Through, Write-Behind, Cache stampede, Redis TTLs |
| [pagination.md](pagination.md) | Database Pagination Mechanics | Offset vs Keysets vs Cursors, B-tree indexes, page drift, benchmark comparisons |
| [celery-scaling.md](celery-scaling.md) | Asynchronous Task Scaling | Worker isolation, prefetch limits, concurrency models (gevent vs prefork), dead-letter handling |

## Architecture & Topology Diagrams

- [caching_layers_overview.svg](caching_layers_overview.svg) — Multi-tier caching architecture (HTTP, Redis, DB).
- [cache_aside_flow.svg](cache_aside_flow.svg) — Cache-Aside read/write lifecycle.
- [cache_eviction_strategies.svg](cache_eviction_strategies.svg) — LRU, LFU, and TTL eviction policies.
- [cache_invalidation_strategies.svg](cache_invalidation_strategies.svg) — Event-driven vs TTL invalidation.
- [distributed_cache_topology.svg](distributed_cache_topology.svg) — Redis cluster, sentinel, and standalone topology.
