#!/bin/sh
set -eu

project_name="wsr-evidence-deployment"

cleanup() {
  docker compose -p "$project_name" -f deployment/compose.yaml down --volumes
}
trap cleanup EXIT INT TERM

docker compose -p "$project_name" -f deployment/compose.yaml up --build --wait
curl --fail --silent --show-error http://127.0.0.1:4318/healthz
