#!/bin/sh
set -eu

backup_file="${WSR_EVIDENCE_BACKUP_FILE:?backup filename is required}"
restore_database="${WSR_EVIDENCE_RESTORE_DATABASE:?restore database is required}"
case "$backup_file" in
  ""|.*|*/*) echo "invalid backup filename" >&2; exit 2 ;;
esac
printf '%s' "$restore_database" | grep -Eq '^wsr_evidence_restore_[a-z0-9_]+$' || {
  echo "restore database must use the wsr_evidence_restore_ prefix" >&2
  exit 2
}
test -r "/backups/$backup_file"

secret_file="${WSR_EVIDENCE_DATABASE_PASSWORD_FILE:?database password file is required}"
export PGPASSWORD="$(tr -d '\r\n' < "$secret_file")"
test -n "$PGPASSWORD"
database_host="${WSR_EVIDENCE_DATABASE_HOST:?database host is required}"
database_user="${WSR_EVIDENCE_DATABASE_USER:?database user is required}"

exists="$(psql --host "$database_host" --username "$database_user" --dbname postgres --tuples-only --no-align --command "SELECT 1 FROM pg_database WHERE datname = '$restore_database'")"
test -z "$exists" || {
  echo "restore target already exists" >&2
  exit 3
}
createdb --host "$database_host" --username "$database_user" "$restore_database"
pg_restore --host "$database_host" --username "$database_user" --dbname "$restore_database" --exit-on-error --no-owner --no-privileges "/backups/$backup_file"
psql --host "$database_host" --username "$database_user" --dbname "$restore_database" --set=ON_ERROR_STOP=1 --set=restore_database="$restore_database" <<'SQL'
REVOKE CREATE ON SCHEMA public FROM PUBLIC;
REVOKE ALL ON DATABASE :"restore_database" FROM PUBLIC;
GRANT CONNECT ON DATABASE :"restore_database" TO wsr_evidence_runtime, wsr_evidence_backup;
GRANT USAGE ON SCHEMA public TO wsr_evidence_runtime, wsr_evidence_backup;
GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO wsr_evidence_runtime;
GRANT USAGE, SELECT, UPDATE ON ALL SEQUENCES IN SCHEMA public TO wsr_evidence_runtime;
GRANT SELECT ON ALL TABLES IN SCHEMA public TO wsr_evidence_backup;
GRANT SELECT ON ALL SEQUENCES IN SCHEMA public TO wsr_evidence_backup;
ALTER DEFAULT PRIVILEGES FOR ROLE wsr_evidence_admin IN SCHEMA public
  GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO wsr_evidence_runtime;
ALTER DEFAULT PRIVILEGES FOR ROLE wsr_evidence_admin IN SCHEMA public
  GRANT USAGE, SELECT, UPDATE ON SEQUENCES TO wsr_evidence_runtime;
ALTER DEFAULT PRIVILEGES FOR ROLE wsr_evidence_admin IN SCHEMA public
  GRANT SELECT ON TABLES TO wsr_evidence_backup;
ALTER DEFAULT PRIVILEGES FOR ROLE wsr_evidence_admin IN SCHEMA public
  GRANT SELECT ON SEQUENCES TO wsr_evidence_backup;
SQL
