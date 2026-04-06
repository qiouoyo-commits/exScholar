#!/usr/bin/env python3
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
import threading
from dotenv import load_dotenv

from search import build_json_records, build_site_url, write_csv, write_json, write_site

ROOT_DIR = Path(__file__).resolve().parent
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

PASSWORD_SALT = (os.getenv("SITE_PASSWORD_SALT") or "").strip()
PASSWORD_HASH = (os.getenv("SITE_PASSWORD_HASH") or "").strip()
SESSION_SECRET = (os.getenv("SITE_SESSION_SECRET") or "").strip() or secrets.token_hex(32)
SESSION_COOKIE = "ccf_site_session"
PBKDF2_ITERATIONS = 200_000
REFERENCE_LIMIT = int((os.getenv("REFERENCE_EXPAND_LIMIT") or "20").strip() or "20")
AI4SCHOLAR_API_KEY = (os.getenv("AI4SCHOLAR_API_KEY") or "").strip()
MOONSHOT_API_KEY = (os.getenv("MOONSHOT_API_KEY") or "").strip()
MOONSHOT_BASE_URL = (os.getenv("MOONSHOT_BASE_URL") or "https://api.moonshot.cn/v1").strip().rstrip("/")
MOONSHOT_ANALYSIS_MODEL = (os.getenv("MOONSHOT_ANALYSIS_MODEL") or "kimi-k2-turbo-preview").strip()

SESSIONS: dict[str, dict] = {}
ANALYSIS_JOBS: dict[str, dict] = {}
ANALYSIS_JOBS_LOCK = threading.Lock()


class ReusableThreadingHTTPServer(ThreadingHTTPServer):
    allow_reuse_address = True


def hash_password(password: str, salt: str) -> str:
    return hashlib.pbkdf2_hmac(
        "sha256", password.encode("utf-8"), salt.encode("utf-8"), PBKDF2_ITERATIONS
    ).hex()


def verify_password(password: str) -> bool:
    if not PASSWORD_SALT or not PASSWORD_HASH:
        return False
    candidate = hash_password(password, PASSWORD_SALT)
    return hmac.compare_digest(candidate, PASSWORD_HASH)


def require_password() -> bool:
    return bool(PASSWORD_SALT and PASSWORD_HASH)


