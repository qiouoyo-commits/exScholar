#!/home/ubuntu/miniconda3/envs/openclaw-analytics/bin/python
"""Compatibility wrapper for the legacy titlesearch CLI."""

from app.openclaw.textsearch_cli import main


if __name__ == "__main__":
    raise SystemExit(main())
