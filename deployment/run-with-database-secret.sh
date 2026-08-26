#!/bin/sh
set -eu

secret_file="${WSR_EVIDENCE_DATABASE_PASSWORD_FILE:?database password file is required}"
database_host="${WSR_EVIDENCE_DATABASE_HOST:?database host is required}"
database_name="${WSR_EVIDENCE_DATABASE_NAME:?database name is required}"
database_user="${WSR_EVIDENCE_DATABASE_USER:?database user is required}"

test -r "$secret_file"
password="$(tr -d '\r\n' < "$secret_file")"
test -n "$password"
encoded_password="$(printf '%s' "$password" | python -c 'import sys; from urllib.parse import quote; print(quote(sys.stdin.read(), safe=""))')"
export WSR_EVIDENCE_DATABASE_URL="postgresql://${database_user}:${encoded_password}@${database_host}/${database_name}"

exec "$@"
