#!/bin/sh
set -eu

compose_files="-f deployment/compose.yaml -f deployment/compose.integration.yaml"
project_name="wsr-evidence-integration"
secret_dir="$(mktemp -d)"
printf '%s\n' "integration-admin-$(openssl rand -hex 16)" > "$secret_dir/admin-password"
printf '%s\n' "integration-runtime-$(openssl rand -hex 16)" > "$secret_dir/runtime-password"
printf '%s\n' "integration-backup-$(openssl rand -hex 16)" > "$secret_dir/backup-password"
export WSR_EVIDENCE_ADMIN_PASSWORD_FILE="$secret_dir/admin-password"
export WSR_EVIDENCE_RUNTIME_PASSWORD_FILE="$secret_dir/runtime-password"
export WSR_EVIDENCE_BACKUP_PASSWORD_FILE="$secret_dir/backup-password"
admin_password="$(tr -d '\r\n' < "$secret_dir/admin-password")"

cleanup() {
  docker compose -p "$project_name" $compose_files down --volumes
  rm -rf "$secret_dir"
}
trap cleanup EXIT INT TERM

docker compose -p "$project_name" $compose_files up -d --wait database
WSR_EVIDENCE_DATABASE_URL="postgresql://wsr_evidence_admin:$admin_password@127.0.0.1:55432/wsr_evidence" \
  uv run --python 3.14 alembic upgrade head
WSR_EVIDENCE_DATABASE_URL="postgresql://wsr_evidence_admin:$admin_password@127.0.0.1:55432/wsr_evidence" \
  uv run --python 3.14 pytest -q -m integration tests/integration
