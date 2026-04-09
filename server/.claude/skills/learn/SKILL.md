---
name: learn
description: Guided workflow for exploring a system design concept across all its variants — explains theory, maps each variant to this project's APIs/models, implements all approaches side by side, and generates a docs/ file. Use when the goal is learning + demonstration, not just production implementation. Project anti-patterns may be intentionally used here for comparison.
---

Explore and implement all variants of a system design concept in this project.

$ARGUMENTS: the concept to explore (e.g. "pagination types", "caching strategies", "message queue patterns", "rate limiting approaches")

## Step 1 — Branch
Before any code is written, create a dedicated git branch:
```
git checkout -b learn/<concept-slug>
```
Example: `learn/caching-strategies`, `learn/pagination-types`, `learn/rate-limiting`

The `master` branch is production. All learning implementations stay on this branch. Confirm the branch is created before continuing.

## Step 2 — Scope
Ask if not already clear:
- What concept are we exploring?
- Any specific variants to include or skip?
- Target doc file name? (default: `docs/<concept>-comparision.md`)

## Step 3 — Explain All Variants
For each variant of the concept:
- What it is (one paragraph)
- How it works mechanically
- Time/space complexity or performance characteristic
- When to use it — and when NOT to
- Real-world examples

Present all variants. Wait for confirmation before continuing.

## Step 4 — Map to This Project
For each variant, identify:
- Which existing APIs or models are a natural fit
- Which variant is already implemented (if any)
- What new demo endpoints or models are needed to show the others
- Any variant that cannot be shown without a new model or significant change — flag it, ask if worth adding

Show mapping. Wait for approval.

## Step 5 — Implementation Plan
For each variant:
- Which file(s) change
- What new files are needed (serializer, view, URL, migration)
- Order of implementation

**Note:** This skill intentionally implements approaches that are non-standard for this project (e.g. offset pagination, raw SQL) for demonstration. Do NOT apply `django-pattern-checker` here — the goal is comparison, not enforcement.

Show plan. Wait for approval before writing any code.

## Step 6 — Implement
Implement variants one at a time. For each:
1. Write the code
2. Add URL to `<app>/urls.py`

Wait for approval after each variant before moving to the next.

## Step 7 — Generate Docs
Write `docs/<concept>.md`. Use the actual code written in Step 5 for all snippets — do not write generic examples.

Structure:
- **Introduction** — why this concept matters and where it appears in real systems
- **Variants table** — name | complexity | best for | trade-offs
- **Per-variant section** — explanation + the actual implementation from this project + when to use it
- **Comparison** — side-by-side summary of all variants
- **Further reading** — concepts and terms to search, not URLs

Show draft. Wait for approval before writing the file.

