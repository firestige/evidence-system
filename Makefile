PYTHON_VERSION ?= 3.14
UV_RUN := uv run --python $(PYTHON_VERSION)
COMPOSE := docker compose -f deployment/compose.yaml

.PHONY: sync format lint unit integration migration deployment deployment-up deployment-down backup restore build check

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

deployment-up:
	./scripts/local-deployment.sh up

deployment-down:
	./scripts/local-deployment.sh down

backup:
	./scripts/local-deployment.sh backup "$(BACKUP_FILE)"

restore:
	./scripts/local-deployment.sh restore "$(BACKUP_FILE)" "$(RESTORE_DATABASE)"

build:
	uv build --python $(PYTHON_VERSION)

check: lint unit build
