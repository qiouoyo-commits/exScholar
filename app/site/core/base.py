"""Base imports, config, and shared runtime state for exScholar core modules."""

import csv
import io
import hashlib
import hmac
import json
import os
import re
import secrets
import sqlite3
import shutil
import subprocess
import threading
import time
import traceback
from contextlib import contextmanager
from datetime import datetime
from difflib import SequenceMatcher
from html import escape
from http import cookies
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import quote, unquote

import requests
from dotenv import load_dotenv

from app.openclaw.ingest import (
    DEFAULT_OPENCLAW_CHECK_MODEL,
    DEFAULT_OPENCLAW_CONFIG_PATH,
    DEFAULT_OPENCLAW_FALLBACK_MODEL,
    DEFAULT_OPENCLAW_MODEL,
    OpenClawIngestError,
    answer_question_from_text,
    compose_research_plan,
    extract_metadata_from_text,
    extract_pdf_bundle,
    refine_research_plan,
    review_research_results,
    suggest_research_queries,
    generate_analysis_from_text,
    plan_research_request,
    validate_research_plan,
)
from app.pipeline.search import build_json_records, build_search_summary_name, build_site_url, run_topic_search, write_csv, write_json, write_site

ROOT_DIR = Path(__file__).resolve().parents[3]
load_dotenv(ROOT_DIR / ".env.local")

HOST = os.getenv("SITE_SERVER_HOST", "0.0.0.0").strip() or "0.0.0.0"
PORT = int((os.getenv("PUBLIC_SITE_PORT", "38128")).strip() or "38128")
PUBLIC_SITE_BASE_URL = (os.getenv("PUBLIC_SITE_BASE_URL") or "").strip().rstrip("/")
ROOT_DATA_DIR = ROOT_DIR / "data"
USERS_DIR = ROOT_DATA_DIR / "users"
USERS_FILE = USERS_DIR / "users.json"
OPENCLAW_INGEST_MODEL = (os.getenv("OPENCLAW_INGEST_MODEL") or DEFAULT_OPENCLAW_MODEL).strip()
OPENCLAW_INGEST_CHECK_MODEL = (os.getenv("OPENCLAW_INGEST_CHECK_MODEL") or DEFAULT_OPENCLAW_CHECK_MODEL).strip()
OPENCLAW_INGEST_FALLBACK_MODEL = (os.getenv("OPENCLAW_INGEST_FALLBACK_MODEL") or DEFAULT_OPENCLAW_FALLBACK_MODEL).strip()
OPENCLAW_CONFIG_PATH = Path((os.getenv("OPENCLAW_CONFIG_PATH") or str(DEFAULT_OPENCLAW_CONFIG_PATH)).strip())

PASSWORD_SALT = (os.getenv("SITE_PASSWORD_SALT") or "").strip()
PASSWORD_HASH = (os.getenv("SITE_PASSWORD_HASH") or "").strip()
SESSION_SECRET = (os.getenv("SITE_SESSION_SECRET") or "").strip() or secrets.token_hex(32)
SESSION_COOKIE = "ccf_site_session"
SESSION_TTL_SECONDS = int((os.getenv("SITE_SESSION_TTL_SECONDS") or str(24 * 60 * 60)).strip() or str(24 * 60 * 60))
PBKDF2_ITERATIONS = 200_000
REFERENCE_LIMIT = int((os.getenv("REFERENCE_EXPAND_LIMIT") or "20").strip() or "20")
AI4SCHOLAR_API_KEY = (os.getenv("AI4SCHOLAR_API_KEY") or "").strip()
MAX_CONCURRENT_RESEARCH_JOBS = int((os.getenv("MAX_CONCURRENT_RESEARCH_JOBS") or "2").strip() or "2")
OPENCLAW_ANALYTICS_PYTHON = (
    os.getenv("OPENCLAW_ANALYTICS_PYTHON")
    or "/home/ubuntu/miniconda3/envs/openclaw-analytics/bin/python"
).strip()

SESSIONS: dict[str, dict] = {}
SESSION_LOCK = threading.Lock()
BATCH_READING_JOB: dict[str, object] = {"status": "idle", "running": False}
BATCH_READING_JOB_LOCK = threading.Lock()
OPENCLAW_JOB_LOCK = threading.Lock()
RESEARCH_JOB_LOCK = threading.Lock()
RESEARCH_JOB_SEMAPHORE = threading.Semaphore(MAX_CONCURRENT_RESEARCH_JOBS)
REQUEST_CONTEXT = threading.local()


class ReusableThreadingHTTPServer(ThreadingHTTPServer):
    allow_reuse_address = True


