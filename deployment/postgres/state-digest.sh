#!/bin/sh
set -eu

database_name="${WSR_EVIDENCE_DATABASE_NAME:?database name is required}"
secret_file="${WSR_EVIDENCE_DATABASE_PASSWORD_FILE:?database password file is required}"
export PGPASSWORD="$(tr -d '\r\n' < "$secret_file")"
test -n "$PGPASSWORD"
state_file="$(mktemp)"
trap 'rm -f "$state_file"' EXIT INT TERM

query() {
  table="$1"
  order="$2"
  printf 'table:%s\n' "$table" >> "$state_file"
  psql \
    --host "${WSR_EVIDENCE_DATABASE_HOST:?database host is required}" \
    --username "${WSR_EVIDENCE_DATABASE_USER:?database user is required}" \
    --dbname "$database_name" \
    --set=ON_ERROR_STOP=1 \
    --command "COPY (SELECT to_jsonb(row_value)::text FROM (SELECT * FROM $table ORDER BY $order) AS row_value) TO STDOUT" \
    >> "$state_file"
}

query alembic_version version_num
query accepted_records identity_kind,identity_key
query projection_effects effect_kind,effect_key
query retention_expiry_markers resource_class,resource_kind,owner_key
sha256sum "$state_file" | cut -d ' ' -f 1
