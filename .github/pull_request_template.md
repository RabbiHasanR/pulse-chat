## Description

<!-- Describe the changes introduced by this pull request and the rationale behind them. -->

## Spec-Driven Compliance Checklist

- [ ] **Governing Spec**: Governed by `docs/specs/<area>/<SPEC_ID>.md`
  - Spec status: `approved` (before coding) / `implemented` (after coding)
  - `last_verified` date updated
- [ ] **Acceptance Criteria**: All relevant criteria from the spec are satisfied:
  - [ ] `<SPEC_ID>.AC1`: ...
  - [ ] `<SPEC_ID>.AC2`: ...
- [ ] **Spec Validator**: Passed validation with zero errors:
  ```bash
  python3 .agents/skills/spec-sync/scripts/check_specs.py
  ```
- [ ] **Contracts Updated**:
  - [ ] `docs/contracts/rest-api.md` (if REST endpoints added or changed)
  - [ ] `docs/contracts/websocket-events.md` (if WebSocket events added or changed)
- [ ] **Architectural Decision Records (ADRs)**:
  - [ ] Complies with all existing ADRs in `docs/adr/`
  - [ ] New ADR added under `docs/adr/` if new architectural decisions were made
- [ ] **Known Issues**:
  - [ ] If fixing a known issue, updated `docs/KNOWN_ISSUES.md` (e.g., `BUG-xxx`)
  - [ ] If discovering a known deviation/bug, logged it in `docs/KNOWN_ISSUES.md`

## Testing

- [ ] Unit / Integration tests added or updated in `server/tests/`
- [ ] Test cases cite spec criteria in docstrings (e.g. `"""SPEC-001.AC1"""`)
- [ ] All tests passing
