#!/usr/bin/env python3
import csv
import hashlib
import hmac
import json
import os
import secrets
import sqlite3
import time
from datetime import datetime
from html import escape
from http import cookies
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import quote, unquote

import requests
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
DB_PATH = ROOT_DIR / "data" / "citation_library.sqlite3"

PASSWORD_SALT = (os.getenv("SITE_PASSWORD_SALT") or "").strip()
PASSWORD_HASH = (os.getenv("SITE_PASSWORD_HASH") or "").strip()
SESSION_SECRET = (os.getenv("SITE_SESSION_SECRET") or "").strip() or secrets.token_hex(32)
SESSION_COOKIE = "ccf_site_session"
PBKDF2_ITERATIONS = 200_000
REFERENCE_LIMIT = int((os.getenv("REFERENCE_EXPAND_LIMIT") or "20").strip() or "20")
AI4SCHOLAR_API_KEY = (os.getenv("AI4SCHOLAR_API_KEY") or "").strip()

SESSIONS: dict[str, dict] = {}


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
        conn.execute(
            """
            UPDATE citations
            SET tags = matched_kw
            WHERE (tags IS NULL OR tags = '')
              AND matched_kw IS NOT NULL
              AND matched_kw != ''
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


def upsert_citation(paper: dict, search_slug: str):
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
                source_search_slug, source_csv_index, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(doi) DO UPDATE SET
                url=excluded.url,
                authors=excluded.authors,
                year=excluded.year,
                venue=excluded.venue,
                abstract=excluded.abstract,
                matched_kw=excluded.matched_kw,
                tags=COALESCE(NULLIF(citations.tags, ''), excluded.tags),
                source_search_slug=excluded.source_search_slug,
                source_csv_index=excluded.source_csv_index
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
                now,
            ),
        )
        if not (paper.get("doi") or "").strip():
            conn.execute(
                """
                INSERT INTO citations (
                    title, doi, url, authors, year, venue, abstract, matched_kw, tags,
                    source_search_slug, source_csv_index, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(title, year) DO UPDATE SET
                    url=excluded.url,
                    authors=excluded.authors,
                    venue=excluded.venue,
                    abstract=excluded.abstract,
                    matched_kw=excluded.matched_kw,
                    tags=COALESCE(NULLIF(citations.tags, ''), excluded.tags),
                    source_search_slug=excluded.source_search_slug,
                    source_csv_index=excluded.source_csv_index
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
                    now,
                ),
            )
        conn.commit()


def list_citations():
    ensure_db()
    with sqlite3.connect(DB_PATH) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            """
            SELECT id, title, doi, url, authors, year, venue, abstract,
                   matched_kw, tags, source_search_slug, source_csv_index, created_at
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
                   matched_kw, tags, source_search_slug, source_csv_index, created_at
            FROM citations
            WHERE id IN ({placeholders})
            ORDER BY created_at DESC, id DESC
            """,
            ids,
        ).fetchall()
    return [dict(row) for row in rows]


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
          <a href="/library">Citation 库</a>
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
  </style>
