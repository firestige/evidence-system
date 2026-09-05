#!/bin/sh
set -eu

event=${GITHUB_EVENT_NAME:?GITHUB_EVENT_NAME is required}
ref=${GITHUB_REF_NAME:?GITHUB_REF_NAME is required}

case "$event:$ref" in
  push:release/next)
    exit 0
    ;;
  *)
    printf 'Evidence release candidate rejects event=%s ref=%s.\n' "$event" "$ref" >&2
    exit 1
    ;;
esac
