#!/bin/sh
set -eu

backup_file="${WSR_EVIDENCE_BACKUP_FILE:?backup filename is required}"
case "$backup_file" in
  ""|.*|*/*) echo "invalid backup filename" >&2; exit 2 ;;
esac

secret_file="${WSR_EVIDENCE_DATABASE_PASSWORD_FILE:?database password file is required}"
export PGPASSWORD="$(tr -d '\r\n' < "$secret_file")"
test -n "$PGPASSWORD"

pg_dump \
  --host "${WSR_EVIDENCE_DATABASE_HOST:?database host is required}" \
  --username "${WSR_EVIDENCE_DATABASE_USER:?database user is required}" \
  --dbname "${WSR_EVIDENCE_DATABASE_NAME:?database name is required}" \
  --format custom \
  --file "/backups/$backup_file"
pg_restore --list "/backups/$backup_file" >/dev/null
sha256sum "/backups/$backup_file"
