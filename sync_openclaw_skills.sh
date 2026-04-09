#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SRC_DIR="$ROOT_DIR/skills"
DEST_DIR="${HOME}/.openclaw/skills"

mkdir -p "$DEST_DIR"

if ! command -v rsync >/dev/null 2>&1; then
  echo "rsync is required but not installed." >&2
  exit 1
fi

rsync -a --delete \
  --exclude 'README.md' \
  "$SRC_DIR"/ \
  "$DEST_DIR"/

echo "Synced skills from $SRC_DIR to $DEST_DIR"
