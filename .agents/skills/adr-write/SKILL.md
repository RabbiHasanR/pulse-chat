---
name: adr-write
description: >-
  Guided workflow for recording a significant architecture or technical decision
  in Pulse Chat. Allocates the next sequential ADR number, drafts the record from
  the template, and links it in docs/adr/README.md.
---

# adr-write — Record an Architectural Decision

**Input:** Decision topic or technical choice (e.g. "adopt Cassandra for message history", "move media processing to AWS Lambda", "switch to WebRTC for voice calls").

## Step 1 — Determine ADR Number
Inspect `docs/adr/` for the highest existing 4-digit number.
Assign the next sequential number: `NNNN` (e.g. `0009`).

## Step 2 — Draft the ADR File
Create `docs/adr/<NNNN>-<kebab-title>.md` following `docs/adr/_TEMPLATE.md`:
- Set `# ADR-<NNNN>: <Title>`
- Set `- **Status:** proposed` (or `accepted` if already agreed upon)
- Define the context, constraints, and forces at play.
- Formulate the decision clearly in 1–2 sentences, followed by technical mechanics.
- Detail the alternatives considered and why they were rejected.
- Document positive consequences, negative trade-offs, and new rules created.

## Step 3 — Index the ADR
Add the new ADR row into the table in `docs/adr/README.md`:
```markdown
| [<NNNN>](<NNNN>-<kebab-title>.md) | <Title> | <status> |
```

## Step 4 — Validate
Run the validation script:
```bash
python3 .agents/skills/spec-sync/scripts/check_specs.py
```
Ensure ADR numbering and index link checks pass.
