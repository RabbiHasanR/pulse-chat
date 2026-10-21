---
name: add-test
description: >-
  Guided test writing for Pulse Chat — selects correct fixture combinations
  for REST, WebSocket, Celery, and middleware, references spec acceptance criteria,
  and follows project conventions.
---

# add-test — Guided Test Writing

**Input:** Test target or spec ID (e.g. "SendMessageView", "CHAT-001", "UserSocketConsumer chat_open").

## Step 1 — Identify Governing Spec & Acceptance Criteria
1. Locate the governing spec in `docs/specs/`.
2. Extract the specific acceptance criteria being tested (`<SPEC>.AC<n>`).
3. Include the AC ID directly in the test function docstring:
   ```python
   def test_send_message_updates_delivery_status(auth_client, user, another_user):
       """CHAT-001.AC3: Status is delivered when recipient is online"""
   ```

## Step 2 — Select Fixture Combinations (`tests/conftest.py`)

| Scenario | Fixtures |
| --- | --- |
| REST View | `auth_client`, `user`, `another_user` |
| WebSocket Consumer | `patch_redis`, `fake_redis`, `helpers`, `@pytest.mark.asyncio` |
| Celery Task | `patch_global_s3_client`, `fake_redis` |
| Media Task | `patch_global_s3_client`, `fake_redis`, `media_asset` |
| Middleware | `mock_request`, `issue_bound_token` |

## Step 3 — File Placement & Naming
- File locations: `server/tests/<app>/test_<views|tasks|consumers>.py`.
- Function naming: `test_<action>_<condition>`.
- Use URL constants from `server/tests/constants.py` rather than hardcoded URL strings.

## Step 4 — Implement Minimum Test Cases
For each endpoint or consumer event, implement:
1. Happy path (valid input, expected database state and response).
2. Validation failures (missing fields, invalid values $\to$ HTTP 400).
3. Authorization failure (unauthenticated $\to$ HTTP 401).
4. Edge cases (duplicate records, nonexistent foreign keys).
