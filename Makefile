BACKEND_PY := backend/.venv/bin/python
AGENT_PY   := local_agent/.venv/bin/python

.PHONY: test test-backend test-agent test-hygiene lint build check

## Full suite. Backend tests need MongoDB on TEST_MONGO_URL (default localhost:27017);
## they use a throw-away vc_test_* database that is dropped afterwards.
test: test-hygiene test-agent test-backend

test-backend:
	$(BACKEND_PY) -m pytest tests/backend

test-agent:
	$(AGENT_PY) -m pytest tests/agent

test-hygiene:
	$(BACKEND_PY) -m pytest tests/test_hygiene.py

lint:
	$(BACKEND_PY) -m ruff check --select F,E9 backend local_agent tests

build:
	cd frontend && yarn build

## Phase gate: everything must be green before a phase is merged.
check: lint test build
