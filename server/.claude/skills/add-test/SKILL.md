---
name: add-test
description: Guided test writing for Pulse Chat — selects correct fixture combination for REST/WebSocket/Celery/middleware, identifies edge cases, follows project naming and URL conventions.
---

Write tests for a specific piece of code. Follow steps in order.

$ARGUMENTS: what to test (e.g. "SendMessageView", "process_image_task", "UserSocketConsumer chat_open event")

## Step 1 — Clarify Type
What are we testing? (REST view / WebSocket consumer / Celery task / Media task / Middleware)
Select the fixture combo from the **Testing Conventions** table in `server/.claude/CLAUDE.md`.

## Step 2 — Locate the Code
Read the file being tested to understand inputs, outputs, and failure paths.

## Step 3 — Identify Edge Cases
For each test target, cover at minimum:
- Happy path (valid input, expected result)
- Missing / invalid fields
- Unauthorized access (401 / WebSocket close 4002)
- Error / exception path (e.g. S3 failure, Redis error)

## Step 4 — Plan Test Cases
List test function names before writing:
- Naming: `test_<action>_<condition>` (e.g. `test_send_message_unauthorized`, `test_process_image_s3_error`)
- File: `tests/<app>/test_<views|tasks|consumers>.py`
- Use URL constants from `tests/constants.py`, never hardcode paths

Show plan, wait for approval.

## Step 5 — Write Tests
Write all planned test functions.
For async tests, add `@pytest.mark.asyncio`.
Show, wait for approval.

## Step 6 — Run
Run `/test <test_file_path>` to confirm all pass.
