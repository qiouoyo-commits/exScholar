"""Shared helpers for OpenClaw-facing local CLIs."""

from app.common import wait_for_job as common_wait_for_job
from app.site.core import load_openclaw_job


def wait_for_job(job_id: str, *, poll_interval: float = 2.0, timeout: float = 1800.0) -> dict:
    return common_wait_for_job(
        load_openclaw_job,
        job_id,
        poll_interval=poll_interval,
        timeout=timeout,
    )
