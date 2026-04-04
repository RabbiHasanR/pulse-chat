# test

Run pytest for the server.

$ARGUMENTS: optional path or test ID. Examples:
  (empty)                                        — run all tests
  tests/chats/                                   — run chats tests only
  tests/users/test_user_views.py                 — single file
  tests/users/test_user_views.py::TestClass      — single class
  tests/users/test_user_views.py::TestClass::test_name — single test

Run:
  cd server/ && pytest $ARGUMENTS -v
