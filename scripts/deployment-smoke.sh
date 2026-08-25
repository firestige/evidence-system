#!/bin/sh
set -eu

project_name="wsr-evidence-deployment"
smoke_dir="$(mktemp -d)"
request_base64="CuECCi8KFQoMc2VydmljZS5uYW1lEgUKA2RzaAoWCg9zZXJ2aWNlLnZlcnNpb24SAwoBMRKtAgokChtpby5hZ2VudG9wcy5kc2gub2JzZXJ2YXRpb24SBTEuMC4wEtsBMh4KEWFnZW50b3BzLmV2ZW50LmlkEgkKB2V2ZW50LTEyLAoYYWdlbnRvcHMud29ya2Zsb3cuZmFtaWx5EhAKDmltcGxlbWVudGF0aW9uMiwKFmFnZW50b3BzLmZhbWlseS5zY2hlbWESEgoQaW1wbGVtZW50YXRpb25AMTIoChlhZ2VudG9wcy5kZWxpdmVyeS5vdXRjb21lEgsKCUNPTVBMRVRFRDIhChZhZ2VudG9wcy5zdW1tYXJ5LnN0YXRlEgcKBUZJTkFMYhBkZWxpdmVyeS5zdW1tYXJ5GidodHRwczovL29wZW50ZWxlbWV0cnkuaW8vc2NoZW1hcy8xLjQxLjA="

cleanup() {
  docker compose -p "$project_name" -f deployment/compose.yaml down --volumes
  rm -rf "$smoke_dir"
}
trap cleanup EXIT INT TERM

docker compose -p "$project_name" -f deployment/compose.yaml up --build --wait
curl --fail --silent --show-error http://127.0.0.1:4318/healthz
printf '%s' "$request_base64" | openssl base64 -d -A -out "$smoke_dir/request.pb"
curl --fail --silent --show-error \
  --header 'Content-Type: application/x-protobuf' \
  --data-binary "@$smoke_dir/request.pb" \
  --output "$smoke_dir/response.pb" \
  http://127.0.0.1:4318/v1/logs
accepted_count="$(
  docker compose -p "$project_name" -f deployment/compose.yaml exec -T database \
    psql -U wsr_evidence -d wsr_evidence -tAc 'SELECT count(*) FROM accepted_records'
)"
test "$accepted_count" = "1"
