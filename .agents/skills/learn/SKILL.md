---
name: learn
description: >-
  Guided workflow for exploring a system design concept across all its variants —
  explains theory, maps each variant to this project's APIs and models, implements
  approaches side by side on a dedicated branch, and generates documentation under
  docs/learning/.
---

# learn — Explore & Demonstrate System Design Concepts

**Input:** Concept to explore (e.g. "rate limiting approaches", "caching strategies", "message queue patterns").

## Step 1 — Create a Dedicated Learning Branch
Before modifying any files, isolate the work on a separate branch to keep `master` production-clean:
```bash
git checkout -b learn/<concept-slug>
```
Confirm the branch is active.

## Step 2 — Scope & Theory
1. Define the concept clearly.
2. Outline all standard variants (e.g. for Caching: Cache-Aside, Write-Through, Write-Back, Refresh-Ahead).
3. Present time/space complexity, trade-offs, and failure modes for each.

## Step 3 — Map to Pulse Chat Architecture
1. Identify which variant is currently used in the codebase.
2. Propose side-by-side demonstration implementations for the alternative variants.
3. Note: Learning implementations may intentionally implement anti-patterns for demonstration purposes.

## Step 4 — Implement Demonstrations
Implement each variant step-by-step with clear inline code commentary.

## Step 5 — Generate Documentation
Write a comprehensive guide in `docs/learning/<concept>.md`:
- Architecture diagrams and flowcharts.
- Concrete code snippets extracted directly from the implementations.
- Comparative benchmark and trade-off summary table.
