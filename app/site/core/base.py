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
import threading
import time
import traceback
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
    generate_analysis_from_text,
    plan_research_request,
    validate_research_plan,
)
from app.pipeline.search import build_json_records, build_site_url, run_topic_search, write_csv, write_json, write_site

ROOT_DIR = Path(__file__).resolve().parents[3]
load_dotenv(ROOT_DIR / ".env.local")

HOST = os.getenv("SITE_SERVER_HOST", "0.0.0.0").strip() or "0.0.0.0"
PORT = int((os.getenv("PUBLIC_SITE_PORT", "38128")).strip() or "38128")
PUBLIC_SITE_BASE_URL = (os.getenv("PUBLIC_SITE_BASE_URL") or "").strip().rstrip("/")
DATA_DIR = ROOT_DIR / "data"
SEARCHES_DIR = DATA_DIR / "searches"
EXPANSIONS_DIR = DATA_DIR / "expansions"
LIBRARY_DIR = DATA_DIR / "library"
READING_DIR = DATA_DIR / "reading"
DB_PATH = ROOT_DIR / "data" / "citation_library.sqlite3"
OPENCLAW_JOBS_DIR = DATA_DIR / "openclaw_jobs"
RESEARCH_JOBS_DIR = DATA_DIR / "research_jobs"
OPENCLAW_INGEST_MODEL = (os.getenv("OPENCLAW_INGEST_MODEL") or DEFAULT_OPENCLAW_MODEL).strip()
OPENCLAW_INGEST_CHECK_MODEL = (os.getenv("OPENCLAW_INGEST_CHECK_MODEL") or DEFAULT_OPENCLAW_CHECK_MODEL).strip()
OPENCLAW_INGEST_FALLBACK_MODEL = (os.getenv("OPENCLAW_INGEST_FALLBACK_MODEL") or DEFAULT_OPENCLAW_FALLBACK_MODEL).strip()
OPENCLAW_CONFIG_PATH = Path((os.getenv("OPENCLAW_CONFIG_PATH") or str(DEFAULT_OPENCLAW_CONFIG_PATH)).strip())

PASSWORD_SALT = (os.getenv("SITE_PASSWORD_SALT") or "").strip()
PASSWORD_HASH = (os.getenv("SITE_PASSWORD_HASH") or "").strip()
SESSION_SECRET = (os.getenv("SITE_SESSION_SECRET") or "").strip() or secrets.token_hex(32)
SESSION_COOKIE = "ccf_site_session"
PBKDF2_ITERATIONS = 200_000
REFERENCE_LIMIT = int((os.getenv("REFERENCE_EXPAND_LIMIT") or "20").strip() or "20")
AI4SCHOLAR_API_KEY = (os.getenv("AI4SCHOLAR_API_KEY") or "").strip()
MAX_CONCURRENT_RESEARCH_JOBS = int((os.getenv("MAX_CONCURRENT_RESEARCH_JOBS") or "2").strip() or "2")

SESSIONS: dict[str, dict] = {}
BATCH_READING_JOB: dict[str, object] = {"status": "idle", "running": False}
BATCH_READING_JOB_LOCK = threading.Lock()
OPENCLAW_JOB_LOCK = threading.Lock()
RESEARCH_JOB_LOCK = threading.Lock()
RESEARCH_JOB_SEMAPHORE = threading.Semaphore(MAX_CONCURRENT_RESEARCH_JOBS)


class ReusableThreadingHTTPServer(ThreadingHTTPServer):
    allow_reuse_address = True
