#!/usr/bin/env bash
set -euo pipefail

if ! command -v oc-conda-run >/dev/null 2>&1; then
  echo "error: oc-conda-run not found. Please install/configure OpenClaw conda runner first." >&2
  exit 1
fi

exec oc-conda-run -- python -m app.pipeline.search "$@"
