#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="/home/ubuntu/tools/exScholar"
DEFAULT_USER="${OPENCLAW_DEFAULT_USERNAME:-Qioyo}"
DEFAULT_USER="$(printf '%s' "$DEFAULT_USER" | tr '[:upper:]' '[:lower:]')"
OPENCLAW_PYTHON="/home/ubuntu/miniconda3/envs/openclaw-analytics/bin/python"

export EXSCHOLAR_DATA_DIR="${EXSCHOLAR_DATA_DIR:-$ROOT_DIR/data/users/$DEFAULT_USER}"
export EXSCHOLAR_SEARCHES_DIR="${EXSCHOLAR_SEARCHES_DIR:-$EXSCHOLAR_DATA_DIR/searches}"
export EXSCHOLAR_TMP_SEARCH_DIR="${EXSCHOLAR_TMP_SEARCH_DIR:-$EXSCHOLAR_DATA_DIR/tmp_search}"
export OPENCLAW_ANALYTICS_PYTHON="${OPENCLAW_ANALYTICS_PYTHON:-$OPENCLAW_PYTHON}"

if ! command -v oc-conda-run >/dev/null 2>&1; then
  echo "error: oc-conda-run not found. Please install/configure OpenClaw conda runner first." >&2
  exit 1
fi

exec oc-conda-run -- "$OPENCLAW_PYTHON" -m app.pipeline.search "$@"