class DynamicPath(os.PathLike):
    def __init__(self, resolver):
        self._resolver = resolver

    def resolve_path(self) -> Path:
        return Path(self._resolver())

    def __fspath__(self) -> str:
        return os.fspath(self.resolve_path())

    def __str__(self) -> str:
        return str(self.resolve_path())

    def __repr__(self) -> str:
        return repr(self.resolve_path())

    def __truediv__(self, other):
        return self.resolve_path() / other

    def __rtruediv__(self, other):
        return Path(other) / self.resolve_path()

    def __getattr__(self, name):
        return getattr(self.resolve_path(), name)


def sanitize_username(username: str) -> str:
    value = (username or "").strip().lower()
    value = re.sub(r"[^a-z0-9_-]+", "-", value)
    value = re.sub(r"-{2,}", "-", value).strip("-_")
    return value[:48]


DEFAULT_OPENCLAW_USERNAME = sanitize_username(os.getenv("OPENCLAW_DEFAULT_USERNAME") or "Qioyo")


def current_username() -> str:
    return str(getattr(REQUEST_CONTEXT, "username", "") or "")


def openclaw_default_username() -> str:
    return DEFAULT_OPENCLAW_USERNAME or "qioyo"


def current_user_root() -> Path | None:
    username = sanitize_username(current_username())
    if not username:
        return None
    return USERS_DIR / username


@contextmanager
def user_context(username: str | None):
    previous = getattr(REQUEST_CONTEXT, "username", None)
    normalized = sanitize_username(username or "")
    if normalized:
        REQUEST_CONTEXT.username = normalized
    elif hasattr(REQUEST_CONTEXT, "username"):
        delattr(REQUEST_CONTEXT, "username")
    try:
        yield normalized
    finally:
        if previous:
            REQUEST_CONTEXT.username = previous
        elif hasattr(REQUEST_CONTEXT, "username"):
            delattr(REQUEST_CONTEXT, "username")


def current_data_dir() -> Path:
    return current_user_root() or ROOT_DATA_DIR


def current_searches_dir() -> Path:
    return current_data_dir() / "searches"


def current_expansions_dir() -> Path:
    return current_data_dir() / "expansions"


def current_library_dir() -> Path:
    return current_data_dir() / "library"


def current_reading_dir() -> Path:
    return current_data_dir() / "reading"


def current_db_path() -> Path:
    return current_data_dir() / "citation_library.sqlite3"


def current_openclaw_jobs_dir() -> Path:
    return current_data_dir() / "openclaw_jobs"


def current_research_jobs_dir() -> Path:
    return current_data_dir() / "research_jobs"


def current_reference_jobs_dir() -> Path:
    return current_data_dir() / "reference_jobs"


def ensure_user_data_dirs(username: str) -> Path:
    normalized = sanitize_username(username)
    if not normalized:
        raise ValueError("username 不合法")
    user_root = USERS_DIR / normalized
    for relative in (
        "",
        "searches",
        "expansions",
        "library",
        "reading",
        "openclaw_jobs",
        "research_jobs",
        "reference_jobs",
    ):
        (user_root / relative).mkdir(parents=True, exist_ok=True)
    return user_root


DATA_DIR = DynamicPath(current_data_dir)
SEARCHES_DIR = DynamicPath(current_searches_dir)
EXPANSIONS_DIR = DynamicPath(current_expansions_dir)
LIBRARY_DIR = DynamicPath(current_library_dir)
READING_DIR = DynamicPath(current_reading_dir)
DB_PATH = DynamicPath(current_db_path)
OPENCLAW_JOBS_DIR = DynamicPath(current_openclaw_jobs_dir)
RESEARCH_JOBS_DIR = DynamicPath(current_research_jobs_dir)
REFERENCE_JOBS_DIR = DynamicPath(current_reference_jobs_dir)


def _session_expiry_cutoff(now: float | None = None) -> float:
    return float(now if now is not None else time.time()) - max(SESSION_TTL_SECONDS, 60)


def prune_expired_sessions(now: float | None = None) -> int:
    cutoff = _session_expiry_cutoff(now)
    removed = 0
    with SESSION_LOCK:
        expired_tokens = [
            token
            for token, payload in SESSIONS.items()
            if float((payload or {}).get("created_at") or 0.0) < cutoff
        ]
        for token in expired_tokens:
            SESSIONS.pop(token, None)
            removed += 1
    return removed


def create_session(username: str) -> tuple[str, dict]:
    now = time.time()
    prune_expired_sessions(now)
    token = secrets.token_urlsafe(32)
    session = {"created_at": now, "username": sanitize_username(username)}
    with SESSION_LOCK:
        SESSIONS[token] = session
    return token, session


def get_session(token: str) -> dict | None:
    if not token:
        return None
    now = time.time()
    with SESSION_LOCK:
        session = SESSIONS.get(token)
        if not session:
            return None
        if float((session or {}).get("created_at") or 0.0) < _session_expiry_cutoff(now):
            SESSIONS.pop(token, None)
            return None
        return dict(session)


def delete_session(token: str) -> None:
    if not token:
        return
    with SESSION_LOCK:
        SESSIONS.pop(token, None)
