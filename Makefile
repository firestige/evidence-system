PYTHON_VERSION ?= 3.14
UV_RUN := uv run --python $(PYTHON_VERSION)
COMPOSE := docker compose -f deployment/compose.yaml

.PHONY: sync format lint unit integration migration deployment build check

sync:
	uv sync --locked --python $(PYTHON_VERSION)

format:
	$(UV_RUN) ruff format .

lint:
	$(UV_RUN) ruff format --check .
	$(UV_RUN) ruff check .
	$(UV_RUN) mypy

unit:
	$(UV_RUN) pytest -q tests/unit

integration:
	./scripts/integration-test.sh

migration:
	$(COMPOSE) run --rm migrate

deployment:
	./scripts/deployment-smoke.sh

build:
	uv build --python $(PYTHON_VERSION)

check: lint unit build