def ensure_db():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    LIBRARY_DIR.mkdir(parents=True, exist_ok=True)
    READING_DIR.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS citations (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                title TEXT NOT NULL,
                doi TEXT,
                url TEXT,
                authors TEXT,
                year TEXT,
                venue TEXT,
                abstract TEXT,
                matched_kw TEXT,
                tags TEXT DEFAULT '',
                source_search_slug TEXT,
                source_csv_index INTEGER,
                pdf_path TEXT,
                pdf_sha256 TEXT,
                reading_paper_id TEXT,
                created_at TEXT NOT NULL,
                UNIQUE(doi),
                UNIQUE(title, year)
            )
            """
        )
        columns = {
            row[1]
            for row in conn.execute("PRAGMA table_info(citations)").fetchall()
        }
        if "tags" not in columns:
            conn.execute("ALTER TABLE citations ADD COLUMN tags TEXT DEFAULT ''")
        if "pdf_path" not in columns:
            conn.execute("ALTER TABLE citations ADD COLUMN pdf_path TEXT")
        if "pdf_sha256" not in columns:
            conn.execute("ALTER TABLE citations ADD COLUMN pdf_sha256 TEXT")
        if "reading_paper_id" not in columns:
            conn.execute("ALTER TABLE citations ADD COLUMN reading_paper_id TEXT")
        conn.execute(
            """
            UPDATE citations
            SET tags = matched_kw
            WHERE (tags IS NULL OR tags = '')
              AND matched_kw IS NOT NULL
              AND matched_kw != ''
            """
        )
        rows = conn.execute(
            """
            SELECT id, pdf_path
            FROM citations
            WHERE pdf_path IS NOT NULL
              AND pdf_path != ''
              AND (pdf_sha256 IS NULL OR pdf_sha256 = '')
            """
        ).fetchall()
        for citation_id, pdf_path in rows:
            pdf_abs = DATA_DIR / pdf_path
            if not pdf_abs.exists():
                continue
            conn.execute(
                "UPDATE citations SET pdf_sha256 = ? WHERE id = ?",
                (compute_file_sha256(pdf_abs), citation_id),
            )
        # Create reading_groups table
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS reading_groups (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL UNIQUE,
                description TEXT,
                created_at TEXT NOT NULL
            )
            """
        )
        # Create citation_group_links table
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS citation_group_links (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                citation_id INTEGER NOT NULL,
                group_id INTEGER NOT NULL,
                created_at TEXT NOT NULL,
                UNIQUE(citation_id, group_id),
                FOREIGN KEY (citation_id) REFERENCES citations(id) ON DELETE CASCADE,
                FOREIGN KEY (group_id) REFERENCES reading_groups(id) ON DELETE CASCADE
            )
            """
        )
        conn.commit()


def iter_result_dirs():
    for base_dir in (SEARCHES_DIR, EXPANSIONS_DIR):
        if not base_dir.exists():
            continue
        for out_dir in sorted(base_dir.iterdir(), reverse=True):
            if out_dir.is_dir():
                yield out_dir


def iter_search_dirs():
    if not SEARCHES_DIR.exists():
        return
    for out_dir in sorted(SEARCHES_DIR.iterdir(), reverse=True):
        if out_dir.is_dir():
            yield out_dir


def find_result_dir_by_slug(slug: str) -> Path | None:
    slug = (slug or "").strip()
    if not slug:
        return None
    for out_dir in iter_result_dirs():
        search_json = out_dir / "search.json"
        if not search_json.exists():
            continue
        try:
            meta = json.loads(search_json.read_text(encoding="utf-8"))
        except Exception:
            continue
        if (meta.get("slug") or out_dir.name.split("_", 1)[-1]) == slug:
            return out_dir
        if out_dir.name == slug:
            return out_dir
    return None


def list_keyword_entries() -> list[dict]:
    grouped: dict[str, dict] = {}
    for out_dir in iter_search_dirs():
        search_json = out_dir / "search.json"
        papers_json = out_dir / "papers.json"
        site_index = out_dir / "site" / "index.html"
        if not papers_json.exists():
            continue
        try:
            meta = json.loads(search_json.read_text(encoding="utf-8")) if search_json.exists() else {}
            payload = json.loads(papers_json.read_text(encoding="utf-8"))
        except Exception:
            continue
        date_str = meta.get("date") or out_dir.name.split("_", 1)[0]
        search_title = meta.get("slug") or out_dir.name.split("_", 1)[-1]
        site_url = build_site_url(str(out_dir), str(site_index)) if site_index.exists() else f"/{out_dir.relative_to(DATA_DIR).as_posix()}/"
        for paper in payload.get("papers", []):
            keyword = (paper.get("matched_kw") or "").strip()
            if not keyword:
                continue
            key = keyword.lower()
            entry = grouped.setdefault(
                key,
                {
                    "keyword": keyword,
                    "count": 0,
                    "papers": [],
                    "latest_date": "",
                },
            )
            entry["count"] += 1
            if date_str and date_str > (entry.get("latest_date") or ""):
                entry["latest_date"] = date_str
            entry["papers"].append(
                {
                    "title": paper.get("title") or "",
                    "content": paper.get("content") or "",
                    "matched_kw": paper.get("matched_kw") or "",
                    "venue": paper.get("venue") or "",
                    "year": paper.get("year") or "",
                    "authors": paper.get("authors") or "",
                    "doi": paper.get("doi") or "",
                    "url": paper.get("url") or "",
                    "paper_id": paper.get("paper_id") or "",
                    "csv_index": paper.get("csv_index"),
                    "source_date": date_str,
                    "source_slug": search_title,
                    "source_site_url": site_url,
                    "source_relative_dir": out_dir.relative_to(DATA_DIR).as_posix(),
                    "source_kind": "search",
                }
            )
    for citation in list_citations():
        tags = [part.strip() for part in (citation.get("tags") or "").split(",") if part.strip()]
        for keyword in tags:
            key = keyword.lower()
            entry = grouped.setdefault(
                key,
                {
                    "keyword": keyword,
                    "count": 0,
                    "papers": [],
                    "latest_date": "",
                },
            )
            created_at = (citation.get("created_at") or "")[:10]
            entry["count"] += 1
            if created_at and created_at > (entry.get("latest_date") or ""):
                entry["latest_date"] = created_at
            entry["papers"].append(
                {
                    "title": citation.get("title") or "",
                    "content": citation.get("abstract") or "",
                    "matched_kw": keyword,
                    "venue": citation.get("venue") or "",
                    "year": citation.get("year") or "",
                    "authors": citation.get("authors") or "",
                    "doi": citation.get("doi") or "",
                    "url": citation.get("url") or "",
                    "paper_id": citation.get("reading_paper_id") or "",
                    "csv_index": None,
                    "source_date": created_at,
                    "source_slug": "deep-reading",
                    "source_site_url": f"/reading/{citation.get('reading_paper_id')}" if citation.get("reading_paper_id") else "/reading",
                    "source_relative_dir": "",
                    "source_kind": "deep_reading",
                }
            )
    entries = sorted(grouped.values(), key=lambda item: ((item.get("latest_date") or ""), item["keyword"].lower()), reverse=True)
    for entry in entries:
        entry["slug"] = quote(entry["keyword"], safe="")
    return entries


def get_keyword_entry(keyword: str) -> dict | None:
    target = (keyword or "").strip().lower()
    if not target:
        return None
    for entry in list_keyword_entries():
        if entry["keyword"].lower() == target:
            return entry
    return None


def get_source_matched_kw(meta: dict) -> str:
    source_paper = meta.get("source_paper") or {}
    matched_kw = (source_paper.get("matched_kw") or "").strip()
    if matched_kw:
        return matched_kw
    source_slug = (source_paper.get("source_slug") or "").strip()
    source_csv_index = source_paper.get("source_csv_index")
    if not source_slug or not source_csv_index:
        return ""
    source_dir = find_result_dir_by_slug(source_slug)
    if not source_dir:
        return ""
    papers_json = source_dir / "papers.json"
    if not papers_json.exists():
        return ""
    try:
        payload = json.loads(papers_json.read_text(encoding="utf-8"))
    except Exception:
        return ""
    for paper in payload.get("papers", []):
        if paper.get("csv_index") == source_csv_index:
            return (paper.get("matched_kw") or "").strip()
    return ""


def upsert_citation(paper: dict, search_slug: str, pdf_path: str = None, pdf_sha256: str = None):
    ensure_db()
    now = datetime.utcnow().isoformat(timespec="seconds") + "Z"
    default_tags = (
        ", ".join(paper.get("tags", []))
        if isinstance(paper.get("tags"), list)
        else (paper.get("tags") or paper.get("matched_kw") or "")
    )
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute(
            """
            INSERT INTO citations (
                title, doi, url, authors, year, venue, abstract, matched_kw, tags,
                source_search_slug, source_csv_index, pdf_path, pdf_sha256, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(doi) DO UPDATE SET
                url=excluded.url,
                authors=excluded.authors,
                year=excluded.year,
                venue=excluded.venue,
                abstract=excluded.abstract,
                matched_kw=excluded.matched_kw,
                tags=COALESCE(NULLIF(citations.tags, ''), excluded.tags),
                source_search_slug=excluded.source_search_slug,
                source_csv_index=excluded.source_csv_index,
                pdf_path=COALESCE(excluded.pdf_path, citations.pdf_path),
                pdf_sha256=COALESCE(excluded.pdf_sha256, citations.pdf_sha256)
            """,
            (
                paper.get("title", ""),
                paper.get("doi") or None,
                paper.get("url") or None,
                paper.get("authors", ""),
                str(paper.get("year", "") or ""),
                paper.get("venue", ""),
                paper.get("content", ""),
                paper.get("matched_kw", ""),
                normalize_tags(default_tags),
                search_slug,
                paper.get("csv_index"),
                pdf_path,
                pdf_sha256,
                now,
            ),
        )
        if not (paper.get("doi") or "").strip():
            conn.execute(
                """
                INSERT INTO citations (
                    title, doi, url, authors, year, venue, abstract, matched_kw, tags,
                    source_search_slug, source_csv_index, pdf_path, pdf_sha256, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(title, year) DO UPDATE SET
                    url=excluded.url,
                    authors=excluded.authors,
                    venue=excluded.venue,
                    abstract=excluded.abstract,
                    matched_kw=excluded.matched_kw,
                    tags=COALESCE(NULLIF(citations.tags, ''), excluded.tags),
                    source_search_slug=excluded.source_search_slug,
                    source_csv_index=excluded.source_csv_index,
                    pdf_path=COALESCE(excluded.pdf_path, citations.pdf_path),
                    pdf_sha256=COALESCE(excluded.pdf_sha256, citations.pdf_sha256)
                """,
                (
                    paper.get("title", ""),
                    None,
                    paper.get("url") or None,
                    paper.get("authors", ""),
                    str(paper.get("year", "") or ""),
                    paper.get("venue", ""),
                    paper.get("content", ""),
                    paper.get("matched_kw", ""),
                    normalize_tags(default_tags),
                    search_slug,
                    paper.get("csv_index"),
                    pdf_path,
                    pdf_sha256,
                    now,
                ),
            )
        citation_id = None
        doi = (paper.get("doi") or "").strip()
        if doi:
            row = conn.execute("SELECT id FROM citations WHERE doi = ?", (doi,)).fetchone()
            citation_id = row[0] if row else None
        if not citation_id:
            row = conn.execute(
                "SELECT id FROM citations WHERE title = ? AND year = ?",
                (paper.get("title", ""), str(paper.get("year", "") or "")),
            ).fetchone()
            citation_id = row[0] if row else None
        conn.commit()
        return citation_id


def list_citations():
    ensure_db()
    with sqlite3.connect(DB_PATH) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            """
            SELECT id, title, doi, url, authors, year, venue, abstract,
                   matched_kw, tags, source_search_slug, source_csv_index, pdf_path, pdf_sha256, reading_paper_id, created_at
            FROM citations
            ORDER BY created_at DESC, id DESC
            """
        ).fetchall()
    return [dict(row) for row in rows]


def get_citations_by_ids(ids: list[int]):
    ensure_db()
    if not ids:
        return []
    placeholders = ",".join("?" for _ in ids)
    with sqlite3.connect(DB_PATH) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            f"""
            SELECT id, title, doi, url, authors, year, venue, abstract,
                   matched_kw, tags, source_search_slug, source_csv_index, pdf_path, pdf_sha256, reading_paper_id, created_at
            FROM citations
            WHERE id IN ({placeholders})
            ORDER BY created_at DESC, id DESC
            """,
            ids,
        ).fetchall()
    return [dict(row) for row in rows]


def get_citation_by_id(citation_id: int):
    ensure_db()
    with sqlite3.connect(DB_PATH) as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            """
            SELECT id, title, doi, url, authors, year, venue, abstract,
                   matched_kw, tags, source_search_slug, source_csv_index, pdf_path, pdf_sha256, reading_paper_id, created_at
            FROM citations
            WHERE id = ?
            """,
            (citation_id,),
        ).fetchone()
    return dict(row) if row else None


def get_citation_by_reading_paper_id(paper_id: str):
    ensure_db()
    with sqlite3.connect(DB_PATH) as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            """
            SELECT id, title, doi, url, authors, year, venue, abstract,
                   matched_kw, tags, source_search_slug, source_csv_index, pdf_path, pdf_sha256, reading_paper_id, created_at
            FROM citations
            WHERE reading_paper_id = ?
            """,
            (paper_id,),
        ).fetchone()
    return dict(row) if row else None


def normalize_tags(raw) -> str:
    if isinstance(raw, list):
        parts = raw
    else:
        text = (raw or "").replace(";", ",")
        parts = text.split(",")
    cleaned = []
    seen = set()
    for part in parts:
        tag = " ".join(str(part).strip().split())
        low = tag.lower()
        if not tag or low in seen:
            continue
        seen.add(low)
        cleaned.append(tag)
    return ", ".join(cleaned)


def normalize_doi(raw: str) -> str:
    value = (raw or "").strip().lower()
    for prefix in ("https://doi.org/", "http://doi.org/", "doi:"):
        if value.startswith(prefix):
            value = value[len(prefix):]
    return value.strip()


def update_citation_tags(citation_id: int, tags: str):
    ensure_db()
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute(
            "UPDATE citations SET tags = ? WHERE id = ?",
            (normalize_tags(tags), citation_id),
        )
        conn.commit()


def extract_theme_keywords(theme_text: str) -> list[str]:
    text = " ".join((theme_text or "").strip().split())
    if not text:
        return []
    parts = re.split(r"[;,/|，；、]+", text)
    keywords = []
    for part in parts:
        value = " ".join(part.strip().split())
        if not value:
            continue
        keywords.append(value)
    if not keywords and text:
        keywords.append(text)
    return keywords


def merge_analysis_theme_into_citation(paper_id: str, analysis_payload: dict):
    citation = get_citation_by_reading_paper_id(paper_id)
    if not citation:
        return
    overview = (((analysis_payload or {}).get("modules") or {}).get("overview") or {}).get("data") or {}
    theme_words = extract_theme_keywords(overview.get("research_theme") or "")
    if not theme_words:
        return
    merged_tags = normalize_tags(
        [part.strip() for part in (citation.get("tags") or "").split(",") if part.strip()] + theme_words
    )
    update_citation_tags(int(citation["id"]), merged_tags)
    refreshed = get_citation_by_id(int(citation["id"])) or citation
    workspace = reading_workspace_path(paper_id)
    paper_json_path = workspace / "paper.json"
    paper = read_json_file(paper_json_path, {})
    if paper:
        paper["keywords"] = [part.strip() for part in (refreshed.get("tags") or "").split(",") if part.strip()]
        paper["updated_at"] = utc_now()
        write_json_file(paper_json_path, paper)


def load_reading_full_text(paper_id: str) -> str:
    source_path = reading_workspace_path(paper_id) / "source" / "full_text.json"
    payload = read_json_file(source_path, {})
    text = (payload.get("text") or "").strip()
    if text:
        return text
    content = payload.get("content") or {}
    if isinstance(content, dict):
        normalized = normalize_full_text_payload(content)
        return normalized.get("text") or ""
    return ""


def build_question_answer_prompt(paper: dict, analysis: dict, extracted_text: str, question: str) -> str:
    overview = (((analysis or {}).get("modules") or {}).get("overview") or {}).get("data") or {}
    problem = (((analysis or {}).get("modules") or {}).get("problem") or {}).get("data") or {}
    method = (((analysis or {}).get("modules") or {}).get("method") or {}).get("data") or {}
    results = (((analysis or {}).get("modules") or {}).get("results") or {}).get("data") or {}
    critique = (((analysis or {}).get("modules") or {}).get("critique") or {}).get("data") or {}
    sample = (extracted_text or "")[:80000]
    return f"""
你是一个论文深度阅读助手。请基于论文内容回答用户问题。

规则：
- 全部回答使用中文。
- 优先依据论文原文，其次参考已有结构化分析。
- 如果论文中没有足够证据，请明确说“文中未明确说明”。
- 不要编造实验结果、数字或结论。
- 回答尽量具体、清楚，必要时分点。

论文标题：{paper.get("title") or ""}
作者：{", ".join(paper.get("authors") or [])}
venue：{paper.get("venue") or ""}
年份：{paper.get("year") or ""}
DOI：{paper.get("doi") or ""}

已有分析摘要：
overview: {json.dumps(overview, ensure_ascii=False)}
problem: {json.dumps(problem, ensure_ascii=False)}
method: {json.dumps(method, ensure_ascii=False)}
results: {json.dumps(results, ensure_ascii=False)}
critique: {json.dumps(critique, ensure_ascii=False)}

论文原文摘录：
{sample}

用户问题：
{question}
""".strip()


def moonshot_answer_question(paper: dict, analysis: dict, extracted_text: str, question: str) -> str:
    session = moonshot_session()
    payload = {
        "model": MOONSHOT_ANALYSIS_MODEL,
        "messages": [
            {"role": "system", "content": "你是一个严谨的论文问答助手。只根据给定论文材料回答，输出中文。"},
            {"role": "user", "content": build_question_answer_prompt(paper, analysis, extracted_text, question)},
        ],
        "temperature": moonshot_temperature(),
    }
    resp = session.post(
        f"{MOONSHOT_BASE_URL}/chat/completions",
        headers={**moonshot_headers(), "Content-Type": "application/json"},
        json=payload,
        timeout=180,
    )
    raise_for_moonshot(resp)
    data = resp.json()
    content = (((data.get("choices") or [{}])[0].get("message") or {}).get("content") or "").strip()
    if not content:
        raise ValueError("模型没有返回问答内容")
    return content


def append_reading_question_history(paper_id: str, question: str, answer: str) -> dict:
    path = reading_qa_history_path(paper_id)
    history = read_json_file(path, [])
    if not isinstance(history, list):
        history = []
    item = {
        "id": f"qa_{datetime.utcnow().strftime('%Y%m%d%H%M%S')}_{secrets.token_hex(3)}",
        "question": question,
        "answer": answer,
        "created_at": utc_now(),
    }
    history.append(item)
    write_json_file(path, history)
    return item


def delete_reading_question_history_item(paper_id: str, qa_id: str) -> bool:
    path = reading_qa_history_path(paper_id)
    history = read_json_file(path, [])
    if not isinstance(history, list):
        history = []
    kept = [item for item in history if str(item.get("id") or "") != qa_id]
    if len(kept) == len(history):
        return False
    write_json_file(path, kept)
    return True


def save_manual_note(paper_id: str, module_name: str, content: str) -> dict:
    allowed = {"overview", "problem", "method", "results", "critique"}
    if module_name not in allowed:
        raise ValueError("不支持的 Notes 模块")
    path = reading_notes_path(paper_id)
    notes = normalize_notes_payload(read_json_file(path, {}))
    notes[module_name] = content
    write_json_file(path, notes)
    return {
        "module": module_name,
        "content": content,
        "updated_at": utc_now(),
    }


def answer_reading_question(paper_id: str, question: str) -> dict:
    bundle = load_reading_bundle(paper_id)
    if not bundle:
        raise ValueError("阅读工作区不存在")
    text = load_reading_full_text(paper_id)
    if not text:
        raise ValueError("当前阅读页还没有可用的论文文本，请先完成一次深度分析。")
    answer = moonshot_answer_question(bundle["paper"], bundle["analysis"], text, question)
    return append_reading_question_history(paper_id, question, answer)


def update_citation_pdf(citation_id: int, pdf_path: str, pdf_sha256: str = ""):
    ensure_db()
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute(
            "UPDATE citations SET pdf_path = ?, pdf_sha256 = ? WHERE id = ?",
            (pdf_path, pdf_sha256 or None, citation_id),
        )
        conn.commit()


def update_citation_metadata(citation_id: int, metadata: dict):
    ensure_db()
    citation = get_citation_by_id(citation_id)
    if not citation:
        raise ValueError("Citation 不存在")
    merged_tags = normalize_tags(citation.get("tags") or "")
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute(
            """
            UPDATE citations
            SET title = ?,
                doi = ?,
                authors = ?,
                year = ?,
                venue = ?,
                abstract = ?
            WHERE id = ?
            """,
            (
                (metadata.get("title") or citation.get("title") or "").strip(),
                normalize_doi(metadata.get("doi") or citation.get("doi") or "") or None,
                ", ".join(metadata.get("authors") or []) or citation.get("authors") or "",
                str(metadata.get("year") or citation.get("year") or ""),
                (metadata.get("venue") or citation.get("venue") or "").strip(),
                (metadata.get("abstract") or citation.get("abstract") or "").strip(),
                citation_id,
            ),
        )
        conn.commit()


def update_citation_reading_paper_id(citation_id: int, paper_id: str):
    ensure_db()
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute(
            "UPDATE citations SET reading_paper_id = ? WHERE id = ?",
            (paper_id or None, citation_id),
        )
        conn.commit()


def transfer_citation_groups(source_citation_id: int, target_citation_id: int):
    for group in get_citation_groups(source_citation_id):
        add_citation_to_group(target_citation_id, int(group["id"]))


def delete_citation_group_links(citation_id: int):
    ensure_db()
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute("DELETE FROM citation_group_links WHERE citation_id = ?", (citation_id,))
        conn.commit()


def delete_citation(citation_id: int):
    ensure_db()
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute("DELETE FROM citations WHERE id = ?", (citation_id,))
        conn.commit()


def merge_citation_into_existing(source_citation_id: int, target_citation_id: int, metadata: dict | None = None):
    source = get_citation_by_id(source_citation_id)
    target = get_citation_by_id(target_citation_id)
    if not source or not target:
        raise ValueError("待合并文献不存在")
    source_paper_id = (source.get("reading_paper_id") or "").strip()
    transfer_citation_groups(source_citation_id, target_citation_id)
    if source.get("pdf_path"):
        update_citation_pdf(target_citation_id, source.get("pdf_path") or "", source.get("pdf_sha256") or "")
    if metadata:
        enriched = dict(target)
        if not (enriched.get("doi") or "").strip() and (metadata.get("doi") or "").strip():
            enriched["doi"] = metadata.get("doi")
        if not (enriched.get("authors") or "").strip() and metadata.get("authors"):
            enriched["authors"] = ", ".join(metadata.get("authors") or [])
        if not (enriched.get("year") or "").strip() and (metadata.get("year") or "").strip():
            enriched["year"] = metadata.get("year")
        if not (enriched.get("venue") or "").strip() and (metadata.get("venue") or "").strip():
            enriched["venue"] = metadata.get("venue")
        if not (enriched.get("abstract") or "").strip() and (metadata.get("abstract") or "").strip():
            enriched["abstract"] = metadata.get("abstract")
        update_citation_metadata(target_citation_id, enriched)
    if source_paper_id:
        update_citation_reading_paper_id(target_citation_id, source_paper_id)
    delete_citation(source_citation_id)
    return get_citation_by_id(target_citation_id)


def clear_citation_reading_link(citation_id: int):
    ensure_db()
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute(
            "UPDATE citations SET reading_paper_id = NULL WHERE id = ?",
            (citation_id,),
        )
        conn.commit()


def list_reading_groups():
    ensure_db()
    with sqlite3.connect(DB_PATH) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            """
            SELECT id, name, description, created_at
            FROM reading_groups
            ORDER BY created_at DESC
            """
        ).fetchall()
    return [dict(row) for row in rows]


def create_reading_group(name: str, description: str = ""):
    ensure_db()
    now = datetime.utcnow().isoformat(timespec="seconds") + "Z"
    with sqlite3.connect(DB_PATH) as conn:
        cursor = conn.execute(
            "INSERT INTO reading_groups (name, description, created_at) VALUES (?, ?, ?)",
            (name.strip(), description.strip(), now),
        )
        conn.commit()
        return cursor.lastrowid


def delete_reading_group(group_id: int):
    ensure_db()
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute("DELETE FROM citation_group_links WHERE group_id = ?", (group_id,))
        conn.execute("DELETE FROM reading_groups WHERE id = ?", (group_id,))
        conn.commit()


def reading_group_exists(group_id: int) -> bool:
    ensure_db()
    with sqlite3.connect(DB_PATH) as conn:
        row = conn.execute("SELECT 1 FROM reading_groups WHERE id = ?", (group_id,)).fetchone()
    return bool(row)


def citation_exists(citation_id: int) -> bool:
    ensure_db()
    with sqlite3.connect(DB_PATH) as conn:
        row = conn.execute("SELECT 1 FROM citations WHERE id = ?", (citation_id,)).fetchone()
    return bool(row)


def get_citation_groups(citation_id: int):
    ensure_db()
    with sqlite3.connect(DB_PATH) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            """
            SELECT rg.id, rg.name, rg.description
            FROM reading_groups rg
            JOIN citation_group_links cgl ON rg.id = cgl.group_id
            WHERE cgl.citation_id = ?
            ORDER BY rg.name
            """,
            (citation_id,),
        ).fetchall()
    return [dict(row) for row in rows]


def add_citation_to_group(citation_id: int, group_id: int):
    ensure_db()
    now = datetime.utcnow().isoformat(timespec="seconds") + "Z"
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute(
            """
            INSERT OR IGNORE INTO citation_group_links (citation_id, group_id, created_at)
            VALUES (?, ?, ?)
            """,
            (citation_id, group_id, now),
        )
        conn.commit()


def remove_citation_from_group(citation_id: int, group_id: int):
    ensure_db()
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute(
            "DELETE FROM citation_group_links WHERE citation_id = ? AND group_id = ?",
            (citation_id, group_id),
        )
        conn.commit()


def list_citations_with_groups():
    ensure_db()
    citations = list_citations()
    for citation in citations:
        citation["groups"] = get_citation_groups(citation["id"])
    return citations


def safe_file_stem(name: str, fallback: str = "paper") -> str:
    chars = []
    for ch in (name or "").strip():
        if ch.isalnum():
            chars.append(ch.lower())
        elif chars and chars[-1] != "-":
            chars.append("-")
    value = "".join(chars).strip("-")
    return value[:80] or fallback


def compute_stream_sha256(stream) -> str:
    stream.seek(0)
    digest = hashlib.sha256()
    while True:
        chunk = stream.read(1024 * 1024)
        if not chunk:
            break
        digest.update(chunk)
    stream.seek(0)
    return digest.hexdigest()


def compute_file_sha256(path: Path) -> str:
    with path.open("rb") as fh:
        return compute_stream_sha256(fh)


def find_existing_pdf_by_hash(pdf_sha256: str) -> dict | None:
    ensure_db()
    if not pdf_sha256:
        return None
    with sqlite3.connect(DB_PATH) as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            """
            SELECT id, pdf_path, pdf_sha256
            FROM citations
            WHERE pdf_sha256 = ?
              AND pdf_path IS NOT NULL
              AND pdf_path != ''
            ORDER BY id DESC
            LIMIT 1
            """,
            (pdf_sha256,),
        ).fetchone()
    if not row:
        return None
    payload = dict(row)
    pdf_abs = DATA_DIR / payload["pdf_path"]
    return payload if pdf_abs.exists() else None


def store_uploaded_pdf(file_item, title: str = "") -> dict:
    if file_item is None or not getattr(file_item, "file", None):
        return {"pdf_path": "", "pdf_sha256": "", "reused": False}
    filename = (getattr(file_item, "filename", "") or "").strip()
    content_type = (getattr(file_item, "type", "") or "").lower()
    suffix = Path(filename).suffix.lower()
    if suffix != ".pdf" and content_type != "application/pdf":
        raise ValueError("仅支持上传 PDF 文件。")
    pdf_sha256 = compute_stream_sha256(file_item.file)
    existing = find_existing_pdf_by_hash(pdf_sha256)
    if existing:
        return {
            "pdf_path": existing["pdf_path"],
            "pdf_sha256": pdf_sha256,
            "reused": True,
        }
    stem = safe_file_stem(title or Path(filename).stem or "paper")
    unique_name = f"{datetime.utcnow().strftime('%Y%m%d-%H%M%S')}-{secrets.token_hex(4)}-{stem}.pdf"
    dest = LIBRARY_DIR / unique_name
    file_item.file.seek(0)
    with dest.open("wb") as fh:
        shutil.copyfileobj(file_item.file, fh)
    return {
        "pdf_path": f"library/{unique_name}",
        "pdf_sha256": pdf_sha256,
        "reused": False,
    }


def citation_pdf_abspath(citation: dict | None) -> Path | None:
    if not citation:
        return None
    pdf_rel = (citation.get("pdf_path") or "").strip()
    if not pdf_rel:
        return None
    path = DATA_DIR / pdf_rel
    return path if path.exists() else None


def citation_has_pdf(citation: dict | None) -> bool:
    return citation_pdf_abspath(citation) is not None


def utc_now() -> str:
    return datetime.utcnow().isoformat(timespec="seconds") + "Z"


def build_reading_paper_id(citation: dict) -> str:
    base = safe_file_stem(citation.get("title") or "", fallback="paper")
    return f"paper_{datetime.utcnow().strftime('%Y%m%d_%H%M%S')}_{secrets.token_hex(3)}_{base[:24]}"


def reading_workspace_path(paper_id: str) -> Path:
    return READING_DIR / paper_id


def reading_qa_history_path(paper_id: str) -> Path:
    return reading_workspace_path(paper_id) / "qa_history.json"


def reading_notes_path(paper_id: str) -> Path:
    return reading_workspace_path(paper_id) / "notes.json"


def normalize_notes_payload(payload) -> dict:
    if isinstance(payload, dict):
        return {str(key): str(value or "") for key, value in payload.items()}
    return {}


def default_analysis_payload(paper_id: str) -> dict:
    now = utc_now()
    empty_module = {"status": "pending", "version": 0, "generated_at": None, "data": {}}
    return {
        "paper_id": paper_id,
        "schema_version": "1.0.0",
        "analysis_version": 1,
        "pipeline_mode": "1_call",
        "modules": {
            "overview": dict(empty_module),
            "problem": dict(empty_module),
            "method": dict(empty_module),
            "results": dict(empty_module),
            "critique": dict(empty_module),
        },
        "calls": [
            {
                "call_id": "call_01",
                "module": "all_modules",
                "status": "pending",
                "started_at": None,
                "ended_at": None,
                "model": None,
                "input_scope": ["full_pdf"],
                "output_version": 0,
            }
        ],
        "updated_at": now,
    }


def split_authors(authors_text: str) -> list[str]:
    parts = []
    for item in (authors_text or "").replace(";", ",").split(","):
        name = " ".join(item.strip().split())
        if name:
            parts.append(name)
    return parts


def normalize_title_for_match(title: str) -> str:
    chars = []
    prev_space = False
    for ch in (title or "").lower():
        if ch.isalnum():
            chars.append(ch)
            prev_space = False
        elif not prev_space:
            chars.append(" ")
            prev_space = True
    return " ".join("".join(chars).split())


def title_similarity(a: str, b: str) -> float:
    left = normalize_title_for_match(a)
    right = normalize_title_for_match(b)
    if not left or not right:
        return 0.0
    return SequenceMatcher(None, left, right).ratio()


def ensure_reading_workspace_for_citation(citation_id: int):
    ensure_db()
    citation = get_citation_by_id(citation_id)
    if not citation:
        raise ValueError("Citation 不存在")
    library_pdf = citation_pdf_abspath(citation)
    if not library_pdf:
        raise ValueError("该文献尚未上传 PDF，暂时不能进入深度阅读。")

    paper_id = citation.get("reading_paper_id") or build_reading_paper_id(citation)
    workspace = reading_workspace_path(paper_id)
    source_dir = workspace / "source"
    source_dir.mkdir(parents=True, exist_ok=True)

    paper_json_path = workspace / "paper.json"
    analysis_json_path = workspace / "analysis.json"
    qa_history_path = reading_qa_history_path(paper_id)
    notes_path = reading_notes_path(paper_id)
    source_pdf_path = f"/{library_pdf.relative_to(DATA_DIR).as_posix()}"

    # Older workspaces may have copied the PDF into reading/source.
    # Keep only derived artifacts there and reuse the single library copy.
    for stale_pdf in source_dir.glob("*.pdf"):
        try:
            stale_pdf.unlink()
        except Exception:
            pass

    now = utc_now()
    paper_payload = {
        "paper_id": paper_id,
        "title": citation.get("title") or "",
        "authors": split_authors(citation.get("authors") or ""),
        "year": int(citation["year"]) if str(citation.get("year") or "").isdigit() else citation.get("year") or None,
        "venue": citation.get("venue") or "",
        "doi": citation.get("doi") or None,
        "keywords": [part.strip() for part in (citation.get("tags") or "").split(",") if part.strip()],
        "pdf": {
            "file_name": Path(source_pdf_path).name if source_pdf_path else "",
            "file_path": source_pdf_path,
            "page_count": None,
            "uploaded_at": citation.get("created_at") or now,
        },
        "text_source": {
            "full_text_path": f"/reading/{paper_id}/source/full_text.json",
            "sections_path": f"/reading/{paper_id}/source/sections.json",
        },
        "status": {
            "ingestion": "completed" if source_pdf_path else "pending",
            "analysis": "pending",
            "metadata": "completed" if citation.get("title") else "pending",
        },
        "created_at": citation.get("created_at") or now,
        "updated_at": now,
    }
    paper_json_path.write_text(json.dumps(paper_payload, ensure_ascii=False, indent=2), encoding="utf-8")

    if not analysis_json_path.exists():
        analysis_json_path.write_text(json.dumps(default_analysis_payload(paper_id), ensure_ascii=False, indent=2), encoding="utf-8")
    if not qa_history_path.exists():
        qa_history_path.write_text("[]", encoding="utf-8")
    if not notes_path.exists():
        notes_path.write_text("{}", encoding="utf-8")

    with sqlite3.connect(DB_PATH) as conn:
        conn.execute(
            "UPDATE citations SET reading_paper_id = ? WHERE id = ?",
            (paper_id, citation_id),
        )
        conn.commit()

    return {
        "paper_id": paper_id,
        "workspace": workspace,
        "paper_json_path": paper_json_path,
        "analysis_json_path": analysis_json_path,
        "reading_url": f"/reading/{paper_id}",
    }


def load_reading_bundle(paper_id: str):
    workspace = reading_workspace_path(paper_id)
    paper_json_path = workspace / "paper.json"
    analysis_json_path = workspace / "analysis.json"
    qa_history_path = reading_qa_history_path(paper_id)
    notes_path = reading_notes_path(paper_id)
    if not paper_json_path.exists() or not analysis_json_path.exists():
        return None
    try:
        paper = json.loads(paper_json_path.read_text(encoding="utf-8"))
        analysis = json.loads(analysis_json_path.read_text(encoding="utf-8"))
        qa_history = json.loads(qa_history_path.read_text(encoding="utf-8")) if qa_history_path.exists() else []
        notes = json.loads(notes_path.read_text(encoding="utf-8")) if notes_path.exists() else {}
    except Exception:
        return None
    return {
        "paper": paper,
        "analysis": analysis,
        "qa_history": qa_history if isinstance(qa_history, list) else [],
        "notes": normalize_notes_payload(notes),
        "workspace": workspace,
    }


def reading_json_ready(paper_id: str) -> bool:
    bundle = load_reading_bundle(paper_id)
    return bool(bundle)


def remove_reading_workspace_for_citation(citation_id: int):
    citation = get_citation_by_id(citation_id)
    if not citation:
        raise ValueError("Citation 不存在")
    paper_id = (citation.get("reading_paper_id") or "").strip()
    pdf_rel = (citation.get("pdf_path") or "").strip()
    pdf_sha256 = (citation.get("pdf_sha256") or "").strip()

    if paper_id:
        workspace = reading_workspace_path(paper_id)
        if workspace.exists():
            shutil.rmtree(workspace, ignore_errors=True)
        with ANALYSIS_JOBS_LOCK:
            ANALYSIS_JOBS.pop(paper_id, None)

    should_delete_pdf = False
    pdf_abs = None
    if pdf_rel:
        pdf_abs = DATA_DIR / pdf_rel
        ensure_db()
        with sqlite3.connect(DB_PATH) as conn:
            row = conn.execute(
                """
                SELECT COUNT(1)
                FROM citations
                WHERE id != ?
                  AND (
                    pdf_path = ?
                    OR (? != '' AND pdf_sha256 = ?)
                  )
                """,
                (citation_id, pdf_rel, pdf_sha256, pdf_sha256),
            ).fetchone()
        should_delete_pdf = not bool((row or [0])[0])

    delete_citation_group_links(citation_id)
    delete_citation(citation_id)

    if should_delete_pdf and pdf_abs and pdf_abs.exists():
        try:
            pdf_abs.unlink()
        except Exception:
            pass
    return True


def match_existing_citation(title: str = "", doi: str = "", year: str = ""):
    ensure_db()
    normalized_doi = (doi or "").strip()
    normalized_title = (title or "").strip()
    normalized_year = str(year or "").strip()
    with sqlite3.connect(DB_PATH) as conn:
        conn.row_factory = sqlite3.Row
        row = None
        if normalized_doi:
            row = conn.execute(
                """
                SELECT id, title, doi, url, authors, year, venue, abstract,
                       matched_kw, tags, source_search_slug, source_csv_index, pdf_path, reading_paper_id, created_at
                FROM citations WHERE doi = ?
                """,
                (normalized_doi,),
            ).fetchone()
        if not row and normalized_title and normalized_year:
            row = conn.execute(
                """
                SELECT id, title, doi, url, authors, year, venue, abstract,
                       matched_kw, tags, source_search_slug, source_csv_index, pdf_path, reading_paper_id, created_at
                FROM citations WHERE title = ? AND year = ?
                """,
                (normalized_title, normalized_year),
            ).fetchone()
        if not row and normalized_title:
            row = conn.execute(
                """
                SELECT id, title, doi, url, authors, year, venue, abstract,
                       matched_kw, tags, source_search_slug, source_csv_index, pdf_path, reading_paper_id, created_at
                FROM citations WHERE title = ?
                ORDER BY created_at DESC LIMIT 1
                """,
                (normalized_title,),
            ).fetchone()
        if not row and normalized_title:
            rows = conn.execute(
                """
                SELECT id, title, doi, url, authors, year, venue, abstract,
                       matched_kw, tags, source_search_slug, source_csv_index, pdf_path, reading_paper_id, created_at
                FROM citations
                WHERE title IS NOT NULL AND title != ''
                ORDER BY created_at DESC
                """
            ).fetchall()
            best_row = None
            best_score = 0.0
            for candidate in rows:
                score = title_similarity(normalized_title, candidate["title"] or "")
                if score >= 0.85 and score > best_score:
                    best_row = candidate
                    best_score = score
            row = best_row
    return dict(row) if row else None


def create_or_match_citation_from_metadata(metadata: dict, pdf_path: str = "", pdf_sha256: str = "", search_slug: str = ""):
    title = (metadata.get("title") or "").strip()
    doi = normalize_doi(metadata.get("doi") or "")
    year = str(metadata.get("year") or "").strip()
    matched = match_existing_citation(title=title, doi=doi, year=year)
    if matched:
        if pdf_path:
            update_citation_pdf(int(matched["id"]), pdf_path, pdf_sha256)
        return get_citation_by_id(int(matched["id"])), True
    paper = {
        "title": title or (Path(pdf_path).stem if pdf_path else "Untitled Paper"),
        "doi": doi,
        "url": "",
        "authors": ", ".join(metadata.get("authors") or []),
        "year": year,
        "venue": (metadata.get("venue") or "").strip(),
        "content": (metadata.get("abstract") or "").strip(),
        "matched_kw": "",
        "csv_index": None,
        "tags": "",
    }
    citation_id = upsert_citation(
        paper,
        (search_slug or "").strip(),
        pdf_path=pdf_path or None,
        pdf_sha256=pdf_sha256 or None,
    )
    return get_citation_by_id(citation_id), bool(matched)


def read_json_file(path: Path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


def write_json_file(path: Path, payload: dict):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def update_paper_status(paper_json_path: Path, *, ingestion: str | None = None, analysis: str | None = None):
    paper = read_json_file(paper_json_path, {})
    status = paper.setdefault("status", {})
    if ingestion is not None:
        status["ingestion"] = ingestion
    if analysis is not None:
        status["analysis"] = analysis
    paper["updated_at"] = utc_now()
    write_json_file(paper_json_path, paper)
    return paper


def update_paper_analysis_progress(
    paper_json_path: Path,
    *,
    state: str | None = None,
    progress: int | None = None,
    stage: str | None = None,
    message: str | None = None,
):
    paper = read_json_file(paper_json_path, {})
    status = paper.setdefault("status", {})
    if state is not None:
        status["analysis"] = state
    if progress is not None:
        status["analysis_progress"] = max(0, min(100, int(progress)))
    if stage is not None:
        status["analysis_stage"] = stage
    if message is not None:
        status["analysis_message"] = message
    paper["updated_at"] = utc_now()
    write_json_file(paper_json_path, paper)
    return paper


def update_paper_metadata_progress(
    paper_json_path: Path,
    *,
    state: str | None = None,
    message: str | None = None,
):
    paper = read_json_file(paper_json_path, {})
    status = paper.setdefault("status", {})
    if state is not None:
        status["metadata"] = state
    if message is not None:
        status["metadata_message"] = message
    paper["updated_at"] = utc_now()
    write_json_file(paper_json_path, paper)
    return paper


def create_placeholder_citation_for_pdf(pdf_path: str, pdf_sha256: str = "") -> int:
    stem = Path(pdf_path).stem if pdf_path else "untitled-paper"
    paper = {
        "title": stem,
        "doi": "",
        "url": "",
        "authors": "",
        "year": "",
        "venue": "",
        "content": "",
        "matched_kw": "",
        "csv_index": None,
        "tags": "",
    }
    citation_id = upsert_citation(paper, "deep-reading-upload", pdf_path=pdf_path or None, pdf_sha256=pdf_sha256 or None)
    if not citation_id:
        raise ValueError("无法创建占位文献记录")
    return citation_id


def moonshot_headers() -> dict:
    if not MOONSHOT_API_KEY:
        raise ValueError("未配置 MOONSHOT_API_KEY")
    return {"Authorization": f"Bearer {MOONSHOT_API_KEY}"}


def moonshot_session():
    session = requests.Session()
    session.trust_env = False
    return session


def moonshot_temperature() -> int | float:
    model = (MOONSHOT_ANALYSIS_MODEL or "").strip().lower()
    if model == "kimi-k2.5":
        return 1
    return 0.2


def raise_for_moonshot(resp):
    try:
        resp.raise_for_status()
    except requests.HTTPError as exc:
        detail = ""
        try:
            payload = resp.json()
            detail = ((payload.get("error") or {}).get("message") or "").strip()
        except Exception:
            detail = (resp.text or "").strip()[:500]
        if detail:
            raise ValueError(detail) from exc
        raise


def moonshot_upload_pdf(pdf_path: Path) -> dict:
    session = moonshot_session()
    with pdf_path.open("rb") as fh:
        resp = session.post(
            f"{MOONSHOT_BASE_URL}/files",
            headers=moonshot_headers(),
            files={"file": (pdf_path.name, fh, "application/pdf")},
            data={"purpose": "file-extract"},
            timeout=120,
        )
    raise_for_moonshot(resp)
    return resp.json()


def moonshot_get_file_content(file_id: str):
    session = moonshot_session()
    resp = session.get(
        f"{MOONSHOT_BASE_URL}/files/{quote(file_id, safe='')}/content",
        headers=moonshot_headers(),
        timeout=120,
    )
    raise_for_moonshot(resp)
    content_type = (resp.headers.get("Content-Type") or "").lower()
    if "application/json" in content_type:
        return resp.json()
    text = resp.text
    try:
        return json.loads(text)
    except Exception:
        return {"content": text}


def normalize_full_text_payload(raw_content):
    if isinstance(raw_content, dict):
        payload = raw_content
    else:
        payload = {"content": str(raw_content or "")}
    text = ""
    if isinstance(payload.get("content"), str):
        text = payload["content"]
    elif isinstance(payload.get("text"), str):
        text = payload["text"]
    elif isinstance(payload.get("markdown"), str):
        text = payload["markdown"]
    elif isinstance(payload.get("pages"), list):
        text = "\n\n".join(
            page.get("content") or page.get("text") or ""
            for page in payload["pages"]
            if isinstance(page, dict)
        )
    sections = []
    for idx, block in enumerate(text.split("\n\n"), start=1):
        value = block.strip()
        if not value:
            continue
        sections.append({"id": f"S{idx}", "heading": "", "content": value})
    return {
        "raw": payload,
        "text": text.strip(),
        "sections": sections,
    }


def build_metadata_extraction_prompt(extracted_text: str, filename: str = "") -> str:
    sample = extracted_text[:40000]
    return f"""
你正在从一篇学术论文 PDF 中抽取书目信息。
请只返回一个 JSON 对象，不要输出 Markdown、解释或代码块。

规则：
- 以 PDF 提取文本为唯一主要依据。
- 如果不确定，返回空字符串。
- `authors` 必须是作者姓名数组。
- `year` 如果存在，必须是四位年份字符串。
- `doi` 必须标准化，不能包含 `https://doi.org/` 前缀。
- 除作者名、标题、venue、doi 等原始元数据外，不要额外翻译字段含义。
- `abstract` 尽量保持与原文一致；如果原文是英文摘要，可以保留英文。

必须使用如下 schema：
{{
  "title": "",
  "authors": [""],
  "venue": "",
  "year": "",
  "doi": "",
  "abstract": ""
}}

文件名：{filename}

PDF 提取文本：
{sample}
""".strip()


def moonshot_extract_metadata(extracted_text: str, filename: str = "") -> dict:
    session = moonshot_session()
    payload = {
        "model": MOONSHOT_ANALYSIS_MODEL,
        "messages": [
            {"role": "system", "content": "你负责抽取学术论文元数据。只返回严格合法的 JSON，不要输出任何额外说明。"},
            {"role": "user", "content": build_metadata_extraction_prompt(extracted_text, filename)},
        ],
        "temperature": moonshot_temperature(),
    }
    resp = session.post(
        f"{MOONSHOT_BASE_URL}/chat/completions",
        headers={**moonshot_headers(), "Content-Type": "application/json"},
        json=payload,
        timeout=120,
    )
    raise_for_moonshot(resp)
    data = resp.json()
    content = (((data.get("choices") or [{}])[0].get("message") or {}).get("content") or "")
    meta = extract_json_object(content)
    authors = meta.get("authors") or []
    if isinstance(authors, str):
        authors = split_authors(authors)
    elif isinstance(authors, list):
        authors = [" ".join(str(item).strip().split()) for item in authors if str(item).strip()]
    else:
        authors = []
    return {
        "title": " ".join(str(meta.get("title") or "").split()),
        "authors": authors,
        "venue": " ".join(str(meta.get("venue") or "").split()),
        "year": str(meta.get("year") or "").strip(),
        "doi": normalize_doi(str(meta.get("doi") or "").strip()),
        "abstract": " ".join(str(meta.get("abstract") or "").split()),
    }


def process_uploaded_pdf_metadata(citation_id: int, pdf_path: str, paper_id: str = ""):
    citation = get_citation_by_id(citation_id)
    if not citation:
        return
    workspace = reading_workspace_path(paper_id) if paper_id else None
    paper_json_path = (workspace / "paper.json") if workspace else None
    if paper_json_path and paper_json_path.exists():
        update_paper_metadata_progress(paper_json_path, state="processing", message="正在识别 PDF 元数据。")
    try:
        file_meta = moonshot_upload_pdf(DATA_DIR / pdf_path)
        file_id = file_meta.get("id") or file_meta.get("file_id") or ""
        if not file_id:
            raise ValueError("Moonshot files 接口未返回 file id")
        raw_content = moonshot_get_file_content(file_id)
        normalized = normalize_full_text_payload(raw_content)
        metadata = moonshot_extract_metadata(normalized["text"], Path(pdf_path).name)
        matched = match_existing_citation(
            title=(metadata.get("title") or "").strip(),
            doi=normalize_doi(metadata.get("doi") or ""),
            year=str(metadata.get("year") or "").strip(),
        )
        target_citation_id = citation_id
        if matched and int(matched["id"]) != citation_id:
            merged = merge_citation_into_existing(citation_id, int(matched["id"]), metadata)
            if merged:
                target_citation_id = int(merged["id"])
        else:
            update_citation_metadata(citation_id, metadata)
        if paper_id:
            update_citation_reading_paper_id(target_citation_id, paper_id)
        ensure_reading_workspace_for_citation(target_citation_id)
        refreshed = get_citation_by_id(target_citation_id)
        if paper_json_path and paper_json_path.exists() and refreshed:
            paper = read_json_file(paper_json_path, {})
            paper["title"] = refreshed.get("title") or paper.get("title") or ""
            paper["authors"] = split_authors(refreshed.get("authors") or "")
            paper["year"] = refreshed.get("year") or paper.get("year")
            paper["venue"] = refreshed.get("venue") or paper.get("venue") or ""
            paper["doi"] = refreshed.get("doi") or paper.get("doi")
            paper["keywords"] = [part.strip() for part in (refreshed.get("tags") or "").split(",") if part.strip()]
            paper["updated_at"] = utc_now()
            write_json_file(paper_json_path, paper)
            update_paper_metadata_progress(paper_json_path, state="completed", message="PDF 元数据识别完成。")
    except Exception as exc:
        if paper_json_path and paper_json_path.exists():
            update_paper_metadata_progress(paper_json_path, state="failed", message=str(exc))


def start_uploaded_pdf_metadata_job(citation_id: int, pdf_path: str, paper_id: str = ""):
    thread = threading.Thread(
        target=process_uploaded_pdf_metadata,
        args=(citation_id, pdf_path, paper_id),
        daemon=True,
    )
    thread.start()


def build_single_call_prompt(paper: dict, extracted_text: str) -> str:
    sample = extracted_text[:120000]
    return f"""
你正在阅读一篇学术论文 PDF，并且必须只输出一个 JSON 对象。

请严格输出以下顶层键：
- overview
- problem
- method
- results
- critique

规则：
- 只输出合法 JSON，不要输出 Markdown 代码块或解释。
- 字段名必须与要求完全一致。
- 所有自然语言内容一律用中文输出。
- 专有名词、论文标题、方法名、缩写、数据集名、模型名可以保留英文原文。
- 总结风格要简洁但信息密度高，偏学术写作。
- 如果证据不足，用空字符串或空数组，不要编造。
- 所有字段都必须基于提供的 PDF 文本。

论文元信息：
title: {paper.get('title') or ''}
authors: {', '.join(paper.get('authors') or [])}
venue: {paper.get('venue') or ''}
year: {paper.get('year') or ''}
doi: {paper.get('doi') or ''}

必须使用如下 schema：
{{
  "overview": {{
    "paper_type": "",
    "research_theme": "",
    "core_problem": "",
    "core_approach": "",
    "contributions": ["", ""]
  }},
  "problem": {{
    "background": "",
    "gap": "",
    "importance": "",
    "research_goal": "",
    "paper_logic": [
      {{"step": 1, "label": "Problem", "content": ""}},
      {{"step": 2, "label": "Approach", "content": ""}},
      {{"step": 3, "label": "Evaluation", "content": ""}},
      {{"step": 4, "label": "Findings", "content": ""}},
      {{"step": 5, "label": "Implications", "content": ""}}
    ]
  }},
  "method": {{
    "object_of_study": "",
    "method_goal": "",
    "pipeline": ["", ""],
    "design_choices": [
      {{"choice": "", "why_it_matters": ""}}
    ],
    "participants_or_data": "",
    "evaluation_setup": "",
    "analysis_method": ""
  }},
  "results": {{
    "findings": [
      {{"id": "F1", "claim": "", "evidence": "", "figure_refs": [], "support_level": ""}}
    ],
    "key_figures": [
      {{"figure_id": "", "title": "", "what_it_shows": "", "why_it_matters": ""}}
    ],
    "author_claims": [""],
    "claim_evidence_match": ""
  }},
  "critique": {{
    "strengths": [""],
    "limitations": [""],
    "hidden_assumptions": [""],
    "weak_points": [""],
    "future_directions": [""],
    "research_positioning": ""
  }}
}}

请确保所有 `content`、`background`、`gap`、`importance`、`research_goal`、`claim`、`evidence`、`what_it_shows`、`why_it_matters`、`strengths`、`limitations` 等自然语言字段都输出中文。

PDF 提取文本：
{sample}
""".strip()


def extract_json_object(text: str) -> dict:
    text = (text or "").strip()
    if not text:
        raise ValueError("模型未返回内容")
    try:
        return json.loads(text)
    except Exception:
        start = text.find("{")
        end = text.rfind("}")
        if start >= 0 and end > start:
            return json.loads(text[start:end + 1])
    raise ValueError("模型返回不是合法 JSON")


def moonshot_generate_analysis(paper: dict, extracted_text: str) -> dict:
    session = moonshot_session()
    payload = {
        "model": MOONSHOT_ANALYSIS_MODEL,
        "messages": [
            {"role": "system", "content": "你是一个严谨的论文精读助手。你只输出严格合法的 JSON，并且所有自然语言分析内容都使用中文。"},
            {"role": "user", "content": build_single_call_prompt(paper, extracted_text)},
        ],
        "temperature": moonshot_temperature(),
    }
    resp = session.post(
        f"{MOONSHOT_BASE_URL}/chat/completions",
        headers={**moonshot_headers(), "Content-Type": "application/json"},
        json=payload,
        timeout=240,
    )
    raise_for_moonshot(resp)
    data = resp.json()
    content = (((data.get("choices") or [{}])[0].get("message") or {}).get("content") or "")
    return extract_json_object(content)


def coerce_analysis_result(result: dict, paper_id: str) -> dict:
    now = utc_now()
    modules = {}
    for name in ("overview", "problem", "method", "results", "critique"):
        modules[name] = {
            "status": "completed",
            "version": 1,
            "generated_at": now,
            "data": result.get(name) or {},
        }
    return {
        "paper_id": paper_id,
        "schema_version": "1.0.0",
        "analysis_version": 1,
        "pipeline_mode": "1_call",
        "modules": modules,
        "calls": [
            {
                "call_id": "call_01",
                "module": "all_modules",
                "status": "completed",
                "started_at": None,
                "ended_at": now,
                "model": MOONSHOT_ANALYSIS_MODEL,
                "input_scope": ["full_pdf"],
                "output_version": 1,
            }
        ],
        "updated_at": now,
    }


def analyze_reading_paper(paper_id: str):
    bundle = load_reading_bundle(paper_id)
    if not bundle:
        raise ValueError("阅读工作区不存在")
    paper = bundle["paper"]
    workspace = bundle["workspace"]
    paper_json_path = workspace / "paper.json"
    analysis_json_path = workspace / "analysis.json"
    pdf_rel = ((paper.get("pdf") or {}).get("file_path") or "").lstrip("/")
    if not pdf_rel:
        raise ValueError("该阅读工作区缺少 PDF 链接")
    pdf_abs = DATA_DIR / pdf_rel
    if not pdf_abs.exists():
        raise ValueError("该阅读工作区缺少可访问的 PDF 文件")

    update_paper_status(paper_json_path, ingestion="processing", analysis="in_progress")
    update_paper_analysis_progress(
        paper_json_path,
        state="in_progress",
        progress=5,
        stage="queued",
        message="已进入分析队列。",
    )
    try:
        update_paper_analysis_progress(
            paper_json_path,
            progress=20,
            stage="uploading_pdf",
            message="正在上传 PDF 到 Moonshot。",
        )
        file_meta = moonshot_upload_pdf(pdf_abs)
        file_id = file_meta.get("id") or file_meta.get("file_id") or ""
        if not file_id:
            raise ValueError("Moonshot files 接口未返回 file id")
        update_paper_analysis_progress(
            paper_json_path,
            progress=45,
            stage="extracting_text",
            message="正在抽取 PDF 文本。",
        )
        raw_content = moonshot_get_file_content(file_id)
        normalized = normalize_full_text_payload(raw_content)

        source_dir = workspace / "source"
        full_text_json = source_dir / "full_text.json"
        sections_json = source_dir / "sections.json"
        write_json_file(full_text_json, {"file": file_meta, "content": normalized["raw"], "text": normalized["text"]})
        write_json_file(sections_json, {"sections": normalized["sections"]})

        update_paper_analysis_progress(
            paper_json_path,
            progress=70,
            stage="generating_analysis",
            message="正在生成深度阅读结构化分析。",
        )
        result = moonshot_generate_analysis(paper, normalized["text"])
        final_analysis = coerce_analysis_result(result, paper_id)
        update_paper_analysis_progress(
            paper_json_path,
            progress=90,
            stage="writing_results",
            message="正在写入分析结果。",
        )
        write_json_file(analysis_json_path, final_analysis)
        merge_analysis_theme_into_citation(paper_id, final_analysis)
        update_paper_status(paper_json_path, ingestion="completed", analysis="completed")
        update_paper_analysis_progress(
            paper_json_path,
            state="completed",
            progress=100,
            stage="completed",
            message="分析完成。",
        )
        return final_analysis
    except Exception as exc:
        update_paper_status(paper_json_path, ingestion="completed", analysis="failed")
        update_paper_analysis_progress(
            paper_json_path,
            state="failed",
            progress=100,
            stage="failed",
            message=str(exc),
        )
        raise


def get_reading_status_payload(paper_id: str) -> dict:
    bundle = load_reading_bundle(paper_id)
    if not bundle:
        raise ValueError("阅读工作区不存在")
    paper = bundle["paper"]
    status = paper.get("status") or {}
    return {
        "paper_id": paper_id,
        "analysis": (status.get("analysis") or "pending").strip(),
        "ingestion": (status.get("ingestion") or "pending").strip(),
        "metadata": (status.get("metadata") or "pending").strip(),
        "analysis_progress": int(status.get("analysis_progress") or 0),
        "analysis_stage": status.get("analysis_stage") or "",
        "analysis_message": status.get("analysis_message") or "",
        "metadata_message": status.get("metadata_message") or "",
        "updated_at": paper.get("updated_at") or "",
    }


def run_analysis_job(paper_id: str):
    with ANALYSIS_JOBS_LOCK:
        ANALYSIS_JOBS[paper_id] = {"status": "running", "started_at": utc_now()}
    try:
        analyze_reading_paper(paper_id)
        with ANALYSIS_JOBS_LOCK:
            ANALYSIS_JOBS[paper_id] = {"status": "completed", "ended_at": utc_now()}
    except Exception as exc:
        with ANALYSIS_JOBS_LOCK:
            ANALYSIS_JOBS[paper_id] = {"status": "failed", "ended_at": utc_now(), "error": str(exc)}


def start_analysis_job(paper_id: str) -> dict:
    payload = get_reading_status_payload(paper_id)
    if payload["analysis"] == "in_progress":
        return {"started": False, "status": payload}
    bundle = load_reading_bundle(paper_id)
    if not bundle:
        raise ValueError("阅读工作区不存在")
    paper_json_path = bundle["workspace"] / "paper.json"
    update_paper_status(paper_json_path, analysis="in_progress")
    update_paper_analysis_progress(
        paper_json_path,
        state="in_progress",
        progress=2,
        stage="queued",
        message="任务已提交，等待后台开始分析。",
    )
    thread = threading.Thread(target=run_analysis_job, args=(paper_id,), daemon=True)
    thread.start()
    return {"started": True, "status": get_reading_status_payload(paper_id)}


def reconstruct_abstract(inverted_index: dict | None) -> str:
    if not inverted_index:
        return ""
    words = {}
    for word, positions in inverted_index.items():
        for pos in positions:
            words[pos] = word
    return " ".join(words[i] for i in sorted(words))


def build_ai4scholar_identifier(paper: dict) -> str:
    for key in ("paper_id", "paperId", "ai4scholar_paper_id"):
        value = (paper.get(key) or "").strip()
        if value:
            return value
    doi = (paper.get("doi") or "").strip()
    if doi:
        return f"DOI:{doi}"
    raise ValueError("该论文缺少 DOI 和 paperId，暂时无法做延展搜索。")


def build_external_url(external_ids: dict | None, fallback_url: str = "") -> str:
    external_ids = external_ids or {}
    doi = (external_ids.get("DOI") or "").strip()
    if doi:
        return f"https://doi.org/{doi}"
    arxiv_id = (external_ids.get("ArXiv") or "").strip()
    if arxiv_id:
        return f"https://arxiv.org/abs/{arxiv_id}"
    return (fallback_url or "").strip()


def fetch_ai4scholar_citation_records(paper: dict, limit: int) -> list[dict]:
    if not AI4SCHOLAR_API_KEY:
        return []
    identifier = build_ai4scholar_identifier(paper)
    url = f"https://ai4scholar.net/graph/v1/paper/{quote(identifier, safe=':')}/citations"
    try:
        resp = requests.get(
            url,
            params={
                "fields": "paperId,title,year,externalIds,authors,venue,abstract,openAccessPdf",
                "limit": limit,
            },
            headers={
                "Authorization": f"Bearer {AI4SCHOLAR_API_KEY}",
                "User-Agent": "ccf-crawler-site/1.0",
            },
            timeout=25,
        )
        resp.raise_for_status()
        payload = resp.json()
    except Exception:
        return []

    records = []
    for item in payload.get("data", []) or []:
        citing = item.get("citingPaper") or {}
        title = (citing.get("title") or "").strip()
        if not title:
            continue
        authors = [
            (author.get("name") or "").strip()
            for author in (citing.get("authors") or [])
            if (author.get("name") or "").strip()
        ]
        external_ids = citing.get("externalIds") or {}
        url = build_external_url(external_ids, ((citing.get("openAccessPdf") or {}).get("url") or ""))
        records.append(
            {
                "title": title,
                "venue": citing.get("venue") or "",
                "year": citing.get("year") or "",
                "authors": authors,
                "doi": external_ids.get("DOI") or "",
                "ee": [url] if url else [],
                "abstract": citing.get("abstract") or "",
                "_matched_kw": "citation",
                "key": citing.get("paperId") or title,
                "paper_id": citing.get("paperId") or "",
            }
        )
    return records


def slugify(text: str, fallback: str = "ref-search") -> str:
    chars = []
    prev_dash = False
    for ch in text.lower():
        if ch.isalnum():
            chars.append(ch)
            prev_dash = False
        elif not prev_dash:
            chars.append("-")
            prev_dash = True
    result = "".join(chars).strip("-")
    return result[:50] or fallback


def load_papers_json(search_dir: Path) -> tuple[list[dict], dict]:
    papers_json = search_dir / "papers.json"
    if not papers_json.exists():
        return [], {}
    data = json.loads(papers_json.read_text(encoding="utf-8"))
    return data.get("papers", []), data.get("meta", {})


def fetch_reference_records(doi: str, limit: int) -> list[dict]:
    crossref_url = f"https://api.crossref.org/works/{doi}"
    try:
        resp = requests.get(crossref_url, timeout=20, headers={"User-Agent": "ccf-crawler-site/1.0"})
        resp.raise_for_status()
        message = resp.json().get("message", {})
    except Exception:
        return []

    refs = message.get("reference", []) or []
    records = []
    for ref in refs[:limit]:
        ref_doi = (ref.get("DOI") or "").upper()
        title = (
            ref.get("article-title")
            or ref.get("volume-title")
            or ref.get("series-title")
            or ref.get("journal-title")
            or ""
        )
        author = ref.get("author") or ""
        year = ref.get("year") or ""
        record = {
            "title": title,
            "venue": ref.get("journal-title") or "",
            "year": year,
            "authors": [author] if author else [],
            "doi": ref_doi,
            "ee": [f"https://doi.org/{ref_doi}"] if ref_doi else [],
            "abstract": "",
            "_matched_kw": "reference",
            "key": ref_doi or title,
        }

        if ref_doi:
            try:
                openalex = requests.get(
                    "https://api.openalex.org/works",
                    params={
                        "filter": f"doi:https://doi.org/{ref_doi.lower()}",
                        "per-page": 1,
                        "select": "title,publication_year,doi,abstract_inverted_index,authorships,primary_location,ids",
                        "mailto": "ccf-crawler@example.com",
                    },
                    timeout=20,
                )
                openalex.raise_for_status()
                results = openalex.json().get("results", [])
                if results:
                    item = results[0]
                    oa_authors = [
                        a.get("author", {}).get("display_name", "")
                        for a in item.get("authorships", [])
                    ]
                    record.update(
                        {
                            "title": item.get("title") or title,
                            "year": item.get("publication_year") or year,
                            "authors": [a for a in oa_authors if a],
                            "abstract": reconstruct_abstract(item.get("abstract_inverted_index")),
                            "venue": (
                                ((item.get("primary_location") or {}).get("source") or {}).get("display_name", "")
                            ),
                            "doi": (item.get("doi") or "").replace("https://doi.org/", "") or ref_doi,
                            "ee": [item.get("doi")] if item.get("doi") else record["ee"],
                            "key": item.get("ids", {}).get("openalex", ref_doi),
                        }
                    )
            except Exception:
                pass

        if record["title"]:
            records.append(record)
    return records


def apply_source_matched_kw(records: list[dict], source_kw: str) -> list[dict]:
    source_kw = (source_kw or "").strip()
    if not source_kw:
        return records
    normalized = []
    for record in records:
        item = dict(record)
        item["_matched_kw"] = source_kw
        normalized.append(item)
    return normalized


def list_expansion_sites() -> dict[str, dict]:
    expansions: dict[str, dict] = {}
    for out_dir in iter_result_dirs():
        search_json = out_dir / "search.json"
        site_path = out_dir / "site" / "index.html"
        if not search_json.exists() or not site_path.exists():
            continue
        try:
            meta = json.loads(search_json.read_text(encoding="utf-8"))
        except Exception:
            continue
        source_paper = meta.get("source_paper") or {}
        doi = normalize_doi(source_paper.get("doi") or "")
        if not doi or doi in expansions:
            continue
        expansions[doi] = {
            "doi": doi,
            "site_url": build_site_url(str(out_dir), str(site_path)),
            "slug": meta.get("slug", out_dir.name),
            "title": source_paper.get("title") or "",
            "source_slug": source_paper.get("source_slug") or "",
            "source_csv_index": source_paper.get("source_csv_index"),
            "expansion_source": meta.get("expansion_source") or "",
            "date": meta.get("date") or "",
        }
    return expansions


def create_reference_search(source_slug: str, paper: dict) -> str:
    title = (paper.get("title") or "").strip()
    doi = (paper.get("doi") or "").strip()
    source_kw = (paper.get("matched_kw") or "").strip()
    normalized_doi = normalize_doi(doi)
    if normalized_doi:
        existing = list_expansion_sites().get(normalized_doi)
        if existing:
            return existing["site_url"]
    ref_records = fetch_ai4scholar_citation_records(paper, REFERENCE_LIMIT)
    keywords = [f"citations of {title}"]
    source_kind = "ai4scholar-citations"
    if not ref_records and doi:
        ref_records = fetch_reference_records(doi, REFERENCE_LIMIT)
        keywords = [f"references of {title}"]
        source_kind = "crossref-references"
    if not ref_records:
        raise ValueError("未找到该论文的延展搜索结果。")
    ref_records = apply_source_matched_kw(ref_records, source_kw)

    slug = slugify(f"{source_slug}-refs-{paper.get('csv_index', 'paper')}-{title}")
    out_dir = EXPANSIONS_DIR / f"{datetime.now().date().isoformat()}_{slug}"
    out_dir.mkdir(parents=True, exist_ok=True)

    csv_path = out_dir / "papers.csv"
    json_path = out_dir / "papers.json"
    search_json_path = out_dir / "search.json"
    site_path = out_dir / "site" / "index.html"

    write_csv(ref_records, str(csv_path))
    json_records = build_json_records(ref_records)
    meta = {
        "slug": slug,
        "keywords": keywords,
        "venues": [],
        "top_per_group": REFERENCE_LIMIT,
        "year_from": 0,
        "fetch_abstract": True,
        "date": datetime.now().date().isoformat(),
        "total_papers": len(ref_records),
        "expansion_source": source_kind,
        "source_paper": {
            "title": title,
            "doi": doi,
            "paper_id": (paper.get("paper_id") or paper.get("paperId") or paper.get("ai4scholar_paper_id") or ""),
            "matched_kw": (paper.get("matched_kw") or "").strip(),
            "source_slug": source_slug,
            "source_csv_index": paper.get("csv_index"),
        },
    }
    write_json(json_records, str(json_path), meta)
    write_site(json_records, str(site_path), meta)
    search_json_path.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    return build_site_url(str(out_dir), str(site_path))


def list_search_entries():
    entries = []
    for out_dir in iter_result_dirs():

        search_json = out_dir / "search.json"
        papers_csv = out_dir / "papers.csv"
        papers_json = out_dir / "papers.json"
        site_index = out_dir / "site" / "index.html"

        if not search_json.exists():
            continue

        try:
            meta = json.loads(search_json.read_text(encoding="utf-8"))
        except Exception:
            meta = {}

        date_str = meta.get("date") or out_dir.name.split("_", 1)[0]
        slug = meta.get("slug") or out_dir.name.split("_", 1)[-1]
        try:
            sort_key = datetime.strptime(date_str, "%Y-%m-%d")
        except Exception:
            sort_key = datetime.min
        relative_dir = out_dir.relative_to(DATA_DIR).as_posix()

        entries.append(
            {
                "dir_name": out_dir.name,
                "relative_dir": relative_dir,
                "date": date_str,
                "slug": slug,
                "keywords": meta.get("keywords") or [],
                "venues": meta.get("venues") or [],
                "fetch_abstract": meta.get("fetch_abstract"),
                "total_papers": meta.get("total_papers"),
                "source_paper": meta.get("source_paper") or {},
                "source_matched_kw": get_source_matched_kw(meta),
                "expansion_source": meta.get("expansion_source") or "",
                "is_expansion": bool(meta.get("source_paper")),
                "site_url": f"/{relative_dir}/site/" if site_index.exists() else None,
                "csv_url": f"/{relative_dir}/papers.csv" if papers_csv.exists() else None,
                "json_url": f"/{relative_dir}/papers.json" if papers_json.exists() else None,
                "search_url": f"/{relative_dir}/search.json",
                "sort_key": sort_key,
            }
        )

    entries.sort(key=lambda item: (item["sort_key"], item["dir_name"]), reverse=True)
    return entries


def build_timeline_html():
    entries = list_search_entries()
    original_items_html = []
    expansion_items_html = []
    expansion_tags = []
    seen_expansion_tags = set()

    def render_entry(entry: dict) -> str:
        keywords = " / ".join(entry["keywords"]) if entry["keywords"] else "未记录关键词"
        venues = ", ".join(entry["venues"]) if entry["venues"] else "全局"
        papers_text = f'{entry["total_papers"]} 篇' if entry["total_papers"] is not None else "篇数未知"
        abstract_text = "含摘要" if entry["fetch_abstract"] else "未抓摘要"
        source_paper = entry.get("source_paper") or {}
        is_expansion = bool(entry.get("is_expansion"))
        title = source_paper.get("title") or keywords
        detail_parts = [papers_text, abstract_text]
        if is_expansion:
            source_slug = source_paper.get("source_slug") or "未知来源"
            expansion_source = entry.get("expansion_source") or "related-search"
            source_kw = entry.get("source_matched_kw") or "未记录命中词"
            detail_parts.extend([f"来源：{source_slug}", f"方式：{expansion_source}"])
            detail_parts.append(f"命中词：{source_kw}")
        else:
            detail_parts.append(f"范围：{venues}")
        primary_href = entry["site_url"] or entry["csv_url"] or entry["json_url"] or entry["search_url"]
        links = [
            f'<a href="{entry["csv_url"]}">CSV</a>' if entry["csv_url"] else "",
        ]
        subtitle = f'<div class="meta">{keywords}</div>' if is_expansion and keywords else ""
        return f"""
        <article class="entry" data-matched-kw="{entry.get("source_matched_kw", "").lower()}">
          <div class="dot"></div>
          <div class="card">
            <div class="row">
              <div class="date">{entry["date"]}</div>
              <div class="slug">{entry["slug"]}</div>
            </div>
            <div class="meta">{' · '.join(detail_parts)}</div>
            <h2><a class="title-link" href="{primary_href}">{title}</a></h2>
            {subtitle}
            <div class="links">{' '.join(link for link in links if link)}</div>
          </div>
        </article>
        """

    for entry in entries:
        if entry.get("is_expansion"):
            source_kw = (entry.get("source_matched_kw") or "").strip()
            if source_kw and source_kw.lower() not in seen_expansion_tags:
                seen_expansion_tags.add(source_kw.lower())
                expansion_tags.append(source_kw)
            expansion_items_html.append(render_entry(entry))
        else:
            original_items_html.append(render_entry(entry))

    original_body = "\n".join(original_items_html) if original_items_html else '<div class="empty">还没有原始搜索结果。</div>'
    expansion_body = "\n".join(expansion_items_html) if expansion_items_html else '<div class="empty">还没有延展搜索结果。</div>'
    auth_text = "已启用密码保护" if require_password() else "未启用密码保护"
    return f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Search Timeline</title>
  <style>
    :root {{
      --bg: #f2efe8;
      --panel: rgba(255, 251, 244, 0.94);
      --ink: #1e1d1a;
      --muted: #6f685c;
      --line: #d5cbba;
      --accent: #9c4f2f;
      --accent-soft: #ead8ca;
    }}
    * {{ box-sizing: border-box; }}
    body {{
      margin: 0;
      color: var(--ink);
      font-family: Georgia, "Noto Serif SC", serif;
      background:
        linear-gradient(180deg, rgba(255,255,255,0.5), transparent 30%),
        radial-gradient(circle at top left, #ece1d0 0, transparent 28rem),
        var(--bg);
    }}
    .wrap {{ max-width: 1100px; margin: 0 auto; padding: 28px 18px 72px; }}
    .hero {{
      border: 1px solid var(--line);
      background: var(--panel);
      border-radius: 28px;
      padding: 28px;
      box-shadow: 0 18px 40px rgba(76, 50, 28, 0.08);
    }}
    .hero-bar {{ display:flex; justify-content:space-between; gap:12px; flex-wrap:wrap; align-items:flex-start; }}
    h1 {{ margin: 0 0 8px; font-size: clamp(30px, 5vw, 56px); line-height: 1; }}
    .sub {{ color: var(--muted); line-height: 1.7; font-size: 15px; }}
    .hero-links {{ display:flex; gap:10px; flex-wrap:wrap; }}
    .hero-links a, .hero-links button {{
      color:white; background:var(--accent); text-decoration:none; padding:10px 14px;
      border:none; border-radius:999px; font:inherit; cursor:pointer;
    }}
    .section {{ margin-top: 26px; }}
    .section-title {{ margin: 0 0 12px; font-size: 28px; }}
    .filters {{ display:flex; gap:10px; flex-wrap:wrap; margin: 10px 0 0; }}
    .filters .tag {{
      border:none; border-radius:999px; background:var(--accent-soft); color:var(--muted);
      padding:8px 12px; cursor:pointer; font:inherit;
    }}
    .filters .tag.active {{ background: var(--accent); color: white; }}
    .timeline {{ position: relative; margin-top: 26px; padding-left: 26px; }}
    .timeline::before {{
      content: ""; position: absolute; left: 9px; top: 8px; bottom: 8px; width: 2px;
      background: linear-gradient(180deg, var(--accent), #c8b09c);
    }}
    .entry {{ position: relative; margin: 0 0 18px; }}
    .dot {{
      position: absolute; left: -1px; top: 20px; width: 20px; height: 20px; border-radius: 999px;
      border: 2px solid var(--accent); background: var(--accent-soft);
      box-shadow: 0 0 0 5px rgba(156, 79, 47, 0.08);
    }}
    .card {{
      margin-left: 28px; border: 1px solid var(--line); background: var(--panel); border-radius: 22px;
      padding: 18px 18px 16px; box-shadow: 0 10px 24px rgba(76, 50, 28, 0.06);
    }}
    .row {{ display: flex; gap: 12px; flex-wrap: wrap; align-items: baseline; }}
    .date {{ color: var(--accent); font-weight: 700; letter-spacing: 0.04em; }}
    .slug {{ color: var(--muted); font-size: 14px; }}
    h2 {{ margin: 10px 0 8px; font-size: 23px; line-height: 1.28; }}
    .title-link {{
      color: var(--ink);
      background: none;
      padding: 0;
      border-radius: 0;
      text-decoration: none;
    }}
    .title-link:hover {{ color: var(--accent); }}
    .meta {{ color: var(--muted); font-size: 14px; line-height: 1.7; }}
    .links {{ margin-top: 14px; display: flex; gap: 12px; flex-wrap: wrap; }}
    a {{ color: white; background: var(--accent); text-decoration: none; padding: 10px 14px; border-radius: 999px; }}
    .empty {{
      border: 1px dashed var(--line); border-radius: 18px; background: rgba(255,255,255,0.55);
      text-align: center; padding: 24px; color: var(--muted);
    }}
    @media (max-width: 720px) {{
      .wrap {{ padding: 22px 14px 56px; }}
      .hero {{ padding: 22px 18px; }}
      .hero-links {{ width: 100%; }}
      .hero-links a, .hero-links button {{ width: 100%; text-align: center; }}
      .section-title {{ font-size: 24px; }}
      .filters .tag {{ width: 100%; text-align: center; }}
      .timeline {{ padding-left: 18px; }}
      .card {{ margin-left: 20px; }}
      h2 {{ font-size: 20px; }}
    }}
  </style>
</head>
<body>
  <main class="wrap">
    <section class="hero">
      <div class="hero-bar">
        <div>
          <h1>Search Timeline</h1>
          <div class="sub">这里汇总了每一次论文搜索结果。按时间倒序排列，优先进入对应搜索的网站页，也可以直接打开 CSV、JSON 和原始参数。</div>
          <div class="sub">站点状态：{auth_text}</div>
        </div>
        <div class="hero-links">
          <a href="/keywords">Keywords</a>
          <a href="/reading">深度阅读</a>
          <button id="logout-btn" type="button">退出登录</button>
        </div>
      </div>
    </section>
    <section class="section">
      <h2 class="section-title">原始搜索</h2>
      <div class="timeline">{original_body}</div>
    </section>
    <section class="section">
      <h2 class="section-title">延展搜索</h2>
      <div class="filters" id="expansion-filters">
        <button class="tag active" type="button" data-expansion-filter="all">全部</button>
        {"".join(f'<button class="tag" type="button" data-expansion-filter="{tag.lower()}">{tag}</button>' for tag in expansion_tags)}
      </div>
      <div class="timeline" id="expansion-timeline">{expansion_body}</div>
    </section>
  </main>
  <script>
    const btn = document.getElementById('logout-btn');
    if (btn) {{
      btn.addEventListener('click', async () => {{
        await fetch('/api/auth/logout', {{ method: 'POST', credentials: 'same-origin' }});
        window.location.href = '/login';
      }});
    }}
    const expansionFilterButtons = Array.from(document.querySelectorAll('[data-expansion-filter]'));
    const expansionEntries = Array.from(document.querySelectorAll('#expansion-timeline .entry[data-matched-kw]'));
    expansionFilterButtons.forEach((button) => {{
      button.addEventListener('click', () => {{
        const filter = button.dataset.expansionFilter || 'all';
        expansionFilterButtons.forEach((item) => item.classList.toggle('active', item === button));
        expansionEntries.forEach((entry) => {{
          const kw = (entry.dataset.matchedKw || '').trim();
          const visible = filter === 'all' || kw === filter;
          entry.style.display = visible ? '' : 'none';
        }});
      }});
    }});
  </script>
</body>
</html>"""


def build_keywords_html():
    entries = list_keyword_entries()
    cards = []
    for entry in entries:
        cards.append(
            f"""
            <a class="kw-card" href="/keywords/{entry['slug']}">
              <div class="kw-name">{escape(entry['keyword'])}</div>
              <div class="kw-count">{entry['count']} 篇论文</div>
              <div class="muted">最近新增：{escape(entry.get('latest_date') or '未知')}</div>
            </a>
            """
        )
    body = "\n".join(cards) if cards else '<div class="empty">还没有可用的命中词数据。</div>'
    return f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Keywords</title>
  <style>
    body {{ margin:0; font-family: Georgia, "Noto Serif SC", serif; background:#f2efe8; color:#1e1d1a; }}
    .wrap {{ max-width:1040px; margin:0 auto; padding:28px 18px 72px; }}
    .hero, .kw-card, .empty {{
      border:1px solid #d5cbba; border-radius:24px; background:rgba(255,251,244,0.96);
      box-shadow:0 18px 40px rgba(76,50,28,0.08);
    }}
    .hero {{ padding:28px; margin-bottom:20px; }}
    .row {{ display:flex; justify-content:space-between; gap:12px; flex-wrap:wrap; }}
    .actions {{ display:flex; gap:10px; flex-wrap:wrap; margin-top:14px; }}
    .actions a {{
      display:inline-block; background:#9c4f2f; color:white; text-decoration:none;
      padding:10px 14px; border-radius:999px;
    }}
    h1 {{ margin:0 0 8px; font-size:40px; }}
    .muted {{ color:#6f685c; line-height:1.7; font-size:14px; }}
    .grid {{ display:grid; grid-template-columns:repeat(auto-fill, minmax(220px, 1fr)); gap:16px; }}
    .kw-card {{ padding:18px; text-decoration:none; color:inherit; }}
    .kw-card:hover {{ transform:translateY(-2px); transition:transform .18s ease; }}
    .kw-name {{ font-size:24px; line-height:1.25; margin-bottom:10px; }}
    .kw-count {{ color:#9c4f2f; font-weight:700; }}
    .empty {{ padding:24px; text-align:center; }}
    @media (max-width: 720px) {{
      .wrap {{ padding:22px 14px 56px; }}
      .hero {{ padding:22px 18px; }}
      .actions {{ width:100%; }}
      .actions a {{ width:100%; text-align:center; }}
      .grid {{ grid-template-columns:1fr; gap:14px; }}
      .kw-card {{ padding:16px; }}
      .kw-name {{ font-size:20px; }}
    }}
  </style>
</head>
<body>
  <main class="wrap">
    <section class="hero">
      <div class="row">
        <div>
          <h1>Keywords</h1>
          <div class="muted">这里汇总了所有关键词，并按最近新增论文的时间倒序排列。除了原始搜索命中词，也会展示深度阅读分析后回写到文献上的关键词。</div>
        </div>
        <div class="actions">
          <a href="/">返回时间线</a>
          <a href="/reading">深度阅读</a>
        </div>
      </div>
    </section>
    <section class="grid">{body}</section>
  </main>
</body>
</html>"""


def build_keyword_detail_html(keyword: str):
    entry = get_keyword_entry(keyword)
    if not entry:
        return build_keywords_html()
    cards = []
    all_groups = list_reading_groups()
    group_options = "".join(f'<option value="{g["id"]}">{g["name"]}</option>' for g in all_groups)
    payload_json = json.dumps(entry["papers"], ensure_ascii=False).replace("</script>", "<\\/script>")
    for idx, paper in enumerate(entry["papers"], start=1):
        source_kind = paper.get("source_kind") or "search"
        meta_parts = [
            f"CSV #{paper['csv_index']}" if paper.get("csv_index") else "",
            paper.get("venue") or "",
            str(paper.get("year") or ""),
            paper.get("authors") or "",
            f"来源：{'深度阅读' if source_kind == 'deep_reading' else ('搜索 ' + str(paper.get('source_slug') or ''))}",
        ]
        meta_text = " · ".join(part for part in meta_parts if part)
        doi_text = f"DOI: {escape(paper['doi'])}" if paper.get("doi") else ""
        link_html = f'<a href="{paper["url"]}" target="_blank" rel="noreferrer">原文链接</a>' if paper.get("url") else ""
        reading_link_html = (
            f'<a href="{paper["source_site_url"]}" target="_blank" rel="noreferrer">打开阅读页</a>'
            if source_kind == "deep_reading" and paper.get("source_site_url")
            else ""
        )
        cards.append(
            f"""
            <article class="card">
              <h2>{escape(paper['title'])}</h2>
              <div class="meta">{escape(meta_text)}</div>
              <div class="meta">{doi_text}</div>
              <p>{escape(paper.get('content') or '暂无内容')}</p>
              <div class="group-select-row" style="margin:12px 0;">
                <label>选择 Reading Group（可选）:</label>
                <select id="group-select-{idx}" style="margin-left:8px; padding:6px; border-radius:8px; border:1px solid #d5cbba;">
                  <option value="">-- 不选择 --</option>
                  {group_options}
                </select>
              </div>
              <div class="pdf-upload-row" style="margin:12px 0;">
                <label>上传 PDF（可选）:</label>
                <input type="file" id="pdf-input-{idx}" accept=".pdf" style="margin-left:8px;">
              </div>
              <div class="links">
                {link_html}
                {reading_link_html}
                <button class="action" type="button" onclick="addKeywordCitation({idx})">加入深度阅读</button>
                <button class="action secondary" type="button" onclick="expandKeywordPaper({idx})">扩展搜索</button>
              </div>
            </article>
            """
        )
    body = "\n".join(cards) if cards else '<div class="empty">这个关键词下暂时没有论文。</div>'
    return f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{escape(entry['keyword'])}</title>
  <style>
    body {{ margin:0; font-family: Georgia, "Noto Serif SC", serif; background:#f2efe8; color:#1e1d1a; }}
    .wrap {{ max-width:1040px; margin:0 auto; padding:28px 18px 72px; }}
    .hero, .card, .empty {{
      border:1px solid #d5cbba; border-radius:24px; background:rgba(255,251,244,0.96);
      box-shadow:0 18px 40px rgba(76,50,28,0.08);
    }}
    .hero {{ padding:28px; margin-bottom:20px; }}
    .row {{ display:flex; justify-content:space-between; gap:12px; flex-wrap:wrap; }}
    .actions {{ display:flex; gap:10px; flex-wrap:wrap; margin-top:14px; }}
    .actions a, .links a {{
      display:inline-block; background:#9c4f2f; color:white; text-decoration:none;
      padding:10px 14px; border-radius:999px;
    }}
    .links button {{
      display:inline-block; background:#9c4f2f; color:white; text-decoration:none;
      padding:10px 14px; border-radius:999px; border:none; cursor:pointer; font:inherit;
    }}
    .links button.secondary {{ background:#6f6455; }}
    h1 {{ margin:0 0 8px; font-size:40px; }}
    h2 {{ margin:0 0 8px; font-size:24px; line-height:1.3; }}
    .muted, .meta {{ color:#6f685c; line-height:1.7; font-size:14px; }}
    .list {{ display:grid; gap:16px; }}
    .card {{ padding:18px; }}
    p {{ line-height:1.8; }}
    .links {{ display:flex; gap:10px; flex-wrap:wrap; margin-top:14px; }}
    .empty {{ padding:24px; text-align:center; }}
    @media (max-width: 720px) {{
      .wrap {{ padding:22px 14px 56px; }}
      .hero {{ padding:22px 18px; }}
      .actions {{ width:100%; }}
      .actions a {{ width:100%; text-align:center; }}
      .card {{ padding:16px; }}
      .links a, .links button {{ width:100%; text-align:center; }}
      h1 {{ font-size:32px; }}
      h2 {{ font-size:20px; }}
    }}
  </style>
</head>
<body>
  <main class="wrap">
    <section class="hero">
      <div class="row">
        <div>
          <h1>{escape(entry['keyword'])}</h1>
          <div class="muted">共 {entry['count']} 篇论文。这里同时汇总原始搜索命中词，以及深度阅读分析后回写到文献上的关键词。</div>
        </div>
        <div class="actions">
          <a href="/keywords">返回 Keywords</a>
          <a href="/">返回时间线</a>
        </div>
      </div>
    </section>
    <section class="list">{body}</section>
  </main>
  <script id="keyword-papers" type="application/json">{payload_json}</script>
  <script>
    const keywordPapers = JSON.parse(document.getElementById('keyword-papers').textContent);
    let readingGroups = [];

    function escapeHtml(value) {{
      return (value || '').toString()
        .replaceAll('&', '&amp;')
        .replaceAll('<', '&lt;')
        .replaceAll('>', '&gt;')
        .replaceAll('"', '&quot;');
    }}

    function ensureCitationDialog() {{
      let dialog = document.getElementById('citation-dialog');
      if (dialog) return dialog;
      dialog = document.createElement('dialog');
      dialog.id = 'citation-dialog';
      dialog.style.maxWidth = '560px';
      dialog.style.width = 'calc(100vw - 24px)';
      dialog.style.border = '1px solid #d5cbba';
      dialog.style.borderRadius = '18px';
      dialog.style.padding = '0';
      dialog.innerHTML = `
        <form method="dialog" id="citation-form" style="padding:20px;">
          <h3 style="margin:0 0 12px; font-size:24px;">加入深度阅读</h3>
          <div id="citation-dialog-title" style="color:#6f685c; line-height:1.6; margin-bottom:14px;"></div>
          <label style="display:block; margin-bottom:10px;">
            <div style="margin-bottom:6px; color:#6f685c;">Reading Group</div>
            <select id="citation-group-select" style="width:100%; padding:10px 12px; border-radius:12px; border:1px solid #d5cbba;">
              <option value="">暂不加入 Group</option>
            </select>
          </label>
          <label style="display:block; margin-bottom:14px;">
            <div style="margin-bottom:6px; color:#6f685c;">上传 PDF（可选）</div>
            <input id="citation-pdf-input" type="file" accept="application/pdf,.pdf" style="width:100%;">
          </label>
          <div style="display:flex; gap:10px; flex-wrap:wrap;">
            <button id="citation-submit" type="submit" value="submit" style="border:none; background:#9c4f2f; color:white; padding:10px 14px; border-radius:999px; cursor:pointer;">保存</button>
            <button type="submit" value="cancel" style="border:none; background:#6f6455; color:white; padding:10px 14px; border-radius:999px; cursor:pointer;">取消</button>
          </div>
        </form>
      `;
      document.body.appendChild(dialog);
      return dialog;
    }}

    async function loadReadingGroups() {{
      const resp = await fetch('/api/reading-groups', {{ credentials: 'same-origin' }});
      const data = await resp.json().catch(() => ({{ ok: false, groups: [] }}));
      readingGroups = data.ok ? (data.groups || []) : [];
    }}

    async function submitCitation(searchSlug, paper) {{
      const dialog = ensureCitationDialog();
      document.getElementById('citation-dialog-title').textContent = paper.title || '';
      const select = document.getElementById('citation-group-select');
      const fileInput = document.getElementById('citation-pdf-input');
      select.innerHTML = '<option value="">暂不加入 Group</option>' + readingGroups.map(
        (group) => `<option value="${{group.id}}">${{escapeHtml(group.name)}}</option>`
      ).join('');
      fileInput.value = '';
      const result = await new Promise((resolve) => {{
        const form = document.getElementById('citation-form');
        const handler = async (event) => {{
          event.preventDefault();
          const submitterValue = event.submitter && event.submitter.value;
          form.removeEventListener('submit', handler);
          if (submitterValue !== 'submit') {{
            dialog.close();
            resolve(null);
            return;
          }}
          const formData = new FormData();
          formData.append('search_slug', searchSlug || '');
          formData.append('paper', JSON.stringify(paper));
          if (select.value) formData.append('group_id', select.value);
          if (fileInput.files[0]) formData.append('pdf', fileInput.files[0]);
          const resp = await fetch('/api/citations', {{
            method: 'POST',
            credentials: 'same-origin',
            body: formData
          }});
          const data = await resp.json().catch(() => ({{ ok: false, error: '请求失败' }}));
          dialog.close();
          if (!resp.ok || data.ok === false) {{
            resolve({{ error: data.error || '请求失败' }});
            return;
          }}
          resolve(data);
        }};
        form.addEventListener('submit', handler);
        dialog.showModal();
      }});
      return result;
    }}

    async function apiPost(path, body) {{
      const resp = await fetch(path, {{
        method: 'POST',
        credentials: 'same-origin',
        headers: {{ 'Content-Type': 'application/json' }},
        body: JSON.stringify(body)
      }});
      const data = await resp.json().catch(() => ({{ ok: false, error: '请求失败' }}));
      if (!resp.ok || data.ok === false) {{
        throw new Error(data.error || '请求失败');
      }}
      return data;
    }}

    async function addKeywordCitation(index) {{
      const paper = keywordPapers[index - 1];
      if (!paper) return;
      try {{
        if (!readingGroups.length) {{
          await loadReadingGroups();
        }}
        const data = await submitCitation(paper.source_slug || '', paper);
        if (!data) return;
        if (data.error) throw new Error(data.error);
        if (data.reading_url) {{
          window.open(data.reading_url, '_blank', 'noopener');
        }}
        alert(data.message || '已加入深度阅读。');
      }} catch (error) {{
        alert(error.message);
      }}
    }}

    async function expandKeywordPaper(index) {{
      const paper = keywordPapers[index - 1];
      if (!paper) return;
      try {{
        const data = await apiPost('/api/papers/expand-references', {{
          search_slug: paper.source_slug || '',
          paper
        }});
        window.open(data.site_url, '_blank', 'noopener');
      }} catch (error) {{
        alert(error.message);
      }}
    }}

    window.addKeywordCitation = addKeywordCitation;
    window.expandKeywordPaper = expandKeywordPaper;
    loadReadingGroups().catch(() => {{}});
  </script>
</body>
</html>"""


def build_login_html(error: str = ""):
    error_html = f'<div class="error">{error}</div>' if error else ""
    return f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Login</title>
  <style>
    body {{
      margin:0; min-height:100vh; display:grid; place-items:center;
      font-family: Georgia, "Noto Serif SC", serif;
      background: radial-gradient(circle at top left, #ead8ca 0, transparent 24rem), #f2efe8;
      color:#221f1b;
    }}
    .card {{
      width:min(92vw, 420px); padding:28px; border-radius:24px; background:rgba(255,251,244,0.96);
      border:1px solid #d5cbba; box-shadow:0 18px 40px rgba(76,50,28,0.08);
    }}
    h1 {{ margin:0 0 10px; font-size:34px; }}
    p {{ color:#6f685c; line-height:1.7; }}
    input, button {{
      width:100%; font:inherit; padding:14px 16px; border-radius:16px; box-sizing:border-box;
    }}
    input {{ border:1px solid #d5cbba; margin-top:8px; background:white; }}
    button {{
      margin-top:14px; border:none; background:#9c4f2f; color:white; cursor:pointer;
    }}
    .error {{ margin-top:10px; color:#a12d2d; font-size:14px; }}
  </style>
</head>
<body>
  <form class="card" method="post" action="/api/auth/login">
    <h1>Private Site</h1>
    <p>这个站点需要密码才能访问搜索结果、深度阅读模块和扩展引用功能。</p>
    <input name="password" type="password" placeholder="输入站点密码" autocomplete="current-password" required>
    <button type="submit">登录</button>
    {error_html}
  </form>
</body>
</html>"""


def build_reading_detail_html(paper_id: str):
    bundle = load_reading_bundle(paper_id)
    if not bundle:
        return "<!doctype html><html lang='zh-CN'><body><h1>未找到阅读页</h1></body></html>"
    paper = bundle["paper"]
    analysis = bundle["analysis"]
    modules = analysis.get("modules") or {}
    overview = (modules.get("overview") or {}).get("data") or {}
    problem = (modules.get("problem") or {}).get("data") or {}
    method = (modules.get("method") or {}).get("data") or {}
    results = (modules.get("results") or {}).get("data") or {}
    critique = (modules.get("critique") or {}).get("data") or {}
    qa_history = bundle.get("qa_history") or []
    notes = bundle.get("notes") or {}

    def render_list(items, empty="等待分析生成"):
        values = items or []
        return "".join(f"<li>{escape(str(item))}</li>" for item in values) or f"<li>{empty}</li>"

    logic = "".join(
        f"<li><strong>{escape(str(item.get('step', '')))}. {escape(item.get('label') or '')}</strong><div>{escape(item.get('content') or '')}</div></li>"
        for item in (problem.get("paper_logic") or [])
    ) or "<li>等待分析生成</li>"
    findings = "".join(
        f"<li><strong>{escape(item.get('id') or '')}</strong> {escape(item.get('claim') or '')}<div>{escape(item.get('evidence') or '')}</div></li>"
        for item in (results.get("findings") or [])
    ) or "<li>等待分析生成</li>"
    qa_history_html = "".join(
        f"""
        <article class="qa-item" data-qa-id="{escape(item.get("id") or "")}">
          <div class="qa-meta">{escape(item.get("created_at") or "")}</div>
          <div class="qa-q"><strong>问：</strong>{escape(item.get("question") or "")}</div>
          <div class="qa-a"><strong>答：</strong>{escape(item.get("answer") or "").replace(chr(10), "<br>")}</div>
          <div class="qa-toolbar"><button class="delete-qa" type="button" data-qa-id="{escape(item.get("id") or "")}">删除这条提问</button></div>
        </article>
        """
        for item in reversed(qa_history)
    ) or '<div class="meta" id="qa-empty">还没有提问记录，先问一个你关心的问题吧。</div>'
    pdf_path = ((paper.get("pdf") or {}).get("file_path") or "").strip()
    pdf_link = f'<a href="{escape(pdf_path)}" target="_blank" rel="noreferrer">打开 PDF</a>' if pdf_path else ""
    metadata_status = ((paper.get("status") or {}).get("metadata") or "pending").strip()
    metadata_message = (paper.get("status") or {}).get("metadata_message") or ""
    analysis_status = ((paper.get("status") or {}).get("analysis") or "pending").strip()
    analysis_progress = int(((paper.get("status") or {}).get("analysis_progress") or 0) or 0)
    analysis_message = (paper.get("status") or {}).get("analysis_message") or ""
    has_analysis_content = any(
        bool(((modules.get(name) or {}).get("data") or {}))
        for name in ("overview", "problem", "method", "results", "critique")
    )
    analyze_label = "重新分析" if analysis_status == "completed" or has_analysis_content else "开始分析"

    return f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{escape(paper.get("title") or "Deep Reading")}</title>
  <style>
    body {{ margin:0; font-family: Georgia, "Noto Serif SC", serif; background:#f3efe7; color:#1f1c18; }}
    .wrap {{ max-width:1080px; margin:0 auto; padding:28px 18px 72px; }}
    .hero, .section {{ border:1px solid #d5cbba; border-radius:24px; background:rgba(255,251,244,0.96); box-shadow:0 18px 40px rgba(76,50,28,0.08); }}
    .hero {{ padding:28px; margin-bottom:18px; }}
    .section {{ padding:22px; margin-top:16px; }}
    .actions, .meta, .grid {{ display:flex; gap:10px; flex-wrap:wrap; }}
    .actions a {{ display:inline-block; background:#9c4f2f; color:white; text-decoration:none; padding:10px 14px; border-radius:999px; }}
    .meta {{ color:#6f685c; line-height:1.8; font-size:14px; margin-top:8px; }}
    .progress-shell {{ margin-top:14px; border:1px solid #e3d8c8; border-radius:16px; padding:12px 14px; background:#fffdfa; }}
    .progress-row {{ display:flex; justify-content:space-between; gap:12px; align-items:center; flex-wrap:wrap; }}
    .progress-track {{ width:100%; height:10px; border-radius:999px; background:#ead8ca; overflow:hidden; margin-top:10px; }}
    .progress-bar {{ height:100%; width:0%; background:linear-gradient(90deg, #c8733f, #9c4f2f); }}
    .grid.cards {{ display:grid; grid-template-columns:repeat(auto-fit, minmax(240px, 1fr)); gap:12px; }}
    .card {{ border:1px solid #e3d8c8; border-radius:18px; padding:14px; background:#fffdfa; }}
    .qa-form {{ display:grid; gap:10px; }}
    .qa-form textarea {{ width:100%; min-height:110px; resize:vertical; border:1px solid #d5cbba; border-radius:16px; padding:12px 14px; font:inherit; background:#fffdfa; }}
    .qa-actions {{ display:flex; gap:10px; align-items:center; flex-wrap:wrap; }}
    .qa-actions button {{ display:inline-block; background:#9c4f2f; color:white; text-decoration:none; padding:10px 14px; border-radius:999px; border:none; font:inherit; cursor:pointer; }}
    .qa-list {{ display:grid; gap:12px; margin-top:16px; }}
    .qa-item {{ border:1px solid #e3d8c8; border-radius:18px; padding:14px; background:#fffdfa; }}
    .qa-meta {{ color:#8a7d6a; font-size:13px; margin-bottom:8px; }}
    .qa-q, .qa-a {{ line-height:1.8; }}
    .qa-a {{ margin-top:8px; }}
    .qa-toolbar {{ margin-top:10px; }}
    .qa-toolbar button, .note-box button {{ border:none; background:#6f6455; color:white; padding:8px 12px; border-radius:999px; cursor:pointer; font:inherit; }}
    .note-box {{ margin-top:14px; border:1px solid #e3d8c8; border-radius:18px; padding:14px; background:#fffdfa; }}
    .note-box textarea {{ width:100%; min-height:110px; resize:vertical; border:1px solid #d5cbba; border-radius:14px; padding:12px 14px; font:inherit; background:white; }}
    .note-toolbar {{ display:flex; gap:10px; align-items:center; flex-wrap:wrap; margin-top:10px; }}
    .cols {{ display:grid; grid-template-columns:1fr 1fr; gap:14px; }}
    h1 {{ margin:0 0 8px; font-size:42px; line-height:1.1; }}
    h2 {{ margin:0 0 10px; font-size:28px; }}
    h3 {{ margin:0 0 8px; font-size:20px; }}
    p, li {{ line-height:1.8; }}
    ul {{ margin:0; padding-left:20px; }}
    @media (max-width: 720px) {{ .cols {{ grid-template-columns:1fr; }} .actions a {{ width:100%; text-align:center; }} h1 {{ font-size:32px; }} }}
  </style>
</head>
<body>
  <main class="wrap">
    <section class="hero">
      <div class="actions"><a href="/reading">返回深度阅读</a>{pdf_link}<a href="#" id="run-analysis">{analyze_label}</a></div>
      <h1>{escape(paper.get("title") or "Untitled Paper")}</h1>
      <div class="meta">{escape(", ".join(paper.get("authors") or []) or "未知作者")} · {escape(str(paper.get("venue") or "未知 venue"))} · {escape(str(paper.get("year") or "未知年份"))}</div>
      <div class="meta">Theme: {escape(overview.get("research_theme") or "待生成")} · DOI: {escape(paper.get("doi") or "无")} · Analysis: {escape(analysis_status)}</div>
      <div class="progress-shell" id="metadata-progress-shell">
        <div class="progress-row">
          <strong>元数据识别</strong>
          <span id="metadata-stage">{escape(metadata_status)}</span>
        </div>
        <div class="meta" id="metadata-message">{escape(metadata_message or "等待元数据识别。")}</div>
      </div>
      <div class="progress-shell" id="analysis-progress-shell">
        <div class="progress-row">
          <strong id="analysis-stage">{escape(analysis_status)}</strong>
          <span id="analysis-percent">{analysis_progress}%</span>
        </div>
        <div class="meta" id="analysis-message">{escape(analysis_message or "准备开始分析。")}</div>
        <div class="progress-track"><div class="progress-bar" id="analysis-progress-bar" style="width:{analysis_progress}%;"></div></div>
      </div>
    </section>
    <section class="section">
      <h2>Overview</h2>
      <div class="grid cards">
        <div class="card"><h3>Paper Type</h3><p>{escape(overview.get("paper_type") or "等待分析生成")}</p></div>
        <div class="card"><h3>Core Problem</h3><p>{escape(overview.get("core_problem") or "等待分析生成")}</p></div>
        <div class="card"><h3>Core Approach</h3><p>{escape(overview.get("core_approach") or "等待分析生成")}</p></div>
      </div>
      <div class="card" style="margin-top:12px;"><h3>Contributions</h3><ul>{render_list(overview.get("contributions"))}</ul></div>
      <div class="note-box">
        <h3>Overview Notes</h3>
        <textarea class="module-note-input" data-module="overview" placeholder="手工记录你对 Overview 的阅读笔记...">{escape(notes.get("overview") or "")}</textarea>
        <div class="note-toolbar"><button class="save-note" type="button" data-module="overview">保存 Notes</button><span class="meta module-note-status" data-module="overview">手工 Notes 会保存在当前论文下。</span></div>
      </div>
    </section>
    <section class="section">
      <h2>Problem</h2>
      <div class="grid cards">
        <div class="card"><h3>Background</h3><p>{escape(problem.get("background") or "等待分析生成")}</p></div>
        <div class="card"><h3>Gap</h3><p>{escape(problem.get("gap") or "等待分析生成")}</p></div>
        <div class="card"><h3>Importance</h3><p>{escape(problem.get("importance") or "等待分析生成")}</p></div>
        <div class="card"><h3>Goal</h3><p>{escape(problem.get("research_goal") or "等待分析生成")}</p></div>
      </div>
      <div class="card" style="margin-top:12px;"><h3>Paper Logic</h3><ul>{logic}</ul></div>
      <div class="note-box">
        <h3>Problem Notes</h3>
        <textarea class="module-note-input" data-module="problem" placeholder="手工记录你对 Problem 的阅读笔记...">{escape(notes.get("problem") or "")}</textarea>
        <div class="note-toolbar"><button class="save-note" type="button" data-module="problem">保存 Notes</button><span class="meta module-note-status" data-module="problem">手工 Notes 会保存在当前论文下。</span></div>
      </div>
    </section>
    <section class="section">
      <h2>Method</h2>
      <div class="grid cards">
        <div class="card"><h3>Object</h3><p>{escape(method.get("object_of_study") or "等待分析生成")}</p></div>
        <div class="card"><h3>Method Goal</h3><p>{escape(method.get("method_goal") or "等待分析生成")}</p></div>
        <div class="card"><h3>Participants / Data</h3><p>{escape(method.get("participants_or_data") or "等待分析生成")}</p></div>
        <div class="card"><h3>Evaluation</h3><p>{escape(method.get("evaluation_setup") or "等待分析生成")}</p></div>
      </div>
      <div class="card" style="margin-top:12px;"><h3>Pipeline</h3><ul>{render_list(method.get("pipeline"))}</ul></div>
      <div class="note-box">
        <h3>Method Notes</h3>
        <textarea class="module-note-input" data-module="method" placeholder="手工记录你对 Method 的阅读笔记...">{escape(notes.get("method") or "")}</textarea>
        <div class="note-toolbar"><button class="save-note" type="button" data-module="method">保存 Notes</button><span class="meta module-note-status" data-module="method">手工 Notes 会保存在当前论文下。</span></div>
      </div>
    </section>
    <section class="section">
      <h2>Results</h2>
      <div class="card"><h3>Claim-Evidence Match</h3><p>{escape(results.get("claim_evidence_match") or "等待分析生成")}</p></div>
      <div class="card" style="margin-top:12px;"><h3>Findings</h3><ul>{findings}</ul></div>
      <div class="note-box">
        <h3>Results Notes</h3>
        <textarea class="module-note-input" data-module="results" placeholder="手工记录你对 Results 的阅读笔记...">{escape(notes.get("results") or "")}</textarea>
        <div class="note-toolbar"><button class="save-note" type="button" data-module="results">保存 Notes</button><span class="meta module-note-status" data-module="results">手工 Notes 会保存在当前论文下。</span></div>
      </div>
    </section>
    <section class="section">
      <h2>Critique</h2>
      <div class="cols">
        <div class="card"><h3>Strengths</h3><ul>{render_list(critique.get("strengths"))}</ul></div>
        <div class="card"><h3>Limitations</h3><ul>{render_list(critique.get("limitations"))}</ul></div>
      </div>
      <div class="note-box">
        <h3>Critique Notes</h3>
        <textarea class="module-note-input" data-module="critique" placeholder="手工记录你对 Critique 的阅读笔记...">{escape(notes.get("critique") or "")}</textarea>
        <div class="note-toolbar"><button class="save-note" type="button" data-module="critique">保存 Notes</button><span class="meta module-note-status" data-module="critique">手工 Notes 会保存在当前论文下。</span></div>
      </div>
    </section>
    <section class="section">
      <h2>提问</h2>
      <div class="qa-form">
        <textarea id="qa-question" placeholder="比如：这篇论文的方法创新点是什么？实验设计有哪些局限？"></textarea>
        <div class="qa-actions">
          <button id="ask-question" type="button">提交问题</button>
          <span class="meta" id="qa-status">提问内容会保存到当前论文的阅读历史中。</span>
        </div>
      </div>
      <div class="qa-list" id="qa-history">{qa_history_html}</div>
    </section>
  </main>
  <script>
    const runBtn = document.getElementById('run-analysis');
    const progressBar = document.getElementById('analysis-progress-bar');
    const progressPercent = document.getElementById('analysis-percent');
    const progressStage = document.getElementById('analysis-stage');
    const progressMessage = document.getElementById('analysis-message');
    const metadataStage = document.getElementById('metadata-stage');
    const metadataMessage = document.getElementById('metadata-message');
    let pollingTimer = null;
    let lastMetadataState = '{metadata_status}';
    const askBtn = document.getElementById('ask-question');
    const qaQuestion = document.getElementById('qa-question');
    const qaStatus = document.getElementById('qa-status');
    const qaHistory = document.getElementById('qa-history');
    const noteButtons = Array.from(document.querySelectorAll('.save-note'));

    function escapeHtml(value) {{
      return String(value || '')
        .replaceAll('&', '&amp;')
        .replaceAll('<', '&lt;')
        .replaceAll('>', '&gt;')
        .replaceAll('"', '&quot;')
        .replaceAll("'", '&#39;');
    }}

    function appendQaItem(item) {{
      if (!qaHistory) return;
      const empty = document.getElementById('qa-empty');
      if (empty) empty.remove();
      const wrapper = document.createElement('article');
      wrapper.className = 'qa-item';
      wrapper.innerHTML = `
        <div class="qa-meta">${{escapeHtml(item.created_at || '')}}</div>
        <div class="qa-q"><strong>问：</strong>${{escapeHtml(item.question || '')}}</div>
        <div class="qa-a"><strong>答：</strong>${{escapeHtml(item.answer || '').replaceAll('\\n', '<br>')}}</div>
        <div class="qa-toolbar"><button class="delete-qa" type="button" data-qa-id="${{escapeHtml(item.id || '')}}">删除这条提问</button></div>
      `;
      qaHistory.prepend(wrapper);
    }}

    function renderStatus(payload) {{
      if (metadataStage) metadataStage.textContent = payload.metadata || 'pending';
      if (metadataMessage) metadataMessage.textContent = payload.metadata_message || '等待元数据识别。';
      const progress = Number(payload.analysis_progress || 0);
      if (progressBar) progressBar.style.width = progress + '%';
      if (progressPercent) progressPercent.textContent = progress + '%';
      if (progressStage) progressStage.textContent = payload.analysis || 'pending';
      if (progressMessage) progressMessage.textContent = payload.analysis_message || '准备开始分析。';
      if (runBtn) {{
        if (payload.analysis === 'in_progress') {{
          runBtn.textContent = '分析中...';
          runBtn.style.pointerEvents = 'none';
          runBtn.style.opacity = '0.7';
        }} else {{
          runBtn.textContent = payload.analysis === 'completed' ? '重新分析' : '开始分析';
          runBtn.style.pointerEvents = '';
          runBtn.style.opacity = '';
        }}
      }}
    }}

    async function fetchStatus() {{
      const resp = await fetch('/api/reading/{paper_id}/status', {{
        credentials: 'same-origin'
      }});
      const data = await resp.json().catch(() => ({{ ok:false, error:'状态获取失败' }}));
      if (resp.status === 401) {{
        const error = new Error(data.error || 'unauthorized');
        error.code = 'unauthorized';
        throw error;
      }}
      if (!resp.ok || data.ok === false) {{
        throw new Error(data.error || '状态获取失败');
      }}
      return data.status;
    }}

    async function pollStatus() {{
      try {{
        const status = await fetchStatus();
        const previousMetadataState = lastMetadataState;
        lastMetadataState = status.metadata || 'pending';
        renderStatus(status);
        if (previousMetadataState === 'processing' && status.metadata === 'completed') {{
          window.location.reload();
          return;
        }}
        if (status.analysis === 'in_progress' || status.metadata === 'processing') {{
          pollingTimer = window.setTimeout(pollStatus, 2000);
          return;
        }}
        pollingTimer = null;
        if (status.analysis === 'completed') {{
          window.location.reload();
          return;
        }}
        if (status.analysis === 'failed') {{
          alert(status.analysis_message || '分析失败');
        }}
      }} catch (error) {{
        if (error && error.code === 'unauthorized') {{
          window.location.href = '/login';
          return;
        }}
        pollingTimer = window.setTimeout(pollStatus, 3000);
      }}
    }}

    if (runBtn) {{
      runBtn.addEventListener('click', async (event) => {{
        event.preventDefault();
        renderStatus({{ analysis: 'in_progress', analysis_progress: 5, analysis_message: '已提交分析任务，正在排队。' }});
        const resp = await fetch('/api/reading/{paper_id}/analyze', {{
          method: 'POST',
          credentials: 'same-origin'
        }});
        const data = await resp.json().catch(() => ({{ ok:false, error:'分析失败' }}));
        if (!resp.ok || data.ok === false) {{
          alert(data.error || '分析失败');
          renderStatus({{ analysis: '{analysis_status}', analysis_progress: {analysis_progress}, analysis_message: '{escape(analysis_message or "准备开始分析。")}' }});
          return;
        }}
        renderStatus(data.status || {{ analysis: 'in_progress', analysis_progress: 5, analysis_message: '已提交分析任务。' }});
        if (!pollingTimer) pollStatus();
      }});
      if ('{analysis_status}' === 'in_progress' || '{metadata_status}' === 'processing') {{
        pollStatus();
      }}
    }}

    if (askBtn && qaQuestion) {{
      askBtn.addEventListener('click', async () => {{
        const question = qaQuestion.value.trim();
        if (!question) {{
          alert('请先输入问题。');
          return;
        }}
        askBtn.disabled = true;
        askBtn.textContent = '回答中...';
        if (qaStatus) qaStatus.textContent = '正在根据论文内容生成回答...';
        const resp = await fetch('/api/reading/{paper_id}/questions', {{
          method: 'POST',
          credentials: 'same-origin',
          headers: {{ 'Content-Type': 'application/json' }},
          body: JSON.stringify({{ question }})
        }});
        const data = await resp.json().catch(() => ({{ ok:false, error:'提问失败' }}));
        if (!resp.ok || data.ok === false) {{
          alert(data.error || '提问失败');
          askBtn.disabled = false;
          askBtn.textContent = '提交问题';
          if (qaStatus) qaStatus.textContent = '提问内容会保存到当前论文的阅读历史中。';
          return;
        }}
        appendQaItem(data.item || {{}});
        qaQuestion.value = '';
        askBtn.disabled = false;
        askBtn.textContent = '提交问题';
        if (qaStatus) qaStatus.textContent = '回答已保存到提问历史。';
      }});
    }}

    if (qaHistory) {{
      qaHistory.addEventListener('click', async (event) => {{
        const btn = event.target.closest('.delete-qa');
        if (!btn) return;
        const qaId = btn.dataset.qaId || '';
        if (!qaId) return;
        if (!confirm('确定删除这条提问记录吗？')) return;
        const resp = await fetch('/api/reading/{paper_id}/questions/' + encodeURIComponent(qaId), {{
          method: 'DELETE',
          credentials: 'same-origin'
        }});
        const data = await resp.json().catch(() => ({{ ok:false, error:'删除失败' }}));
        if (!resp.ok || data.ok === false) {{
          alert(data.error || '删除失败');
          return;
        }}
        const item = btn.closest('.qa-item');
        if (item) item.remove();
        if (!qaHistory.querySelector('.qa-item')) {{
          qaHistory.innerHTML = '<div class="meta" id="qa-empty">还没有提问记录，先问一个你关心的问题吧。</div>';
        }}
      }});
    }}

    noteButtons.forEach((button) => {{
      button.addEventListener('click', async () => {{
        const moduleName = button.dataset.module || '';
        const input = document.querySelector('.module-note-input[data-module="' + moduleName + '"]');
        const status = document.querySelector('.module-note-status[data-module="' + moduleName + '"]');
        const content = input ? input.value : '';
        button.disabled = true;
        button.textContent = '保存中...';
        if (status) status.textContent = '正在保存手工 Notes...';
        const resp = await fetch('/api/reading/{paper_id}/notes', {{
          method: 'POST',
          credentials: 'same-origin',
          headers: {{ 'Content-Type': 'application/json' }},
          body: JSON.stringify({{ module: moduleName, content }})
        }});
        const data = await resp.json().catch(() => ({{ ok:false, error:'保存失败' }}));
        if (!resp.ok || data.ok === false) {{
          alert(data.error || '保存失败');
          button.disabled = false;
          button.textContent = '保存 Notes';
          if (status) status.textContent = '手工 Notes 会保存在当前论文下。';
          return;
        }}
        button.disabled = false;
        button.textContent = '保存 Notes';
        if (status) status.textContent = 'Notes 已保存。';
      }});
    }});
  </script>
</body>
</html>"""


def build_library_html():
    items = list_citations()
    all_groups = list_reading_groups()
    all_tags = []
    seen_tags = set()
    for item in items:
        for tag in [part.strip() for part in (item.get("tags") or "").split(",") if part.strip()]:
            low = tag.lower()
            if low in seen_tags:
                continue
            seen_tags.add(low)
            all_tags.append(tag)
    filter_html = "".join(
        f'<button class="tag" type="button" data-filter="{tag.lower()}">{tag}</button>'
        for tag in all_tags
    )
    group_filter_html = "".join(
        f'<button class="tag" type="button" data-group-filter="{g["id"]}">{escape(g["name"])}</button>'
        for g in all_groups
    )
    upload_group_options = "".join(f'<option value="{g["id"]}">{g["name"]}</option>' for g in all_groups)
    cards = []
    for item in items:
        doi_text = f"DOI: {item['doi']}" if item.get("doi") else "无 DOI"
        url_html = f'<a class="action-link action-link-secondary" href="{item["url"]}" target="_blank" rel="noreferrer">原文链接</a>' if item.get("url") else ""
        has_pdf = citation_has_pdf(item)
        reading_ready = bool(item.get("reading_paper_id")) and reading_json_ready(item.get("reading_paper_id"))
        upload_label = "更新 PDF" if has_pdf else "上传 PDF"
        if has_pdf:
            reading_label = "打开深度阅读" if reading_ready else "生成深度阅读"
            reading_button = f'<button class="deep-reading-link action-link action-link-primary" type="button" data-id="{item["id"]}" data-paper-id="{item.get("reading_paper_id") or ""}" data-ready="{str(reading_ready).lower()}">{reading_label}</button>'
            reading_hint = "已绑定 PDF，可直接进入深度阅读"
        else:
            reading_button = ""
            reading_hint = "上传 PDF 后可进入深度阅读"
        groups = get_citation_groups(item["id"])
        group_badges = "".join(f'<span class="group-badge" data-group-id="{g["id"]}">{g["name"]}</span>' for g in groups) or '<span class="muted">未加入任何 Group</span>'
        group_ids = ",".join(str(g["id"]) for g in groups)
        group_options = "".join(f'<option value="{g["id"]}">{g["name"]}</option>' for g in all_groups)
        tags = [part.strip() for part in (item.get("tags") or "").split(",") if part.strip()]
        tag_badges = "".join(f'<button class="tag" type="button" data-filter-tag="{tag}">{tag}</button>' for tag in tags) or '<span class="muted">无 tags</span>'
        cards.append(
            f"""
            <article class="card" data-tags="{(item.get("tags") or "").lower()}" data-group-ids="{group_ids}" data-citation-id="{item["id"]}">
              <div class="checkrow">
                <label class="checklabel">
                  <input class="cite-check" type="checkbox" value="{item["id"]}">
                  <span>选择导出</span>
                </label>
              </div>
              <div class="meta">#{item["id"]} · {item["created_at"]}</div>
              <h2>{item["title"]}</h2>
              <div class="meta">{item.get("authors") or "未知作者"} · {item.get("venue") or "未知 venue"} · {item.get("year") or "未知年份"}</div>
              <div class="meta">{doi_text} · 来自搜索：{item.get("source_search_slug") or "未知"}</div>
              <div class="group-row"><strong>Groups: </strong>{group_badges}</div>
              <div class="group-editor" style="display:flex; gap:8px; flex-wrap:wrap; margin:10px 0;">
                <select class="group-select" style="flex:1; padding:8px; border-radius:10px; border:1px solid #d5cbba;">
                  <option value="">选择 Group...</option>
                  {group_options}
                </select>
                <button class="add-to-group" type="button" data-id="{item["id"]}" style="padding:8px 14px; border-radius:10px;">加入</button>
                <button class="remove-from-group" type="button" data-id="{item["id"]}" style="padding:8px 14px; border-radius:10px; background:#6f6455;">移出</button>
              </div>
              <div class="tag-row">{tag_badges}</div>
              <div class="tag-editor">
                <input class="tag-input" type="text" value="{item.get("tags") or ''}" placeholder="输入 tags，逗号分隔">
                <button class="save-tag" type="button" data-id="{item["id"]}">保存 tags</button>
              </div>
              <p>{item.get("abstract") or "暂无摘要"}</p>
              <div class="links">
                {url_html}
                <button class="upload-pdf-link action-link action-link-upload" type="button" data-id="{item["id"]}">{upload_label}</button>
                <input class="upload-pdf-input" type="file" accept="application/pdf,.pdf" style="display:none;">
                {reading_button}
                <button class="remove-reading-link action-link action-link-danger" type="button" data-id="{item["id"]}">删除深度阅读</button>
              </div>
              <div class="links-meta">
                <span class="muted">{reading_hint}</span>
              </div>
              <div class="upload-progress" style="display:none; margin-top:10px;">
                <div class="meta upload-progress-label">准备上传...</div>
                <div style="width:100%; height:8px; border-radius:999px; background:#ead8ca; overflow:hidden; margin-top:6px;">
                  <div class="upload-progress-bar" style="height:100%; width:0%; background:linear-gradient(90deg, #c8733f, #9c4f2f);"></div>
                </div>
              </div>
            </article>
            """
        )
    body = "\n".join(cards) if cards else '<div class="empty">深度阅读模块还是空的，先去搜索结果页加入几篇，或直接上传 PDF 吧。</div>'
    return f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Deep Reading</title>
  <style>
    body {{ margin:0; font-family: Georgia, "Noto Serif SC", serif; background:#f2efe8; color:#1e1d1a; }}
    .wrap {{ max-width:1020px; margin:0 auto; padding:28px 18px 72px; }}
    .hero, .card, .empty {{
      border:1px solid #d5cbba; border-radius:24px; background:rgba(255,251,244,0.96);
      box-shadow:0 18px 40px rgba(76,50,28,0.08);
    }}
    .hero {{ padding:28px; margin-bottom:20px; }}
    .row {{ display:flex; justify-content:space-between; gap:12px; flex-wrap:wrap; }}
    .actions {{ display:flex; gap:10px; flex-wrap:wrap; margin-top:14px; }}
    h1 {{ margin:0 0 8px; font-size:40px; }}
    .muted, .meta {{ color:#6f685c; line-height:1.7; font-size:14px; }}
    .list {{ display:grid; gap:16px; }}
    .card {{ padding:18px; }}
    .checkrow {{ margin-bottom:6px; }}
    .checklabel {{ display:inline-flex; align-items:center; gap:8px; color:#6f685c; font-size:14px; }}
    .filters {{ display:flex; gap:10px; flex-wrap:wrap; margin-top:14px; }}
    .filters .tag, .tag-row .tag {{
      border:none; border-radius:999px; background:#ead8ca; color:#6f685c;
      padding:8px 12px; cursor:pointer; font:inherit;
    }}
    .filters .tag.active {{ background:#9c4f2f; color:white; }}
    .tag-row {{ display:flex; gap:8px; flex-wrap:wrap; margin:10px 0; }}
    .tag-editor {{ display:flex; gap:10px; flex-wrap:wrap; margin:12px 0; }}
    .group-row {{ display:flex; gap:8px; flex-wrap:wrap; margin:10px 0; align-items:center; }}
    .group-badge {{ background:#c8e6c9; color:#2e7d32; padding:4px 10px; border-radius:999px; font-size:13px; }}
    .tag-editor input {{
      flex:1 1 280px; padding:10px 12px; border-radius:14px; border:1px solid #d5cbba; font:inherit;
    }}
    h2 {{ margin:8px 0; font-size:24px; line-height:1.3; }}
    p {{ line-height:1.8; }}
    .hero a, .hero button {{
      display:inline-block; background:#9c4f2f; color:white; text-decoration:none;
      padding:10px 14px; border-radius:999px; border:none; font:inherit; cursor:pointer;
    }}
    .links {{
      display:flex;
      gap:12px;
      flex-wrap:wrap;
      align-items:center;
      margin-top:14px;
    }}
    .links-meta {{
      margin-top:10px;
    }}
    .action-link {{
      display:inline-flex;
      align-items:center;
      justify-content:center;
      min-height:46px;
      padding:12px 18px;
      border-radius:16px;
      border:1px solid transparent;
      font:inherit;
      font-weight:700;
      letter-spacing:0.02em;
      text-decoration:none;
      cursor:pointer;
      transition:transform 0.15s ease, box-shadow 0.15s ease, background 0.15s ease;
    }}
    .action-link:hover {{
      transform:translateY(-1px);
      box-shadow:0 10px 20px rgba(76,50,28,0.12);
    }}
    .action-link-secondary {{
      background:#fffaf3;
      color:#7a4a2a;
      border-color:#d6b89b;
    }}
    .action-link-upload {{
      background:#fff;
      color:#8a4e22;
      border:2px dashed #c8733f;
      box-shadow:inset 0 0 0 1px rgba(200,115,63,0.08);
    }}
    .action-link-primary {{
      background:linear-gradient(135deg, #a6522d, #d06d3b);
      color:white;
      box-shadow:0 14px 28px rgba(156,79,47,0.22);
    }}
    .action-link-danger {{
      background:#fff4f1;
      color:#b33a2f;
      border-color:#e2a49a;
    }}
    .empty {{ padding:24px; text-align:center; }}
    @media (max-width: 720px) {{
      .wrap {{ padding:22px 14px 56px; }}
      .hero {{ padding:22px 18px; }}
      .actions {{ width:100%; }}
      .actions button, .hero a {{ width:100%; text-align:center; }}
      .filters .tag, .tag-row .tag {{ width:100%; text-align:center; }}
      .tag-editor input, .tag-editor button {{ width:100%; }}
      .links .action-link {{ width:100%; text-align:center; }}
      h1 {{ font-size:32px; }}
      h2 {{ font-size:20px; }}
    }}
  </style>
</head>
<body>
  <main class="wrap">
    <section class="hero">
      <div class="row">
        <div>
          <h1>深度阅读</h1>
          <div class="muted">这里保存文献、PDF 与深度阅读入口。你可以从搜索页加入时上传 PDF，也可以在这里上传 PDF 并由系统创建或匹配到数据库文献。当前共 {len(items)} 篇。</div>
          <div class="actions">
            <button id="select-all" type="button">全选 / 取消</button>
            <button id="export-json" type="button">导出所选 JSON</button>
            <button id="manage-groups" type="button">管理 Reading Groups</button>
          </div>
          <div id="reading-upload" style="display:grid; grid-template-columns:repeat(auto-fit, minmax(220px, 1fr)); gap:8px; margin-top:14px; padding:14px; border:1px solid #d5cbba; border-radius:14px; background:#faf8f5;">
            <div style="grid-column:1 / -1; color:#6f685c; font-size:14px;">上传 PDF 后会自动识别标题、作者、Venue、年份与 DOI，并创建或匹配到现有文献。</div>
            <select id="reading-group">
              <option value="">暂不加入 Group</option>
              {upload_group_options}
            </select>
            <input type="file" id="reading-pdf" accept="application/pdf,.pdf">
            <button id="reading-upload-btn" type="button">上传并生成阅读页</button>
            <div id="reading-upload-progress" style="display:none; grid-column:1 / -1;">
              <div class="meta" id="reading-upload-progress-label">准备上传...</div>
              <div style="width:100%; height:8px; border-radius:999px; background:#ead8ca; overflow:hidden; margin-top:6px;">
                <div id="reading-upload-progress-bar" style="height:100%; width:0%; background:linear-gradient(90deg, #c8733f, #9c4f2f);"></div>
              </div>
            </div>
          </div>
          <div id="group-management" style="display:none; margin-top:14px; padding:14px; border:1px solid #d5cbba; border-radius:14px; background:#faf8f5;">
            <div style="font-weight:600; margin-bottom:8px;">Reading Groups</div>
            <div id="group-list" style="margin-bottom:10px;"></div>
            <div style="display:flex; gap:8px; flex-wrap:wrap;">
              <input type="text" id="new-group-name" placeholder="新 Group 名称" style="flex:1; padding:8px 12px; border-radius:10px; border:1px solid #d5cbba;">
              <input type="text" id="new-group-desc" placeholder="描述（可选）" style="flex:2; padding:8px 12px; border-radius:10px; border:1px solid #d5cbba;">
              <button id="create-group" type="button" style="padding:8px 14px; border-radius:10px;">创建</button>
            </div>
          </div>
          <div class="filters">
            <button class="tag active" type="button" data-filter="all">全部</button>
            {filter_html}
          </div>
          <div class="filters" style="margin-top:10px;">
            <button class="tag active" type="button" data-group-filter="all">全部 Group</button>
            {group_filter_html}
          </div>
        </div>
        <a href="/">返回时间线</a>
      </div>
    </section>
    <section class="list">{body}</section>
  </main>
  <script>
    const selectAllBtn = document.getElementById('select-all');
    const exportBtn = document.getElementById('export-json');
    const checks = () => Array.from(document.querySelectorAll('.cite-check'));
    const cards = () => Array.from(document.querySelectorAll('.card'));
    const filterButtons = () => Array.from(document.querySelectorAll('[data-filter]'));
    const groupFilterButtons = () => Array.from(document.querySelectorAll('[data-group-filter]'));
    let activeGroupFilter = 'all';

    if (selectAllBtn) {{
      selectAllBtn.addEventListener('click', () => {{
        const all = checks();
        const shouldSelect = all.some((box) => !box.checked);
        all.forEach((box) => {{
          box.checked = shouldSelect;
        }});
      }});
    }}

    if (exportBtn) {{
      exportBtn.addEventListener('click', async () => {{
        const ids = checks().filter((box) => box.checked).map((box) => Number(box.value));
        if (!ids.length) {{
          alert('请先选择至少一篇论文。');
          return;
        }}
        const resp = await fetch('/api/citations/export', {{
          method: 'POST',
          credentials: 'same-origin',
          headers: {{ 'Content-Type': 'application/json' }},
          body: JSON.stringify({{ ids }})
        }});
        if (!resp.ok) {{
          const data = await resp.json().catch(() => ({{ error: '导出失败' }}));
          alert(data.error || '导出失败');
          return;
        }}
        const blob = await resp.blob();
        const url = URL.createObjectURL(blob);
        const a = document.createElement('a');
        a.href = url;
        a.download = 'citations-export.json';
        document.body.appendChild(a);
        a.click();
        a.remove();
        URL.revokeObjectURL(url);
      }});
    }}

    filterButtons().forEach((btn) => {{
      btn.addEventListener('click', () => {{
        const filter = btn.dataset.filter || 'all';
        filterButtons().forEach((item) => item.classList.toggle('active', item === btn));
        cards().forEach((card) => {{
          const tags = card.dataset.tags || '';
          const groupIds = (card.dataset.groupIds || '').split(',').map((x) => x.trim()).filter(Boolean);
          const tagVisible = filter === 'all' || tags.split(',').map((x) => x.trim()).includes(filter);
          const groupVisible = activeGroupFilter === 'all' || groupIds.includes(activeGroupFilter);
          const visible = tagVisible && groupVisible;
          card.style.display = visible ? '' : 'none';
        }});
      }});
    }});

    groupFilterButtons().forEach((btn) => {{
      btn.addEventListener('click', () => {{
        activeGroupFilter = btn.dataset.groupFilter || 'all';
        groupFilterButtons().forEach((item) => item.classList.toggle('active', item === btn));
        const activeTag = (filterButtons().find((item) => item.classList.contains('active')) || {{ dataset: {{ filter: 'all' }} }}).dataset.filter || 'all';
        cards().forEach((card) => {{
          const tags = card.dataset.tags || '';
          const groupIds = (card.dataset.groupIds || '').split(',').map((x) => x.trim()).filter(Boolean);
          const tagVisible = activeTag === 'all' || tags.split(',').map((x) => x.trim()).includes(activeTag);
          const groupVisible = activeGroupFilter === 'all' || groupIds.includes(activeGroupFilter);
          const visible = tagVisible && groupVisible;
          card.style.display = visible ? '' : 'none';
        }});
      }});
    }});

    document.querySelectorAll('[data-filter-tag]').forEach((btn) => {{
      btn.addEventListener('click', () => {{
        const target = (btn.dataset.filterTag || '').toLowerCase();
        const filterBtn = filterButtons().find((item) => item.dataset.filter === target);
        if (filterBtn) filterBtn.click();
      }});
    }});

    document.querySelectorAll('.save-tag').forEach((btn) => {{
      btn.addEventListener('click', async () => {{
        const card = btn.closest('.card');
        const input = card.querySelector('.tag-input');
        const id = Number(btn.dataset.id);
        const tags = input.value;
        const resp = await fetch('/api/citations/tags', {{
          method: 'POST',
          credentials: 'same-origin',
          headers: {{ 'Content-Type': 'application/json' }},
          body: JSON.stringify({{ id, tags }})
        }});
        const data = await resp.json().catch(() => ({{ ok:false, error:'保存失败' }}));
        if (!resp.ok || data.ok === false) {{
          alert(data.error || '保存失败');
          return;
        }}
        window.location.reload();
      }});
    }});

    // Group management
    const manageGroupsBtn = document.getElementById('manage-groups');
    const groupManagementDiv = document.getElementById('group-management');
    const groupListDiv = document.getElementById('group-list');
    const createGroupBtn = document.getElementById('create-group');
    const newGroupNameInput = document.getElementById('new-group-name');
    const newGroupDescInput = document.getElementById('new-group-desc');
    const readingUploadBtn = document.getElementById('reading-upload-btn');
    const readingUploadProgress = document.getElementById('reading-upload-progress');
    const readingUploadProgressBar = document.getElementById('reading-upload-progress-bar');
    const readingUploadProgressLabel = document.getElementById('reading-upload-progress-label');

    function setProgress(container, bar, label, percent, text) {{
      if (container) container.style.display = 'block';
      if (bar) bar.style.width = Math.max(0, Math.min(100, Number(percent || 0))) + '%';
      if (label) label.textContent = text || '处理中...';
    }}

    function uploadWithProgress(url, formData, onProgress) {{
      return new Promise((resolve, reject) => {{
        const xhr = new XMLHttpRequest();
        xhr.open('POST', url, true);
        xhr.withCredentials = true;
        xhr.upload.addEventListener('progress', (event) => {{
          if (!event.lengthComputable) return;
          const percent = Math.round((event.loaded / event.total) * 100);
          onProgress(percent, percent < 100 ? `正在上传 PDF... ${{percent}}%` : '上传完成，等待服务器处理...');
        }});
        xhr.onload = () => {{
          let data = null;
          try {{
            data = JSON.parse(xhr.responseText || '{{}}');
          }} catch (error) {{
            data = {{ ok: false, error: '响应解析失败' }};
          }}
          if (xhr.status >= 200 && xhr.status < 300 && data.ok !== false) {{
            resolve(data);
            return;
          }}
          reject(new Error((data && data.error) || '上传失败'));
        }};
        xhr.onerror = () => reject(new Error('网络错误，上传失败'));
        xhr.send(formData);
      }});
    }}

    async function loadGroups() {{
      const resp = await fetch('/api/reading-groups', {{ credentials: 'same-origin' }});
      if (!resp.ok) return;
      const data = await resp.json();
      if (!data.ok) return;
      let html = '';
      data.groups.forEach(g => {{
        html += `<div style="display:flex; justify-content:space-between; align-items:center; padding:6px 0; border-bottom:1px solid #e0e0e0;">
          <span><strong>${{g.name}}</strong>${{g.description ? ' - ' + g.description : ''}}</span>
          <button class="delete-group" data-id="${{g.id}}" style="padding:4px 10px; border-radius:8px; font-size:12px; background:#c62828;">删除</button>
        </div>`;
      }});
      groupListDiv.innerHTML = html || '<span class="muted">暂无 Groups</span>';
      document.querySelectorAll('.delete-group').forEach(btn => {{
        btn.addEventListener('click', async () => {{
          const id = btn.dataset.id;
          if (!confirm('确定删除此 Group？其中的文章不会被删除。')) return;
          const resp = await fetch('/api/reading-groups/' + id, {{ method: 'DELETE', credentials: 'same-origin' }});
          if (resp.ok) loadGroups();
        }});
      }});
    }}

    if (manageGroupsBtn) {{
      manageGroupsBtn.addEventListener('click', () => {{
        const visible = groupManagementDiv.style.display !== 'none';
        groupManagementDiv.style.display = visible ? 'none' : 'block';
        if (!visible) loadGroups();
      }});
    }}

    if (createGroupBtn) {{
      createGroupBtn.addEventListener('click', async () => {{
        const name = newGroupNameInput.value.trim();
        const description = newGroupDescInput.value.trim();
        if (!name) {{ alert('请输入 Group 名称'); return; }}
        const resp = await fetch('/api/reading-groups', {{
          method: 'POST',
          credentials: 'same-origin',
          headers: {{ 'Content-Type': 'application/json' }},
          body: JSON.stringify({{ name, description }})
        }});
        if (resp.ok) {{
          newGroupNameInput.value = '';
          newGroupDescInput.value = '';
          loadGroups();
          window.location.reload();
        }}
      }});
    }}

    // Add/remove citation from group
    document.querySelectorAll('.add-to-group').forEach(btn => {{
      btn.addEventListener('click', async () => {{
        const card = btn.closest('.card');
        const select = card.querySelector('.group-select');
        const groupId = select.value;
        if (!groupId) {{ alert('请选择 Group'); return; }}
        const citationId = btn.dataset.id;
        const resp = await fetch('/api/citations/' + citationId + '/groups/' + groupId, {{
          method: 'POST', credentials: 'same-origin'
        }});
        if (resp.ok) window.location.reload();
      }});
    }});

    document.querySelectorAll('.remove-from-group').forEach(btn => {{
      btn.addEventListener('click', async () => {{
        const card = btn.closest('.card');
        const select = card.querySelector('.group-select');
        const groupId = select.value;
        if (!groupId) {{ alert('请选择 Group'); return; }}
        const citationId = btn.dataset.id;
        const resp = await fetch('/api/citations/' + citationId + '/groups/' + groupId, {{
          method: 'DELETE', credentials: 'same-origin'
        }});
        if (resp.ok) window.location.reload();
      }});
    }});

    document.querySelectorAll('.upload-pdf-link').forEach((btn) => {{
      btn.addEventListener('click', () => {{
        const card = btn.closest('.card');
        const input = card.querySelector('.upload-pdf-input');
        if (input) input.click();
      }});
    }});

    document.querySelectorAll('.upload-pdf-input').forEach((input) => {{
      input.addEventListener('change', async () => {{
        const file = input.files && input.files[0];
        if (!file) return;
        const card = input.closest('.card');
        const citationId = card.dataset.citationId;
        const progressBox = card.querySelector('.upload-progress');
        const progressBar = card.querySelector('.upload-progress-bar');
        const progressLabel = card.querySelector('.upload-progress-label');
        const formData = new FormData();
        formData.append('pdf', file);
        try {{
          const data = await uploadWithProgress('/api/citations/' + citationId + '/pdf', formData, (percent, text) => {{
            setProgress(progressBox, progressBar, progressLabel, percent, text);
          }});
          setProgress(progressBox, progressBar, progressLabel, 100, 'PDF 已上传并绑定文献。');
          if (data.reading_url) {{
            window.location.href = data.reading_url;
            return;
          }}
          window.location.reload();
        }} catch (error) {{
          alert(error.message || '上传失败');
          input.value = '';
          return;
        }}
      }});
    }});

    document.querySelectorAll('.deep-reading-link').forEach((btn) => {{
      btn.addEventListener('click', async () => {{
        const ready = (btn.dataset.ready || '') === 'true';
        const paperId = btn.dataset.paperId || '';
        if (ready && paperId) {{
          window.location.href = '/reading/' + encodeURIComponent(paperId);
          return;
        }}
        const citationId = btn.dataset.id;
        const resp = await fetch('/api/citations/' + citationId + '/reading', {{
          method: 'POST',
          credentials: 'same-origin'
        }});
        const data = await resp.json().catch(() => ({{ ok:false, error:'生成失败' }}));
        if (!resp.ok || data.ok === false) {{
          alert(data.error || '生成失败');
          return;
        }}
        window.location.href = data.reading_url;
      }});
    }});

    document.querySelectorAll('.remove-reading-link').forEach((btn) => {{
      btn.addEventListener('click', async () => {{
        const citationId = btn.dataset.id;
        if (!confirm('确定删除这篇深度阅读文献吗？相关的阅读页、分析、提问、Notes、分组关联与独占 PDF 都会一起删除。')) return;
        const resp = await fetch('/api/citations/' + citationId + '/reading', {{
          method: 'DELETE',
          credentials: 'same-origin'
        }});
        const data = await resp.json().catch(() => ({{ ok:false, error:'移除失败' }}));
        if (!resp.ok || data.ok === false) {{
          alert(data.error || '移除失败');
          return;
        }}
        window.location.reload();
      }});
    }});

    if (readingUploadBtn) {{
      readingUploadBtn.addEventListener('click', async () => {{
        const formData = new FormData();
        const groupId = document.getElementById('reading-group').value;
        const pdfFile = document.getElementById('reading-pdf').files[0];
        if (!pdfFile) {{
          alert('请先选择 PDF。');
          return;
        }}
        if (groupId) formData.append('group_id', groupId);
        formData.append('pdf', pdfFile);
        try {{
          const data = await uploadWithProgress('/api/reading/upload', formData, (percent, text) => {{
            setProgress(readingUploadProgress, readingUploadProgressBar, readingUploadProgressLabel, percent, text);
          }});
          setProgress(readingUploadProgress, readingUploadProgressBar, readingUploadProgressLabel, 100, '上传完成，正在跳转阅读页。');
          if (data.reading_url) {{
            window.location.href = data.reading_url;
            return;
          }}
          window.location.reload();
        }} catch (error) {{
          alert(error.message || '上传失败');
          return;
        }}
      }});
    }}
  </script>
</body>
</html>"""


class SearchSiteHandler(SimpleHTTPRequestHandler):
    def translate_path(self, path):
        path = unquote(path.split("?", 1)[0].split("#", 1)[0]).lstrip("/")
        return str(DATA_DIR / path)

    def parse_body(self):
        length = int(self.headers.get("Content-Length", "0") or "0")
        raw = self.rfile.read(length) if length else b""
        content_type = self.headers.get("Content-Type", "")
        if "multipart/form-data" in content_type:
            environ = {
                "REQUEST_METHOD": self.command,
                "CONTENT_TYPE": content_type,
                "CONTENT_LENGTH": str(length),
            }
            form = __import__("cgi").FieldStorage(
                fp=io.BytesIO(raw),
                headers=self.headers,
                environ=environ,
                keep_blank_values=True,
            )
            data = {}
            if getattr(form, "list", None):
                for field in form.list:
                    if field.filename:
                        data[field.name] = field
                    elif field.name in data:
                        current = data[field.name]
                        if isinstance(current, list):
                            current.append(field.value)
                        else:
                            data[field.name] = [current, field.value]
                    else:
                        data[field.name] = field.value
            return data
        if "application/json" in content_type:
            return json.loads(raw.decode("utf-8") or "{}")
        if "application/x-www-form-urlencoded" in content_type:
            text = raw.decode("utf-8")
            pairs = [part.split("=", 1) for part in text.split("&") if "=" in part]
            return {k: unquote(v.replace("+", " ")) for k, v in pairs}
        return {}

    def send_json(self, payload: dict, status: int = 200, extra_headers: dict | None = None):
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        if extra_headers:
            for key, value in extra_headers.items():
                self.send_header(key, value)
        self.end_headers()
        self.wfile.write(data)

    def send_html(self, html: str, status: int = 200, extra_headers: dict | None = None):
        data = html.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        if extra_headers:
            for key, value in extra_headers.items():
                self.send_header(key, value)
        self.end_headers()
        self.wfile.write(data)

    def current_session(self):
        if not require_password():
            return {"id": "no-password", "created_at": time.time()}
        cookie_header = self.headers.get("Cookie", "")
        jar = cookies.SimpleCookie()
        jar.load(cookie_header)
        token = jar.get(SESSION_COOKIE)
        if not token:
            return None
        return SESSIONS.get(token.value)

    def is_authenticated(self) -> bool:
        return self.current_session() is not None

    def reject_unauthorized(self):
        if self.path.startswith("/api/"):
            self.send_json({"ok": False, "error": "unauthorized"}, status=401)
        else:
            self.send_response(302)
            self.send_header("Location", "/login")
            self.end_headers()

    def handle_server_error(self, exc: Exception):
        error_log = Path("/tmp/exscholar-serve-errors.log")
        message = (
            f"[{utc_now()}] {self.command} {self.path}\n"
            f"{traceback.format_exc()}\n"
        )
        try:
            error_log.parent.mkdir(parents=True, exist_ok=True)
            with error_log.open("a", encoding="utf-8") as fh:
                fh.write(message)
        except Exception:
            pass
        if self.path.startswith("/api/"):
            self.send_json({"ok": False, "error": f"server_error: {exc}"}, status=500)
        else:
            self.send_html("<!doctype html><html lang='zh-CN'><body><h1>Server Error</h1></body></html>", status=500)

    def do_GET(self):
        if self.path == "/api/auth/status":
            self.send_json({"ok": True, "authenticated": self.is_authenticated(), "require_password": require_password()})
            return

        if self.path == "/login":
            if self.is_authenticated():
                self.send_response(302)
                self.send_header("Location", "/")
                self.end_headers()
                return
            self.send_html(build_login_html())
            return

        if not self.is_authenticated():
            self.reject_unauthorized()
            return

        if self.path in ("/", "/index.html"):
            self.send_html(build_timeline_html())
            return

        if self.path == "/keywords":
            self.send_html(build_keywords_html())
            return

        if self.path.startswith("/keywords/"):
            keyword = unquote(self.path[len("/keywords/"):]).strip().strip("/")
            self.send_html(build_keyword_detail_html(keyword))
            return

        if self.path in ("/library", "/library/", "/reading", "/reading/"):
            self.send_html(build_library_html())
            return

        if self.path.startswith("/reading/"):
            paper_id = unquote(self.path[len("/reading/"):]).strip().strip("/")
            if paper_id and "/" not in paper_id:
                bundle = load_reading_bundle(paper_id)
                if bundle and ((bundle.get("paper") or {}).get("pdf") or {}).get("file_path"):
                    self.send_html(build_reading_detail_html(paper_id))
                    return
                self.send_html("<!doctype html><html lang='zh-CN'><body><h1>该阅读页缺少可访问的 PDF，暂时不能打开。</h1></body></html>", status=404)
                return

        if self.path.startswith("/api/reading/") and self.path.endswith("/status"):
            paper_id = unquote(self.path[len("/api/reading/"):]).strip().strip("/")
            paper_id = paper_id[:-len("/status")].rstrip("/") if paper_id.endswith("/status") else paper_id
            if not paper_id:
                self.send_json({"ok": False, "error": "paper id 不合法"}, status=400)
                return
            try:
                status_payload = get_reading_status_payload(paper_id)
            except ValueError as exc:
                self.send_json({"ok": False, "error": str(exc)}, status=404)
                return
            self.send_json({"ok": True, "status": status_payload})
            return

        if self.path == "/api/citations":
            self.send_json({"ok": True, "items": list_citations_with_groups()})
            return

        if self.path == "/api/reading-groups":
            self.send_json({"ok": True, "groups": list_reading_groups()})
            return

        if self.path == "/api/expansions":
            self.send_json({"ok": True, "items": list_expansion_sites()})
            return

        super().do_GET()

    def do_POST(self):
        try:
            self._do_POST_impl()
        except Exception as exc:
            self.handle_server_error(exc)

    def _do_POST_impl(self):
        if self.path == "/api/auth/login":
            data = self.parse_body()
            password = data.get("password", "")
            if not verify_password(password):
                if "application/json" in self.headers.get("Content-Type", ""):
                    self.send_json({"ok": False, "error": "invalid_password"}, status=403)
                else:
                    self.send_html(build_login_html("密码错误，请重试。"), status=403)
                return

            token = secrets.token_urlsafe(32)
            SESSIONS[token] = {"created_at": time.time()}
            headers = {
                "Set-Cookie": f"{SESSION_COOKIE}={token}; Path=/; HttpOnly; SameSite=Lax",
            }
            if "application/json" in self.headers.get("Content-Type", ""):
                self.send_json({"ok": True}, extra_headers=headers)
            else:
                self.send_response(302)
                self.send_header("Location", "/")
                for key, value in headers.items():
                    self.send_header(key, value)
                self.end_headers()
            return

        if self.path == "/api/auth/logout":
            jar = cookies.SimpleCookie()
            jar.load(self.headers.get("Cookie", ""))
            token = jar.get(SESSION_COOKIE)
            if token:
                SESSIONS.pop(token.value, None)
            self.send_json(
                {"ok": True},
                extra_headers={"Set-Cookie": f"{SESSION_COOKIE}=; Path=/; Max-Age=0; HttpOnly; SameSite=Lax"},
            )
            return

        if not self.is_authenticated():
            self.reject_unauthorized()
            return

        if self.path == "/api/citations":
            data = self.parse_body()
            paper = data.get("paper") or {}
            if isinstance(paper, str):
                try:
                    paper = json.loads(paper)
                except Exception:
                    paper = {}
            search_slug = (data.get("search_slug") or "").strip()
            group_id_raw = data.get("group_id")
            if not paper.get("title"):
                self.send_json({"ok": False, "error": "missing_title"}, status=400)
                return
            try:
                pdf_record = store_uploaded_pdf(data.get("pdf"), paper.get("title") or "")
            except ValueError as exc:
                self.send_json({"ok": False, "error": str(exc)}, status=400)
                return
            pdf_path = pdf_record.get("pdf_path") or ""
            pdf_sha256 = pdf_record.get("pdf_sha256") or ""
            citation_id = upsert_citation(
                paper,
                search_slug,
                pdf_path=pdf_path or None,
                pdf_sha256=pdf_sha256 or None,
            )
            reading_url = ""
            if group_id_raw not in (None, ""):
                try:
                    group_id = int(group_id_raw)
                except Exception:
                    self.send_json({"ok": False, "error": "group_id 不合法"}, status=400)
                    return
                if not reading_group_exists(group_id):
                    self.send_json({"ok": False, "error": "Reading Group 不存在"}, status=404)
                    return
                if citation_id:
                    add_citation_to_group(citation_id, group_id)
            if pdf_path and citation_id:
                reading = ensure_reading_workspace_for_citation(citation_id)
                reading_url = reading["reading_url"]
            self.send_json(
                {
                    "ok": True,
                    "message": "已加入深度阅读。",
                    "citation_id": citation_id,
                    "pdf_path": pdf_path or "",
                    "pdf_reused": bool(pdf_record.get("reused")),
                    "reading_url": reading_url,
                }
            )
            return

        if self.path == "/api/citations/export":
            data = self.parse_body()
            ids = data.get("ids") or []
            try:
                ids = [int(item) for item in ids]
            except Exception:
                self.send_json({"ok": False, "error": "ids 格式不正确"}, status=400)
                return
            items = get_citations_by_ids(ids)
            payload = {
                "exported_at": datetime.utcnow().isoformat(timespec="seconds") + "Z",
                "count": len(items),
                "items": items,
            }
            raw = json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8")
            filename = f"citations-export-{datetime.now().strftime('%Y%m%d-%H%M%S')}.json"
            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(raw)))
            self.send_header("Content-Disposition", f'attachment; filename="{filename}"')
            self.end_headers()
            self.wfile.write(raw)
            return

        if self.path == "/api/citations/tags":
            data = self.parse_body()
            try:
                citation_id = int(data.get("id"))
            except Exception:
                self.send_json({"ok": False, "error": "缺少合法的 citation id"}, status=400)
                return
            tags = data.get("tags", "")
            update_citation_tags(citation_id, tags)
            self.send_json({"ok": True, "message": "tags 已更新。"})
            return

        if self.path == "/api/reading-groups":
            data = self.parse_body()
            name = (data.get("name") or "").strip()
            description = (data.get("description") or "").strip()
            if not name:
                self.send_json({"ok": False, "error": "Group 名称不能为空"}, status=400)
                return
            try:
                group_id = create_reading_group(name, description)
            except sqlite3.IntegrityError:
                self.send_json({"ok": False, "error": "Group 名称已存在"}, status=409)
                return
            self.send_json({"ok": True, "group_id": group_id, "message": "Group 已创建。"})
            return

        if self.path == "/api/reading/upload":
            data = self.parse_body()
            try:
                pdf_record = store_uploaded_pdf(data.get("pdf"), (data.get("title") or "").strip())
            except ValueError as exc:
                self.send_json({"ok": False, "error": str(exc)}, status=400)
                return
            pdf_path = pdf_record.get("pdf_path") or ""
            pdf_sha256 = pdf_record.get("pdf_sha256") or ""
            if not pdf_path:
                self.send_json({"ok": False, "error": "缺少 PDF 文件"}, status=400)
                return
            existing = find_existing_pdf_by_hash(pdf_sha256) if pdf_sha256 else None
            citation = get_citation_by_id(int(existing["id"])) if existing else None
            matched = bool(citation)
            metadata = {}
            if not citation:
                citation_id = create_placeholder_citation_for_pdf(pdf_path, pdf_sha256)
                citation = get_citation_by_id(citation_id)
            if not citation:
                self.send_json({"ok": False, "error": "无法创建文献记录"}, status=500)
                return
            group_id_raw = data.get("group_id")
            if group_id_raw not in (None, ""):
                try:
                    add_citation_to_group(int(citation["id"]), int(group_id_raw))
                except Exception:
                    pass
            try:
                reading = ensure_reading_workspace_for_citation(int(citation["id"]))
            except Exception as exc:
                self.send_json({"ok": False, "error": f"创建阅读工作区失败: {exc}"}, status=500)
                return
            if not matched:
                start_uploaded_pdf_metadata_job(int(citation["id"]), pdf_path, reading["paper_id"])
            self.send_json(
                {
                    "ok": True,
                    "citation_id": citation["id"],
                    "matched_existing": matched,
                    "metadata": metadata,
                    "pdf_path": pdf_path,
                    "pdf_reused": bool(pdf_record.get("reused")),
                    "reading_url": reading["reading_url"],
                    "paper_id": reading["paper_id"],
                    "message": "PDF 已上传，元数据正在后台识别。",
                }
            )
            return

        if self.path.startswith("/api/citations/") and self.path.endswith("/pdf"):
            data = self.parse_body()
            parts = [part for part in self.path.strip("/").split("/") if part]
            try:
                citation_id = int(parts[2])
            except Exception:
                self.send_json({"ok": False, "error": "citation id 不合法"}, status=400)
                return
            citation = get_citation_by_id(citation_id)
            if not citation:
                self.send_json({"ok": False, "error": "Citation 不存在"}, status=404)
                return
            try:
                pdf_record = store_uploaded_pdf(data.get("pdf"), citation.get("title") or "")
            except ValueError as exc:
                self.send_json({"ok": False, "error": str(exc)}, status=400)
                return
            pdf_path = pdf_record.get("pdf_path") or ""
            pdf_sha256 = pdf_record.get("pdf_sha256") or ""
            if not pdf_path:
                self.send_json({"ok": False, "error": "缺少 PDF 文件"}, status=400)
                return
            update_citation_pdf(citation_id, pdf_path, pdf_sha256)
            citation = get_citation_by_id(citation_id)
            reading_url = ""
            if citation:
                try:
                    reading_url = ensure_reading_workspace_for_citation(citation_id)["reading_url"]
                except Exception:
                    reading_url = ""
            self.send_json(
                {
                    "ok": True,
                    "message": "PDF 已绑定到该文献。",
                    "pdf_path": pdf_path,
                    "pdf_reused": bool(pdf_record.get("reused")),
                    "reading_url": reading_url,
                }
            )
            return

        if self.path.startswith("/api/citations/") and "/groups/" in self.path:
            parts = [part for part in self.path.strip("/").split("/") if part]
            try:
                citation_id = int(parts[2])
                group_id = int(parts[4])
            except Exception:
                self.send_json({"ok": False, "error": "路径参数不合法"}, status=400)
                return
            if not citation_exists(citation_id):
                self.send_json({"ok": False, "error": "Citation 不存在"}, status=404)
                return
            if not reading_group_exists(group_id):
                self.send_json({"ok": False, "error": "Reading Group 不存在"}, status=404)
                return
            add_citation_to_group(citation_id, group_id)
            self.send_json({"ok": True, "message": "已加入 Group。"})
            return

        if self.path.startswith("/api/citations/") and self.path.endswith("/reading"):
            parts = [part for part in self.path.strip("/").split("/") if part]
            try:
                citation_id = int(parts[2])
            except Exception:
                self.send_json({"ok": False, "error": "citation id 不合法"}, status=400)
                return
            if not citation_exists(citation_id):
                self.send_json({"ok": False, "error": "Citation 不存在"}, status=404)
                return
            try:
                reading = ensure_reading_workspace_for_citation(citation_id)
            except ValueError as exc:
                self.send_json({"ok": False, "error": str(exc)}, status=400)
                return
            self.send_json({"ok": True, "reading_url": reading["reading_url"], "paper_id": reading["paper_id"]})
            return

        if self.path.startswith("/api/reading/") and self.path.endswith("/analyze"):
            parts = [part for part in self.path.strip("/").split("/") if part]
            paper_id = parts[2] if len(parts) >= 4 else ""
            if not paper_id:
                self.send_json({"ok": False, "error": "paper id 不合法"}, status=400)
                return
            try:
                result = start_analysis_job(paper_id)
            except Exception as exc:
                self.send_json({"ok": False, "error": f"分析失败: {exc}"}, status=500)
                return
            self.send_json(
                {
                    "ok": True,
                    "message": "分析任务已启动。" if result.get("started") else "分析任务已在运行。",
                    "started": bool(result.get("started")),
                    "status": result.get("status") or {},
                }
            )
            return

        if self.path.startswith("/api/reading/") and self.path.endswith("/questions"):
            parts = [part for part in self.path.strip("/").split("/") if part]
            paper_id = parts[2] if len(parts) >= 4 else ""
            if not paper_id:
                self.send_json({"ok": False, "error": "paper id 不合法"}, status=400)
                return
            data = self.parse_body()
            question = " ".join(str(data.get("question") or "").split())
            if not question:
                self.send_json({"ok": False, "error": "问题不能为空"}, status=400)
                return
            try:
                item = answer_reading_question(paper_id, question)
            except Exception as exc:
                self.send_json({"ok": False, "error": f"提问失败: {exc}"}, status=500)
                return
            self.send_json({"ok": True, "item": item})
            return

        if self.path.startswith("/api/reading/") and self.path.endswith("/notes"):
            parts = [part for part in self.path.strip("/").split("/") if part]
            paper_id = parts[2] if len(parts) >= 4 else ""
            if not paper_id:
                self.send_json({"ok": False, "error": "paper id 不合法"}, status=400)
                return
            data = self.parse_body()
            module_name = (data.get("module") or "").strip()
            content = str(data.get("content") or "")
            try:
                item = save_manual_note(paper_id, module_name, content)
            except Exception as exc:
                self.send_json({"ok": False, "error": f"保存 Notes 失败: {exc}"}, status=400)
                return
            self.send_json({"ok": True, "item": item})
            return

        if self.path == "/api/papers/expand-references":
            data = self.parse_body()
            search_slug = (data.get("search_slug") or "").strip()
            paper = data.get("paper") or {}
            try:
                site_url = create_reference_search(search_slug, paper)
            except ValueError as exc:
                self.send_json({"ok": False, "error": str(exc)}, status=400)
                return
            except Exception as exc:
                self.send_json({"ok": False, "error": f"扩展引用失败: {exc}"}, status=500)
                return
            self.send_json({"ok": True, "site_url": site_url})
            return

        self.send_json({"ok": False, "error": "not_found"}, status=404)

    def do_DELETE(self):
        try:
            self._do_DELETE_impl()
        except Exception as exc:
            self.handle_server_error(exc)

    def _do_DELETE_impl(self):
        if not self.is_authenticated():
            self.reject_unauthorized()
            return

        if self.path.startswith("/api/reading-groups/"):
            group_id_raw = self.path.rsplit("/", 1)[-1]
            try:
                group_id = int(group_id_raw)
            except Exception:
                self.send_json({"ok": False, "error": "group id 不合法"}, status=400)
                return
            if not reading_group_exists(group_id):
                self.send_json({"ok": False, "error": "Reading Group 不存在"}, status=404)
                return
            delete_reading_group(group_id)
            self.send_json({"ok": True, "message": "Group 已删除，文章保留。"})
            return

        if self.path.startswith("/api/citations/") and "/groups/" in self.path:
            parts = [part for part in self.path.strip("/").split("/") if part]
            try:
                citation_id = int(parts[2])
                group_id = int(parts[4])
            except Exception:
                self.send_json({"ok": False, "error": "路径参数不合法"}, status=400)
                return
            remove_citation_from_group(citation_id, group_id)
            self.send_json({"ok": True, "message": "已移出 Group。"})
            return

        if self.path.startswith("/api/citations/") and self.path.endswith("/reading"):
            parts = [part for part in self.path.strip("/").split("/") if part]
            try:
                citation_id = int(parts[2])
            except Exception:
                self.send_json({"ok": False, "error": "citation id 不合法"}, status=400)
                return
            if not citation_exists(citation_id):
                self.send_json({"ok": False, "error": "Citation 不存在"}, status=404)
                return
            removed = remove_reading_workspace_for_citation(citation_id)
            self.send_json({"ok": True, "message": "已删除该深度阅读文献及其相关数据。", "removed": removed})
            return

        if self.path.startswith("/api/reading/") and "/questions/" in self.path:
            parts = [part for part in self.path.strip("/").split("/") if part]
            paper_id = parts[2] if len(parts) >= 5 else ""
            qa_id = parts[4] if len(parts) >= 5 else ""
            if not paper_id or not qa_id:
                self.send_json({"ok": False, "error": "路径参数不合法"}, status=400)
                return
            removed = delete_reading_question_history_item(paper_id, qa_id)
            if not removed:
                self.send_json({"ok": False, "error": "提问记录不存在"}, status=404)
                return
            self.send_json({"ok": True, "message": "提问记录已删除。"})
            return

        self.send_json({"ok": False, "error": "not_found"}, status=404)


def main():
    SEARCHES_DIR.mkdir(parents=True, exist_ok=True)
    EXPANSIONS_DIR.mkdir(parents=True, exist_ok=True)
    ensure_db()
    server = ReusableThreadingHTTPServer((HOST, PORT), SearchSiteHandler)
    print(f"[site] serving {DATA_DIR} at http://{HOST}:{PORT}")
    server.serve_forever()


if __name__ == "__main__":
    main()
