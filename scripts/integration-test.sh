#!/bin/sh
set -eu

compose_files="-f deployment/compose.yaml -f deployment/compose.integration.yaml"
project_name="wsr-evidence-integration"

cleanup() {
  docker compose -p "$project_name" $compose_files down --volumes
}
trap cleanup EXIT INT TERM

docker compose -p "$project_name" $compose_files up -d --wait database
WSR_EVIDENCE_DATABASE_URL="postgresql://wsr_evidence@127.0.0.1:55432/wsr_evidence" \
  uv run --python 3.14 alembic upgrade head
WSR_EVIDENCE_DATABASE_URL="postgresql://wsr_evidence@127.0.0.1:55432/wsr_evidence" \
  uv run --python 3.14 pytest -q -m integration tests/integration
