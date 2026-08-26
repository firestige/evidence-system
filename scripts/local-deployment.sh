#!/bin/sh
set -eu

project_name="${WSR_EVIDENCE_COMPOSE_PROJECT:-wsr-evidence}"
secret_dir="deployment/.secrets"
compose_file="deployment/compose.yaml"

ensure_secret() {
  target="$1"
  if test ! -f "$target"; then
    umask 077
    openssl rand -hex 32 > "$target"
  fi
  test -s "$target"
}

ensure_secrets() {
  mkdir -p "$secret_dir"
  ensure_secret "$secret_dir/admin-password"
  ensure_secret "$secret_dir/runtime-password"
  ensure_secret "$secret_dir/backup-password"
}

command="${1:-}"
case "$command" in
  up)
    ensure_secrets
    docker compose -p "$project_name" -f "$compose_file" up --build --detach --wait
    ;;
  down)
    docker compose -p "$project_name" -f "$compose_file" down
    ;;
  backup)
    ensure_secrets
    backup_file="${2:?backup filename is required}"
    docker compose -p "$project_name" -f "$compose_file" --profile operations run --rm -e "WSR_EVIDENCE_BACKUP_FILE=$backup_file" backup
    ;;
  restore)
    ensure_secrets
    backup_file="${2:?backup filename is required}"
    restore_database="${3:?restore database is required}"
    docker compose -p "$project_name" -f "$compose_file" --profile operations run --rm -e "WSR_EVIDENCE_BACKUP_FILE=$backup_file" -e "WSR_EVIDENCE_RESTORE_DATABASE=$restore_database" restore
    ;;
  *)
    echo "usage: $0 {up|down|backup <file>|restore <file> <wsr_evidence_restore_name>}" >&2
    exit 2
    ;;
esac
