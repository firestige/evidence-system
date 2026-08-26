#!/bin/sh
set -eu

project_name="wsr-evidence-deployment"
smoke_dir="$(mktemp -d)"
compose_file="deployment/compose.yaml"
restore_container=""
request_base64="CuECCi8KFQoMc2VydmljZS5uYW1lEgUKA2RzaAoWCg9zZXJ2aWNlLnZlcnNpb24SAwoBMRKtAgokChtpby5hZ2VudG9wcy5kc2gub2JzZXJ2YXRpb24SBTEuMC4wEtsBMh4KEWFnZW50b3BzLmV2ZW50LmlkEgkKB2V2ZW50LTEyLAoYYWdlbnRvcHMud29ya2Zsb3cuZmFtaWx5EhAKDmltcGxlbWVudGF0aW9uMiwKFmFnZW50b3BzLmZhbWlseS5zY2hlbWESEgoQaW1wbGVtZW50YXRpb25AMTIoChlhZ2VudG9wcy5kZWxpdmVyeS5vdXRjb21lEgsKCUNPTVBMRVRFRDIhChZhZ2VudG9wcy5zdW1tYXJ5LnN0YXRlEgcKBUZJTkFMYhBkZWxpdmVyeS5zdW1tYXJ5GidodHRwczovL29wZW50ZWxlbWV0cnkuaW8vc2NoZW1hcy8xLjQxLjA="