</head>
<body>
  <main class="wrap">
    <section class="hero">
      <div class="row">
        <div>
          <h1>Keywords</h1>
          <div class="muted">这里汇总了所有命中词，并按最近新增论文的时间倒序排列。点击某个关键词即可进入该关键词下的论文列表。</div>
        </div>
        <div class="actions">
          <a href="/">返回时间线</a>
          <a href="/library">Citation 库</a>
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
    payload_json = json.dumps(entry["papers"], ensure_ascii=False).replace("</script>", "<\\/script>")
    for idx, paper in enumerate(entry["papers"], start=1):
        meta_parts = [
            f"CSV #{paper['csv_index']}" if paper.get("csv_index") else "",
            paper.get("venue") or "",
            str(paper.get("year") or ""),
            paper.get("authors") or "",
            f"来源搜索：{paper['source_slug']}",
        ]
        meta_text = " · ".join(part for part in meta_parts if part)
        doi_text = f"DOI: {escape(paper['doi'])}" if paper.get("doi") else ""
        link_html = f'<a href="{paper["url"]}" target="_blank" rel="noreferrer">原文链接</a>' if paper.get("url") else ""
        cards.append(
            f"""
            <article class="card">
              <h2>{escape(paper['title'])}</h2>
              <div class="meta">{escape(meta_text)}</div>
              <div class="meta">{doi_text}</div>
              <p>{escape(paper.get('content') or '暂无内容')}</p>
              <div class="links">
                {link_html}
                <button class="action" type="button" onclick="addKeywordCitation({idx})">加入 Citation 库</button>
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
  </style>
</head>
<body>
  <main class="wrap">
    <section class="hero">
      <div class="row">
        <div>
          <h1>{escape(entry['keyword'])}</h1>
          <div class="muted">共 {entry['count']} 篇论文。这里汇总了所有原始搜索中命中该关键词的论文。</div>
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
        const data = await apiPost('/api/citations', {{
          search_slug: paper.source_slug || '',
          paper
        }});
        alert(data.message || '已加入 Citation 库。');
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
    <p>这个站点需要密码才能访问搜索结果、Citation 库和扩展引用功能。</p>
    <input name="password" type="password" placeholder="输入站点密码" autocomplete="current-password" required>
    <button type="submit">登录</button>
    {error_html}
  </form>
</body>
</html>"""


def build_library_html():
    items = list_citations()
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
    cards = []
    for item in items:
        doi_text = f"DOI: {item['doi']}" if item.get("doi") else "无 DOI"
        url_html = f'<a href="{item["url"]}" target="_blank" rel="noreferrer">原文链接</a>' if item.get("url") else ""
        tags = [part.strip() for part in (item.get("tags") or "").split(",") if part.strip()]
        tag_badges = "".join(f'<button class="tag" type="button" data-filter-tag="{tag}">{tag}</button>' for tag in tags) or '<span class="muted">无 tags</span>'
        cards.append(
            f"""
            <article class="card" data-tags="{(item.get("tags") or "").lower()}">
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
              <div class="tag-row">{tag_badges}</div>
              <div class="tag-editor">
                <input class="tag-input" type="text" value="{item.get("tags") or ''}" placeholder="输入 tags，逗号分隔">
                <button class="save-tag" type="button" data-id="{item["id"]}">保存 tags</button>
              </div>
              <p>{item.get("abstract") or "暂无摘要"}</p>
              <div class="links">{url_html}</div>
            </article>
            """
        )
    body = "\n".join(cards) if cards else '<div class="empty">Citation 库还是空的，先去搜索结果页加入几篇吧。</div>'
    return f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Citation Library</title>
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
    .tag-editor input {{
      flex:1 1 280px; padding:10px 12px; border-radius:14px; border:1px solid #d5cbba; font:inherit;
    }}
    h2 {{ margin:8px 0; font-size:24px; line-height:1.3; }}
    p {{ line-height:1.8; }}
    .links a, .hero a, .hero button {{
      display:inline-block; background:#9c4f2f; color:white; text-decoration:none;
      padding:10px 14px; border-radius:999px; border:none; font:inherit; cursor:pointer;
    }}
    .empty {{ padding:24px; text-align:center; }}
  </style>
</head>
<body>
  <main class="wrap">
    <section class="hero">
      <div class="row">
        <div>
          <h1>Citation 库</h1>
          <div class="muted">这里保存你从搜索页手动加入的论文。当前共 {len(items)} 篇。</div>
          <div class="actions">
            <button id="select-all" type="button">全选 / 取消</button>
            <button id="export-json" type="button">导出所选 JSON</button>
          </div>
          <div class="filters">
            <button class="tag active" type="button" data-filter="all">全部</button>
            {filter_html}
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
          const visible = filter === 'all' || tags.split(',').map((x) => x.trim()).includes(filter);
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

        if self.path == "/library":
            self.send_html(build_library_html())
            return

        if self.path == "/api/citations":
            self.send_json({"ok": True, "items": list_citations()})
            return

        if self.path == "/api/expansions":
            self.send_json({"ok": True, "items": list_expansion_sites()})
            return

        super().do_GET()

    def do_POST(self):
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
            search_slug = (data.get("search_slug") or "").strip()
            if not paper.get("title"):
                self.send_json({"ok": False, "error": "missing_title"}, status=400)
                return
            upsert_citation(paper, search_slug)
            self.send_json({"ok": True, "message": "已加入 Citation 库。"})
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


def main():
    SEARCHES_DIR.mkdir(parents=True, exist_ok=True)
    EXPANSIONS_DIR.mkdir(parents=True, exist_ok=True)
    ensure_db()
    server = ReusableThreadingHTTPServer((HOST, PORT), SearchSiteHandler)
    print(f"[site] serving {DATA_DIR} at http://{HOST}:{PORT}")
    server.serve_forever()


if __name__ == "__main__":
    main()
