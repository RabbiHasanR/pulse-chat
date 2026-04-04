# coverage

Run pytest with coverage report for the server.

Run:
  cd server/ && pytest --cov=. --cov-report=html --cov-report=term-missing -q

Then open: server/htmlcov/index.html