printf '%s\n' "smoke-admin-$(openssl rand -hex 16)" > "$smoke_dir/admin-password"
printf '%s\n' "smoke-runtime-$(openssl rand -hex 16)" > "$smoke_dir/runtime-password"
printf '%s\n' "smoke-backup-$(openssl rand -hex 16)" > "$smoke_dir/backup-password"
chmod 600 "$smoke_dir"/*-password
export WSR_EVIDENCE_ADMIN_PASSWORD_FILE="$smoke_dir/admin-password"
export WSR_EVIDENCE_RUNTIME_PASSWORD_FILE="$smoke_dir/runtime-password"
export WSR_EVIDENCE_BACKUP_PASSWORD_FILE="$smoke_dir/backup-password"
export WSR_EVIDENCE_RETENTION_INTERVAL_SECONDS=10

cleanup() {
  if test -n "$restore_container"; then
    docker rm --force "$restore_container" >/dev/null 2>&1 || true
  fi
  docker compose -p "$project_name" -f "$compose_file" --profile operations down --volumes
  rm -rf "$smoke_dir"
}
trap cleanup EXIT INT TERM

docker compose -p "$project_name" -f "$compose_file" up --build --wait

api_binding="$(docker compose -p "$project_name" -f "$compose_file" port evidence 4318)"
test "$api_binding" = "127.0.0.1:4318"
database_container="$(docker compose -p "$project_name" -f "$compose_file" ps --quiet database)"
database_binding="$(docker inspect --format '{{json .HostConfig.PortBindings}}' "$database_container")"
test "$database_binding" = "{}" || test "$database_binding" = "null"
services="$(docker compose -p "$project_name" -f "$compose_file" config --services)"
test "$services" = "database
migrate
evidence"

curl --fail --silent --show-error http://127.0.0.1:4318/healthz
printf '%s' "$request_base64" | openssl base64 -d -A -out "$smoke_dir/request.pb"
curl --fail --silent --show-error \
  --header 'Content-Type: application/x-protobuf' \
  --data-binary "@$smoke_dir/request.pb" \
  --output "$smoke_dir/response.pb" \
  http://127.0.0.1:4318/v1/logs

accepted_count="$(
  docker compose -p "$project_name" -f "$compose_file" exec -T database sh -eu -c \
    'PGPASSWORD="$(cat /run/secrets/runtime_password)" psql -h 127.0.0.1 -U wsr_evidence_runtime -d wsr_evidence -tAc "SELECT count(*) FROM accepted_records"'
)"
test "$accepted_count" = "1"

role_state="$(
  docker compose -p "$project_name" -f "$compose_file" exec -T database \
    psql -U wsr_evidence_admin -d wsr_evidence -tAc \
    "SELECT rolname || ':' || rolsuper || ':' || rolcreatedb || ':' || rolcreaterole FROM pg_roles WHERE rolname IN ('wsr_evidence_runtime','wsr_evidence_backup') ORDER BY rolname"
)"
test "$role_state" = "wsr_evidence_backup:false:false:false
wsr_evidence_runtime:false:false:false"
backup_read_only="$(
  docker compose -p "$project_name" -f "$compose_file" exec -T database sh -eu -c \
    'PGPASSWORD="$(cat /run/secrets/backup_password)" psql -h 127.0.0.1 -U wsr_evidence_backup -d wsr_evidence -tAc "SHOW default_transaction_read_only"'
)"
test "$backup_read_only" = "on"
if docker compose -p "$project_name" -f "$compose_file" exec -T database sh -eu -c \
  'PGPASSWORD="$(cat /run/secrets/backup_password)" psql -h 127.0.0.1 -U wsr_evidence_backup -d wsr_evidence -v ON_ERROR_STOP=1 -c "CREATE TABLE forbidden_backup_write(id integer)"' \
  >"$smoke_dir/backup-write.stdout" 2>"$smoke_dir/backup-write.stderr"; then
  echo "backup role unexpectedly wrote to the database" >&2
  exit 1
fi

attempt=0
raw_marker_count=0
while test "$attempt" -lt 20; do
  raw_marker_count="$(
    docker compose -p "$project_name" -f "$compose_file" exec -T database sh -eu -c \
      'PGPASSWORD="$(cat /run/secrets/runtime_password)" psql -h 127.0.0.1 -U wsr_evidence_runtime -d wsr_evidence -tAc "SELECT count(*) FROM retention_expiry_markers WHERE resource_class = '\''RAW_DEBUG'\''"'
  )"
  test "$raw_marker_count" = "1" && break
  attempt=$((attempt + 1))
  sleep 1
done
test "$raw_marker_count" = "1"

curl --fail --silent --show-error \
  --output "$smoke_dir/original-facts.json" \
  'http://127.0.0.1:4318/v1/evidence/facts?event_name=delivery.summary'
uv run --python 3.14 python -c 'import json,sys; value=json.load(open(sys.argv[1])); assert value["contract"] == {"name":"evidence.query","revision":"0.1.0"}; assert value["items"]; assert all(item["truth"]["expiry"] == "ACTIVE" for item in value["items"])' "$smoke_dir/original-facts.json"

docker compose -p "$project_name" -f "$compose_file" --profile operations run --rm \
  -e WSR_EVIDENCE_BACKUP_FILE=wave10.backup backup > "$smoke_dir/backup.out"
docker compose -p "$project_name" -f "$compose_file" --profile operations run --rm \
  -e WSR_EVIDENCE_BACKUP_FILE=wave10.backup \
  -e WSR_EVIDENCE_RESTORE_DATABASE=wsr_evidence_restore_wave10 restore

original_digest="$(
  docker compose -p "$project_name" -f "$compose_file" --profile operations run --rm \
    --entrypoint /opt/wsr/state-digest.sh backup
)"
restored_digest="$(
  docker compose -p "$project_name" -f "$compose_file" --profile operations run --rm \
    --entrypoint /opt/wsr/state-digest.sh -e WSR_EVIDENCE_DATABASE_NAME=wsr_evidence_restore_wave10 backup
)"
test "$original_digest" = "$restored_digest"

docker compose -p "$project_name" -f "$compose_file" stop evidence
restore_container="$(
  docker compose -p "$project_name" -f "$compose_file" run --detach --rm --service-ports --no-deps \
    -e WSR_EVIDENCE_DATABASE_NAME=wsr_evidence_restore_wave10 evidence
)"
attempt=0
until curl --fail --silent --show-error http://127.0.0.1:4318/healthz >/dev/null 2>&1; do
  attempt=$((attempt + 1))
  test "$attempt" -lt 20
  sleep 1
done
curl --fail --silent --show-error \
  --output "$smoke_dir/restored-facts.json" \
  'http://127.0.0.1:4318/v1/evidence/facts?event_name=delivery.summary'
uv run --python 3.14 python -c 'import json,sys; left=json.load(open(sys.argv[1])); right=json.load(open(sys.argv[2])); left.pop("snapshot"); right.pop("snapshot"); assert left == right' "$smoke_dir/original-facts.json" "$smoke_dir/restored-facts.json"

printf 'PASS: loopback=%s postgres_host_port=absent roles=least-privilege raw_marker=%s state_sha256=%s restored_query=identical\n' \
  "$api_binding" "$raw_marker_count" "$original_digest"
