"""
search.py — 基于关键词的轻量级 DBLP 论文搜索 + 摘要获取 + CSV 导出

用法：
  python -m app.pipeline.search \
    --keywords "physiological notification;biosignal alert" \
    --venues chi,uist,cscw \
    --slug physio-ui \
    [--top 100] \
    [--year-from 2020] \
    [--no-abstract]

输出目录结构：
  data/searches/YYYY-MM-DD_<slug>/
    ├── search.json   ← 搜索参数记录
    ├── papers.csv    ← 论文列表（含摘要）
    ├── papers.json   ← 面向静态站点的结构化 JSON
    └── site/index.html ← 可直接打开的静态网页

说明：
  - 默认爬取摘要（无代理，低并发，随机延迟 2-4s，避免反爬）
  - --no-abstract 跳过摘要爬取，只输出标题/年份/链接
  - 不传 --venues 则在全 DBLP 范围内搜索
"""

import os
import sys
import csv
import json
import re
import time
import random
import asyncio
import argparse
import secrets
import requests
import logging
import shutil
import fcntl
from collections import OrderedDict
from datetime import date
from pathlib import Path
from html import escape, unescape
from urllib.parse import quote

from dotenv import load_dotenv

ROOT_DIR = Path(__file__).resolve().parents[2]
load_dotenv(ROOT_DIR / ".env.local")
MAX_CONCURRENT_RESEARCH_JOBS = max(1, int((os.getenv("MAX_CONCURRENT_RESEARCH_JOBS") or "2").strip() or "2"))
MAX_TOTAL_SEARCH_RESULTS = 200
DATA_DIR = Path((os.getenv("EXSCHOLAR_DATA_DIR") or str(ROOT_DIR / "data")).strip())
SEARCHES_BASE_DIR = Path((os.getenv("EXSCHOLAR_SEARCHES_DIR") or str(DATA_DIR / "searches")).strip())
TMP_SEARCH_BASE_DIR = Path((os.getenv("EXSCHOLAR_TMP_SEARCH_DIR") or str(DATA_DIR / "tmp_search")).strip())
RESEARCH_RUNTIME_DIR = ROOT_DIR / "data" / "research_runtime"
RESEARCH_SLOT_DIR = RESEARCH_RUNTIME_DIR / "slots"

DBLP_SEARCH_URL = "https://dblp.org/search/publ/api"

# HTTP proxies that can reach dblp.org (bypasses the SOCKS5 wrapper at 17900)
_HTTP_PROXY_CANDIDATES = ["http://127.0.0.1:7890"]

def _detect_http_proxy() -> dict | None:
    """Return a working HTTP proxy dict for requests, or None."""
    for proxy_url in _HTTP_PROXY_CANDIDATES:
        try:
            r = requests.get("https://dblp.org/", proxies={"https": proxy_url, "http": proxy_url},
                             timeout=5, headers={"User-Agent": "exscholar-search/1.0"})
            if r.status_code < 500:
                return {"http": proxy_url, "https": proxy_url}
        except Exception:
            continue
    return None

_DBLP_PROXIES: dict | None | bool = False  # False = not yet detected

# OpenAlex venue source IDs (fallback when DBLP is unreachable)
OPENALEX_VENUE_IDS = {
    "chi":      "S4363607743",   # CHI Conference on Human Factors in Computing Systems
    "uist":     "S4306421131",   # User Interface Software and Technology
    "cscw":     "S177586587",    # Computer Supported Cooperative Work
    "imwut":    "S4210219751",   # IMWUT
    "dis":      "S4363608268",   # Designing Interactive Systems Conference
    "iui":      "S4306418948",   # Intelligent User Interfaces
    "nime":     "S4306420611",   # New Interfaces for Musical Expression
    "its":      "S4306418954",   # Interactive Tabletops and Surfaces
    "gi":       "S4306418501",   # Graphics Interface
    "muc":      "S4363608440",   # International Conference on Multimodal Interaction
    "mm":       "S4306417570",   # ACM Multimedia
    "vis":      "S4306418840",   # IEEE VGTC Conference on Visualization
}

OPENALEX_VENUE_ALIASES = {
    "chi": ["chi conference on human factors in computing systems", "human factors in computing systems"],
    "uist": ["user interface software and technology", "uist"],
    "cscw": ["computer supported cooperative work", "cscw"],
    "ubicomp": ["ubicomp", "international joint conference on pervasive and ubiquitous computing", "ubiquitous computing"],
    "imwut": ["imwut", "interactive mobile wearable and ubiquitous technologies"],
    "dis": ["designing interactive systems", "dis conference"],
    "iui": ["intelligent user interfaces", "acm intelligent user interfaces", "iui"],
    "tei": ["tangible, embedded and embodied interaction", "tangible embedded and embodied interaction"],
    "mobilehci": ["human-computer interaction with mobile devices and services"],
    "assets": ["sigaccess conference on computers and accessibility"],
    "nime": ["new interfaces for musical expression", "nime"],
    "hri": ["human-robot interaction"],
    "its": ["interactive tabletops and surfaces", "its"],
    "gi": ["graphics interface"],
    "muc": ["multimodal interaction", "icmi"],
    "mm": ["acm multimedia", "international conference on multimedia"],
    "vis": ["ieee vgtc conference on visualization", "ieee visualization", "visualization conference"],
}

SUMMARY_STOPWORDS = {
    "a", "an", "and", "for", "from", "in", "of", "on", "the", "to", "with",
    "about", "after", "before", "between", "paper", "papers", "research", "results",
    "search", "topic", "using", "based",
}
SUMMARY_ACRONYMS = {
    "acm", "ai", "ar", "chi", "cscw", "cv", "cvpr", "dis", "gi", "hci", "hri",
    "iui", "its", "llm", "ml", "mm", "muc", "nime", "nlp", "tei", "uist", "ui",
    "ux", "vis", "vr",
}


def _render_summary_word(token: str) -> str:
    value = str(token or "").strip()
    if not value:
        return ""
    lowered = value.lower()
    if lowered in SUMMARY_ACRONYMS:
        return lowered.upper()
    if value.isdigit():
        return value
    return value.capitalize()


def build_search_summary_name(*sources: str) -> str:
    selected: list[str] = []
    seen: set[str] = set()
    fallback: list[str] = []
    for source in sources:
        for raw in re.findall(r"[A-Za-z0-9]+", str(source or "")):
            lowered = raw.lower()
            if lowered not in seen:
                seen.add(lowered)
                fallback.append(raw)
            if lowered in SUMMARY_STOPWORDS:
                continue
            if lowered.isdigit() and len(lowered) == 4:
                continue
            if len(lowered) <= 1 and lowered not in SUMMARY_ACRONYMS:
                continue
            if lowered not in {item.lower() for item in selected}:
                selected.append(raw)
            if len(selected) >= 3:
                break
        if len(selected) >= 3:
            break
    if len(selected) < 2:
        for raw in fallback:
            lowered = raw.lower()
            if lowered.isdigit() and len(lowered) == 4:
                continue
            if lowered not in {item.lower() for item in selected}:
                selected.append(raw)
            if len(selected) >= 2:
                break
    if not selected:
        return "Research Topic"
    return " ".join(
        rendered for rendered in (_render_summary_word(item) for item in selected[:3]) if rendered
    ) or "Research Topic"

NO_PROXY_SESSION = None


def _clean_output_text(value) -> str:
    return " ".join(unescape(str(value or "")).split())

def _get_no_proxy_session():
    """Return a requests.Session that bypasses ALL_PROXY env var."""
    global NO_PROXY_SESSION
    if NO_PROXY_SESSION is None:
        import requests
        s = requests.Session()
        s.trust_env = False  # ignore HTTP_PROXY / ALL_PROXY env vars
        NO_PROXY_SESSION = s
    return NO_PROXY_SESSION


def _is_retryable_dblp_error(exc) -> bool:
    if isinstance(exc, (requests.Timeout, requests.ConnectionError, requests.exceptions.SSLError)):
        return True
    if isinstance(exc, requests.HTTPError):
        response = getattr(exc, "response", None)
        if response is None:
            return True
        status = int(response.status_code or 0)
        return status in {408, 409, 425, 429} or status >= 500
    return False


def _dblp_request_json(params: dict) -> dict:
    global _DBLP_PROXIES
    if _DBLP_PROXIES is False:
        _DBLP_PROXIES = _detect_http_proxy()
        if _DBLP_PROXIES:
            print(f"[search] 检测到可用 HTTP 代理: {list(_DBLP_PROXIES.values())[0]}")

    direct_session = _get_no_proxy_session()
    last_err = None

    attempt_routes: list[tuple[str, dict | None]] = [("direct", None)]
    if _DBLP_PROXIES:
        attempt_routes.append(("proxy", _DBLP_PROXIES))
    attempt_routes.extend([("direct", None)])
    if _DBLP_PROXIES:
        attempt_routes.append(("proxy", _DBLP_PROXIES))

    for attempt_index, (route_name, proxies) in enumerate(attempt_routes, start=1):
        try:
            if proxies:
                resp = requests.get(
                    DBLP_SEARCH_URL,
                    params=params,
                    timeout=15,
                    headers={"User-Agent": "exscholar-search/1.0"},
                    proxies=proxies,
                )
            else:
                resp = direct_session.get(
                    DBLP_SEARCH_URL,
                    params=params,
                    timeout=15,
                    headers={"User-Agent": "exscholar-search/1.0"},
                )
            resp.raise_for_status()
            return resp.json()
        except Exception as exc:
            last_err = exc
            retryable = _is_retryable_dblp_error(exc)
            print(
                f"[search] DBLP attempt {attempt_index}/{len(attempt_routes)} "
                f"via {route_name} failed: {type(exc).__name__}: {exc}"
            )
            if not retryable:
                break
            if attempt_index < len(attempt_routes):
                time.sleep(min(6.0, 1.5 * attempt_index + random.uniform(0.2, 0.8)))
    raise last_err or RuntimeError("DBLP request failed")


def _reconstruct_abstract(inverted_index: dict | None) -> str:
    """Reconstruct abstract text from OpenAlex abstract_inverted_index."""
    if not inverted_index:
        return ""
    words: dict[int, str] = {}
    for word, positions in inverted_index.items():
        for pos in positions:
            words[pos] = word
    return " ".join(words[i] for i in sorted(words))


def _ensure_research_slot_dir():
    RESEARCH_SLOT_DIR.mkdir(parents=True, exist_ok=True)


def _slot_state_path(slot_index: int) -> Path:
    return RESEARCH_SLOT_DIR / f"slot_{slot_index}.json"


def _cleanup_stale_research_slot_state():
    _ensure_research_slot_dir()
    for slot_index in range(MAX_CONCURRENT_RESEARCH_JOBS):
        state_path = _slot_state_path(slot_index)
        if not state_path.exists():
            continue
        lock_path = RESEARCH_SLOT_DIR / f"slot_{slot_index}.lock"
        lock_file = open(lock_path, "a+", encoding="utf-8")
        try:
            try:
                fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                continue
            state_path.unlink(missing_ok=True)
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)
        finally:
            lock_file.close()


def _acquire_research_slot(owner_id: str, status_callback=None, poll_interval: float = 2.0):
    _ensure_research_slot_dir()
    _cleanup_stale_research_slot_state()
    wait_started_at = None
    while True:
        for slot_index in range(MAX_CONCURRENT_RESEARCH_JOBS):
            lock_path = RESEARCH_SLOT_DIR / f"slot_{slot_index}.lock"
            lock_file = open(lock_path, "a+", encoding="utf-8")
            try:
                fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                lock_file.close()
                continue

            state = {
                "owner_id": owner_id,
                "slot_index": slot_index,
                "pid": os.getpid(),
                "acquired_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            }
            _slot_state_path(slot_index).write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
            return slot_index, lock_file

        if wait_started_at is None:
            wait_started_at = time.time()
        waited_seconds = max(0, int(time.time() - wait_started_at))
        if callable(status_callback):
            status_callback(
                "queued",
                f"搜索任务正在排队，当前最多同时运行 {MAX_CONCURRENT_RESEARCH_JOBS} 个任务，已等待 {waited_seconds} 秒。",
                waited_seconds=waited_seconds,
                max_concurrent_jobs=MAX_CONCURRENT_RESEARCH_JOBS,
            )
        time.sleep(poll_interval)


def _release_research_slot(slot_index: int, lock_file):
    try:
        _slot_state_path(slot_index).unlink(missing_ok=True)
    except Exception:
        pass
    try:
        fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)
    finally:
        lock_file.close()


def _build_search_output_dir(slug: str) -> tuple[str, str]:
    dated_slug = f"{date.today().isoformat()}_{slug}"
    base_dir = SEARCHES_BASE_DIR
    out_dir = base_dir / dated_slug
    suffix = 2
    while out_dir.exists():
        dated_slug = f"{date.today().isoformat()}_{slug}-{suffix}"
        out_dir = base_dir / dated_slug
        suffix += 1
    return dated_slug, str(out_dir)


def openalex_search(keywords: str, venue: str | None, top: int, year_from: int) -> list[dict]:
    """Search via OpenAlex API as fallback when DBLP is unreachable."""
    session = _get_no_proxy_session()
    base = "https://api.openalex.org/works"

    filters = []
    has_source_filter = False
    venue_aliases = []
    if venue:
        venue_aliases = OPENALEX_VENUE_ALIASES.get(venue.lower(), [])
        vid = OPENALEX_VENUE_IDS.get(venue.lower())
        if vid:
            filters.append(f"primary_location.source.id:{vid}")
            has_source_filter = True
        elif not venue_aliases:
            print(f"[search] OpenAlex fallback 跳过未映射 venue: {venue}")
            return []
    if year_from:
        filters.append(f"publication_year:{year_from}-2030")

    params = {
        "search": keywords,
        "per-page": min(top, 200),
        "select": "title,publication_year,doi,abstract_inverted_index,authorships,primary_location,ids",
        "mailto": "ccf-crawler@example.com",
    }
    if filters:
        params["filter"] = ",".join(filters)

    papers = []
    try:
        resp = session.get(base, params=params, timeout=30)
        resp.raise_for_status()
        data = resp.json()
    except Exception as e:
        print(f"[search] OpenAlex 请求失败: {e}")
        return []

    for item in data.get("results", []):
        venue_name = (item.get("primary_location") or {}).get("source") or {}
        venue_name = venue_name.get("display_name", "") if isinstance(venue_name, dict) else ""
        venue_name_lower = venue_name.lower()
        if venue and not has_source_filter and venue_aliases:
            if not any(alias in venue_name_lower for alias in venue_aliases):
                continue
        authors_raw = item.get("authorships", [])
        authors = [a.get("author", {}).get("display_name", "") for a in authors_raw]
        doi = item.get("doi") or ""
        if doi.startswith("https://doi.org/"):
            doi = doi[len("https://doi.org/"):]
        abstract = _reconstruct_abstract(item.get("abstract_inverted_index"))
        openalex_id = item.get("ids", {}).get("openalex", "")
        ee = [f"https://doi.org/{doi}"] if doi else []

        papers.append({
            "title": item.get("title") or item.get("display_name", ""),
            "venue": venue_name,
            "year": item.get("publication_year"),
            "authors": authors,
            "doi": doi,
            "ee": ee,
            "abstract": abstract,
            "key": openalex_id,
            "_source_engine": "openalex",
        })

    return papers


def dblp_search(keywords: str, venue: str | None, top: int, year_from: int, diagnostics: dict | None = None) -> list[dict]:
    """调用 DBLP search API，返回论文列表（只匹配标题）。"""
    query = keywords
    if venue:
        query += f" venue:{venue}"

    params = {"q": query, "format": "json", "h": min(top, 1000), "f": 0, "c": 0}

    try:
        data = _dblp_request_json(params)
        if isinstance(diagnostics, dict):
            diagnostics["engine"] = "dblp"
            diagnostics["fallback"] = False
    except Exception as e:
        if isinstance(diagnostics, dict):
            diagnostics["engine"] = "openalex"
            diagnostics["fallback"] = True
            diagnostics["error_type"] = type(e).__name__
            diagnostics["error"] = str(e)
        print(f"[search] DBLP 请求失败: {e}，自动切换到 OpenAlex...")
        return None  # signal caller to fallback

    hits = data.get("result", {}).get("hits", {}).get("hit", []) or []
    papers = []
    for hit in hits:
        info = hit.get("info", {})
        year = info.get("year")
        try:
            year = int(year)
        except (TypeError, ValueError):
            year = None

        if year_from and (year is None or year < year_from):
            continue

        ee = info.get("ee")
        if isinstance(ee, str):
            ee = [ee]
        elif not isinstance(ee, list):
            ee = []

        authors_raw = info.get("authors", {}).get("author", [])
        if isinstance(authors_raw, dict):
            authors_raw = [authors_raw]
        authors = [a.get("text", "") if isinstance(a, dict) else str(a) for a in authors_raw]

        papers.append({
            "title": info.get("title", ""),
            "venue": info.get("venue", ""),
            "year": year,
            "authors": authors,
            "doi": info.get("doi", ""),
            "ee": ee,
            "abstract": "",
            "key": hit.get("@id", ""),
            "_source_engine": "dblp",
        })

    return papers


def fetch_abstracts_for_papers(papers: list[dict], tmp_dir: str, max_concurrent: int = 2) -> list[dict]:
    """
    调用 AsyncAbstractFetcher 补充摘要。
    无代理模式：并发数 2，每次请求间随机延迟 2-4 秒。
    100 篇预计耗时 10-20 分钟。
    """
    from .crawler.fetch_abstract import AsyncAbstractFetcher

    os.makedirs(tmp_dir, exist_ok=True)
    tmp_file = os.path.join(tmp_dir, "batch.json")

    with open(tmp_file, "w", encoding="utf-8") as f:
        json.dump({"metadata": {"venue_name": "search", "year": 0}, "papers": papers},
                  f, ensure_ascii=False)

    async def run():
        async with AsyncAbstractFetcher(max_concurrent=max_concurrent, proxy_pool_size=0) as fetcher:
            await fetcher.driver.start()
            try:
                await fetcher.process_dir_async(tmp_dir)
            finally:
                await fetcher.driver.close()

    logging.disable(logging.WARNING)
    asyncio.run(run())
    logging.disable(logging.NOTSET)

    with open(tmp_file, encoding="utf-8") as f:
        result = json.load(f)

    return result.get("papers", papers)


def write_csv(papers: list[dict], output_path: str):
    with open(output_path, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=[
            "matched_kw", "matched_venue", "source_engine", "relevance_label", "relevance_score", "autotags", "review_reason", "title", "venue", "year", "authors", "doi", "url", "abstract"
        ])
        writer.writeheader()
        for p in papers:
            ee = p.get("ee") or []
            authors = p.get("authors", [])
            writer.writerow({
                "matched_kw": p.get("_matched_kw", "") or p.get("matched_kw", ""),
                "matched_venue": p.get("_matched_venue", "") or p.get("matched_venue", ""),
                "source_engine": p.get("_source_engine", "") or p.get("source_engine", ""),
                "relevance_label": p.get("relevance_label", ""),
                "relevance_score": p.get("relevance_score", ""),
                "autotags": ", ".join(_clean_output_text(item) for item in (p.get("autotags") or []) if str(item or "").strip()),
                "review_reason": _clean_output_text(p.get("review_reason", "")),
                "title": _clean_output_text(p.get("title", "")),
                "venue": _clean_output_text(p.get("venue", "")),
                "year": p.get("year", ""),
                "authors": _clean_output_text(", ".join(authors) if isinstance(authors, list) else str(authors)),
                "doi": _clean_output_text(p.get("doi", "")),
                "url": (ee[0] if ee else "") or p.get("url", ""),
                "abstract": _clean_output_text((p.get("abstract") or p.get("content") or "").replace("\n", " ").strip()),
            })


def build_json_records(papers: list[dict]) -> list[dict]:
    records = []
    for idx, p in enumerate(papers, start=1):
        ee = p.get("ee") or []
        authors = p.get("authors", [])
        content = _clean_output_text((p.get("abstract") or "").replace("\n", " ").strip())
        records.append({
            "csv_index": idx,
            "title": _clean_output_text(p.get("title", "")),
            "content": content,
            "matched_kw": p.get("_matched_kw", ""),
            "matched_venue": p.get("_matched_venue", ""),
            "source_engine": p.get("_source_engine", ""),
            "relevance_label": p.get("relevance_label", ""),
            "relevance_score": p.get("relevance_score", 0),
            "autotags": [_clean_output_text(item) for item in (p.get("autotags") or []) if str(item or "").strip()],
            "review_reason": _clean_output_text(p.get("review_reason", "")),
            "venue": _clean_output_text(p.get("venue", "")),
            "year": p.get("year", ""),
            "authors": _clean_output_text(", ".join(authors) if isinstance(authors, list) else str(authors)),
            "doi": _clean_output_text(p.get("doi", "")),
            "url": ee[0] if ee else "",
            "paper_id": p.get("paper_id", "") or p.get("paperId", ""),
        })
    return records


def write_json(records: list[dict], output_path: str, meta: dict):
    payload = {
        "meta": meta,
        "papers": records,
    }
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)


def write_site(records: list[dict], output_path: str, meta: dict):
    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    normalized_records = records.get("papers", []) if isinstance(records, dict) else records
    normalized_meta = records.get("meta", {}) if isinstance(records, dict) else {}
    if not isinstance(normalized_records, list):
        normalized_records = []
    if not isinstance(meta, dict):
        meta = {}
    merged_meta = dict(normalized_meta)
    merged_meta.update(meta)

    def preview_text(value: str, limit: int = 900) -> str:
        text = str(value or "暂无内容").strip() or "暂无内容"
        if len(text) <= limit:
            return text
        return text[:limit].rstrip() + " ..."

    def render_initial_card(paper: dict) -> str:
        authors = paper.get("authors") or ""
        if isinstance(authors, list):
            authors_text = ", ".join(str(item).strip() for item in authors if str(item).strip())
        else:
            authors_text = str(authors).strip()
        line_meta = " · ".join(
            item for item in [
                f"CSV #{paper.get('csv_index')}" + (f" · 命中词：{paper.get('matched_kw')}" if paper.get("matched_kw") else ""),
                paper.get("venue") or "",
                str(paper.get("year") or ""),
                authors_text,
            ] if item
        )
        review_meta = " · ".join(
            item for item in [
                f"相关性：{paper.get('relevance_label')}" if paper.get("relevance_label") else "",
                f"分数：{paper.get('relevance_score')}" if paper.get("relevance_score") not in ("", None) else "",
                "标签：" + ", ".join(paper.get("autotags") or []) if paper.get("autotags") else "",
            ] if item
        )
        doi_text = f"DOI: {escape(str(paper.get('doi') or ''))}" if paper.get("doi") else ""
        link_html = (
            f'<a href="{escape(str(paper.get("url") or ""))}" target="_blank" rel="noreferrer">原文链接</a>'
            if paper.get("url") else ""
        )
        meta_line = " · ".join(item for item in [doi_text, link_html] if item)
        relevance_label = str(paper.get("relevance_label") or "").strip().lower()
        review_pills = []
        if relevance_label:
            score = paper.get("relevance_score")
            suffix = f" · {score}" if score not in ("", None) else ""
            review_pills.append(
                f'<span class="review-pill {escape(relevance_label)}">相关性：{escape(relevance_label)}{escape(str(suffix))}</span>'
            )
        for tag in paper.get("autotags") or []:
            value = str(tag or "").strip()
            if not value:
                continue
            review_pills.append(
                f'<button class="review-pill" type="button" data-autotag-filter="{escape(value.lower())}">{escape(value)}</button>'
            )
        return f"""
        <article class="card">
          <div class="card-head">
            <div class="idx">#{escape(str(paper.get("csv_index") or ""))}</div>
            <h2 class="paper-title">{escape(str(paper.get("title") or ""))}</h2>
          </div>
          <div class="paper-meta">{escape(line_meta)}</div>
          {'<div class="review-row">' + ''.join(review_pills) + '</div>' if review_pills else ''}
          <div class="paper-meta">{escape(review_meta)}</div>
          <div class="paper-meta">{meta_line}</div>
          {f'<div class="paper-meta">复核说明：{escape(str(paper.get("review_reason") or ""))}</div>' if paper.get("review_reason") else ''}
          <div class="content">{escape(preview_text(paper.get("content") or "暂无内容"))}</div>
          <div class="actions">
            <button class="action" type="button" onclick="addCitation({json.dumps(paper.get('csv_index'))})">加入深度阅读</button>
            <button class="action secondary" type="button" onclick="expandReferences({json.dumps(paper.get('csv_index'))})">延展搜索</button>
          </div>
        </article>
        """

    payload = {
        "meta": merged_meta,
        "papers": normalized_records,
    }
    payload_json = json.dumps(payload, ensure_ascii=False)
    initial_cards_html = "\n".join(render_initial_card(record) for record in normalized_records) if normalized_records else ""
    title = escape(f"{merged_meta.get('slug', 'papers')} Papers")
    keyword_text = escape(" / ".join(merged_meta.get("keywords", [])))
    venue_text = escape(", ".join(merged_meta.get("venues", [])) or "全局")
    html = f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{title}</title>
  <style>
    :root {{
      color-scheme: light;
      --bg: #f4f0e8;
      --panel: #fffaf2;
      --ink: #1f1f1f;
      --muted: #6f6455;
      --line: #d7ccb9;
      --accent: #b35c2e;
      --accent-soft: #f0dccf;
    }}
    * {{ box-sizing: border-box; }}
    body {{
      margin: 0;
      font-family: Georgia, "Noto Serif SC", serif;
      color: var(--ink);
      background:
        radial-gradient(circle at top left, #efe4d4 0, transparent 28rem),
        linear-gradient(180deg, #f7f1e7 0%, var(--bg) 100%);
    }}
    .wrap {{
      max-width: 1080px;
      margin: 0 auto;
      padding: 32px 20px 80px;
    }}
    .hero {{
      padding: 28px;
      border: 1px solid var(--line);
      background: rgba(255, 250, 242, 0.92);
      border-radius: 24px;
      box-shadow: 0 18px 40px rgba(90, 64, 32, 0.08);
    }}
    h1 {{
      margin: 0 0 10px;
      font-size: clamp(30px, 5vw, 54px);
      line-height: 1.02;
    }}
    .meta, .hint {{
      color: var(--muted);
      font-size: 14px;
      line-height: 1.6;
    }}
    .toolbar {{
      display: grid;
      grid-template-columns: 1fr auto;
      gap: 12px;
      margin: 24px 0 20px;
    }}
    input {{
      width: 100%;
      border: 1px solid var(--line);
      border-radius: 999px;
      padding: 14px 18px;
      font: inherit;
      background: rgba(255,255,255,0.85);
    }}
    .count {{
      display: inline-flex;
      align-items: center;
      justify-content: center;
      min-width: 124px;
      border-radius: 999px;
      padding: 0 16px;
      background: var(--accent);
      color: white;
      font-weight: 700;
    }}
    .filters {{
      display: flex;
      gap: 10px;
      flex-wrap: wrap;
      margin: 0 0 20px;
    }}
    .tag {{
      border: none;
      border-radius: 999px;
      background: var(--accent-soft);
      color: var(--muted);
      padding: 8px 12px;
      cursor: pointer;
      font: inherit;
    }}
    .tag.active {{
      background: var(--accent);
      color: white;
    }}
    .list {{
      display: grid;
      gap: 14px;
    }}
    .card {{
      border: 1px solid var(--line);
      border-radius: 20px;
      padding: 20px;
      background: rgba(255, 250, 242, 0.96);
      box-shadow: 0 10px 24px rgba(90, 64, 32, 0.06);
    }}
    .topbar {{
      margin-top: 18px;
      display: flex;
      gap: 12px;
      flex-wrap: wrap;
    }}
    .card-head {{
      display: flex;
      gap: 12px;
      align-items: baseline;
      flex-wrap: wrap;
      margin-bottom: 10px;
    }}
    .idx {{
      color: var(--accent);
      font-weight: 700;
      letter-spacing: 0.04em;
    }}
    .paper-title {{
      margin: 0;
      font-size: 22px;
      line-height: 1.25;
    }}
    .paper-meta {{
      color: var(--muted);
      font-size: 14px;
      margin-bottom: 12px;
      line-height: 1.7;
    }}
    .review-row {{
      display:flex;
      gap:8px;
      flex-wrap:wrap;
      margin-bottom: 12px;
    }}
    .review-pill {{
      display:inline-flex;
      align-items:center;
      gap:6px;
      padding: 6px 10px;
      border-radius: 999px;
      font-size: 13px;
      line-height: 1;
      background: #efe2d4;
      color: #6f6455;
    }}
    .review-pill.high {{ background:#dceee4; color:#215f43; }}
    .review-pill.medium {{ background:#efe7d2; color:#7a5d18; }}
    .review-pill.low {{ background:#f2dddd; color:#8a3d32; }}
    .content {{
      white-space: pre-wrap;
      line-height: 1.8;
      font-size: 16px;
    }}
    .actions {{
      margin-top: 16px;
      display: flex;
      gap: 10px;
      flex-wrap: wrap;
    }}
    button.action, a.pill {{
      border: none;
      border-radius: 999px;
      padding: 10px 14px;
      font: inherit;
      cursor: pointer;
      text-decoration: none;
      background: var(--accent);
      color: white;
    }}
    button.action.secondary {{
      background: #6f6455;
    }}
    button.action.related {{
      background: #1f7a5c;
    }}
    .card.expanded {{
      border-color: #99c3b1;
      box-shadow: 0 12px 28px rgba(31, 122, 92, 0.12);
      background: linear-gradient(180deg, rgba(241, 251, 246, 0.98), rgba(255, 250, 242, 0.96));
    }}
    .toast {{
      position: fixed;
      right: 18px;
      bottom: 18px;
      max-width: min(88vw, 360px);
      padding: 14px 16px;
      border-radius: 16px;
      background: rgba(31,31,31,0.94);
      color: white;
      line-height: 1.6;
      box-shadow: 0 14px 28px rgba(0,0,0,0.2);
      opacity: 0;
      transform: translateY(16px);
      transition: opacity .22s ease, transform .22s ease;
      pointer-events: none;
    }}
    .toast.show {{
      opacity: 1;
      transform: translateY(0);
    }}
    .empty {{
      padding: 20px;
      color: var(--muted);
      border: 1px dashed var(--line);
      border-radius: 18px;
      text-align: center;
      background: rgba(255,255,255,0.5);
    }}
    a {{
      color: var(--accent);
      text-decoration: none;
    }}
    @media (max-width: 720px) {{
      .wrap {{ padding: 22px 14px 56px; }}
      .hero {{ padding: 22px 18px; }}
      .toolbar {{ grid-template-columns: 1fr; }}
      .count {{ min-height: 48px; }}
      .topbar a.pill,
      .actions button.action,
      .actions a.pill {{
        width: 100%;
        text-align: center;
      }}
      .filters {{ gap: 8px; }}
      .tag {{ width: 100%; text-align: center; }}
      .paper-title {{ font-size: 20px; }}
      .card {{ padding: 16px; }}
      .content {{ font-size: 15px; }}
    }}
  </style>
</head>
<body>
  <main class="wrap">
    <section class="hero">
      <h1>{title}</h1>
      <div class="meta">关键词：{keyword_text}</div>
      <div class="meta">范围：{venue_text}</div>
      <div class="meta">共 <span id="total-count">{len(normalized_records)}</span> 篇，支持按标题、关键词、摘要内容检索。</div>
      <div class="topbar">
        <a class="pill" href="/">返回时间线</a>
        <a class="pill" href="/reading">打开深度阅读</a>
      </div>
    </section>

    <section class="toolbar">
      <input id="q" type="search" placeholder="搜索标题、关键词、摘要内容">
      <div class="count"><span id="count">{len(normalized_records)}</span> 篇可见</div>
    </section>

    <section class="filters" id="kw-filters"></section>
    <section class="filters" id="relevance-filters"></section>
    <section class="filters" id="autotag-filters"></section>

    <section id="list" class="list">{initial_cards_html}</section>
    <template id="empty">
      <div class="empty">没有匹配结果，试试更短的关键词。</div>
    </template>
    <noscript>
      <div class="empty" style="margin-top:14px;">当前页面已直接展示论文卡片。若要使用搜索过滤，请开启 JavaScript。</div>
    </noscript>
  </main>

  <script id="papers-data" type="application/json">{payload_json}</script>
  <script>
    const payload = JSON.parse(document.getElementById('papers-data').textContent);
    const papers = payload.papers || [];
    const meta = payload.meta || {{}};
    const list = document.getElementById('list');
    const input = document.getElementById('q');
    const count = document.getElementById('count');
    const kwFilters = document.getElementById('kw-filters');
    const relevanceFilters = document.getElementById('relevance-filters');
    const autotagFilters = document.getElementById('autotag-filters');
    const emptyTemplate = document.getElementById('empty');
    let expansionIndex = {{}};
    let activeMatchedKw = 'all';
    let activeRelevance = 'all';
    let activeAutotag = 'all';
    let readingGroups = [];
    const toast = document.createElement('div');
    toast.className = 'toast';
    document.body.appendChild(toast);

    function text(v) {{
      return (v || '').toString();
    }}

    function esc(v) {{
      return text(v)
        .replaceAll('&', '&amp;')
        .replaceAll('<', '&lt;')
        .replaceAll('>', '&gt;')
        .replaceAll('"', '&quot;');
    }}

    function showToast(message) {{
      toast.textContent = message;
      toast.classList.add('show');
      clearTimeout(showToast.timer);
      showToast.timer = setTimeout(() => toast.classList.remove('show'), 2600);
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
          <div id="citation-source-link-box" style="display:none; margin-bottom:14px; padding:12px; border:1px dashed #d5cbba; border-radius:14px; background:#fffaf4;">
            <div style="margin-bottom:8px; color:#6f685c;">请先打开原文链接手动下载 PDF，再上传到深度阅读。</div>
            <a id="citation-source-link" href="#" target="_blank" rel="noreferrer" style="display:none; border:none; background:#9c4f2f; color:white; padding:10px 14px; border-radius:999px; text-decoration:none;">打开原文链接</a>
          </div>
          <label style="display:block; margin-bottom:14px;">
            <div style="margin-bottom:6px; color:#6f685c;">上传 PDF（必填）</div>
            <input id="citation-pdf-input" type="file" accept="application/pdf,.pdf" style="width:100%;">
          </label>
          <div id="citation-progress-box" style="display:none; margin:-4px 0 14px;">
            <div style="height:10px; border-radius:999px; background:#eadfce; overflow:hidden;">
              <div id="citation-progress-bar" style="width:0%; height:100%; background:#9c4f2f; transition:width .2s ease;"></div>
            </div>
            <div id="citation-progress-text" style="margin-top:8px; color:#6f685c; font-size:14px;">等待开始...</div>
          </div>
          <div id="citation-status" style="display:none; margin:-4px 0 14px; padding:10px 12px; border-radius:12px; background:#f5ede4; color:#6b4b39; line-height:1.6;"></div>
          <div style="display:flex; gap:10px; flex-wrap:wrap;">
            <button id="citation-submit" type="submit" value="submit" style="border:none; background:#9c4f2f; color:white; padding:10px 14px; border-radius:999px; cursor:pointer;">保存并解析</button>
            <button id="citation-cancel" type="submit" value="cancel" style="border:none; background:#6f6455; color:white; padding:10px 14px; border-radius:999px; cursor:pointer;">取消</button>
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

    async function submitCitation(searchSlug, paper, preferredGroupName = '') {{
      const dialog = ensureCitationDialog();
      document.getElementById('citation-dialog-title').textContent = paper.title || '';
      const select = document.getElementById('citation-group-select');
      const fileInput = document.getElementById('citation-pdf-input');
      const progressBox = document.getElementById('citation-progress-box');
      const progressBar = document.getElementById('citation-progress-bar');
      const progressText = document.getElementById('citation-progress-text');
      const statusBox = document.getElementById('citation-status');
      const sourceLinkBox = document.getElementById('citation-source-link-box');
      const sourceLink = document.getElementById('citation-source-link');
      const submitButton = document.getElementById('citation-submit');
      const cancelButton = document.getElementById('citation-cancel');
      select.innerHTML = '<option value="">暂不加入 Group</option>' + readingGroups.map(
        (group) => `<option value="${{group.id}}">${{esc(group.name)}}</option>`
      ).join('');
      const preferred = text(preferredGroupName).trim().toLowerCase();
      if (preferred) {{
        const matchedGroup = readingGroups.find((group) => text(group.name).trim().toLowerCase() === preferred);
        if (matchedGroup) select.value = String(matchedGroup.id);
      }}
      fileInput.value = '';
      progressBox.style.display = 'none';
      progressBar.style.width = '0%';
      progressText.textContent = '等待开始...';
      statusBox.style.display = 'none';
      statusBox.textContent = '';
      const sourceUrl = (paper.url || '').toString().trim();
      sourceLinkBox.style.display = 'block';
      sourceLink.href = sourceUrl || '#';
      sourceLink.style.display = sourceUrl ? 'inline-flex' : 'none';
      submitButton.disabled = false;
      cancelButton.disabled = false;
      return new Promise((resolve) => {{
        const form = document.getElementById('citation-form');

        const setBusy = (busy) => {{
          submitButton.disabled = busy;
          cancelButton.disabled = busy;
        }};

        const cleanup = () => {{
          form.removeEventListener('submit', handler);
        }};

        const handler = async (event) => {{
          event.preventDefault();
          const action = event.submitter && event.submitter.value;
          if (action !== 'submit') {{
            cleanup();
            dialog.close();
            resolve(null);
            return;
          }}
          if (!fileInput.files[0]) {{
            statusBox.textContent = '请先从原文链接下载 PDF，然后上传 PDF 后再加入深度阅读。';
            statusBox.style.display = 'block';
            return;
          }}
          setBusy(true);
          progressBox.style.display = 'block';
          progressBar.style.width = '0%';
          progressText.textContent = '准备上传 PDF...';
          const formData = new FormData();
          formData.append('search_slug', searchSlug || '');
          formData.append('paper', JSON.stringify(paper));
          if (select.value) formData.append('group_id', select.value);
          formData.append('pdf', fileInput.files[0]);
          try {{
            const data = await uploadWithProgress('/api/citations', formData, (percent, text) => {{
              progressBar.style.width = percent + '%';
              progressText.textContent = text;
            }});
            progressBar.style.width = '100%';
            progressText.textContent = 'PDF 已上传，正在启动深度解析...';
            cleanup();
            dialog.close();
            resolve(data);
          }} catch (error) {{
            cleanup();
            dialog.close();
            resolve({{ error: error.message || '请求失败' }});
          }}
        }};

        form.addEventListener('submit', handler);
        dialog.showModal();
      }});
    }}

    function normalizeDoi(value) {{
      let text = (value || '').toString().trim().toLowerCase();
      for (const prefix of ['https://doi.org/', 'http://doi.org/', 'doi:']) {{
        if (text.startsWith(prefix)) {{
          text = text.slice(prefix.length);
        }}
      }}
      return text.trim();
    }}

    function uniqueMatchedKeywords() {{
      const values = [];
      const seen = new Set();
      for (const paper of papers) {{
        const kw = text(paper.matched_kw).trim();
        if (!kw) continue;
        const key = kw.toLowerCase();
        if (seen.has(key)) continue;
        seen.add(key);
        values.push(kw);
      }}
      return values;
    }}

    function uniqueRelevanceLabels() {{
      const order = ['high', 'medium', 'low'];
      return order.filter((label) => papers.some((paper) => text(paper.relevance_label).trim().toLowerCase() === label));
    }}

    function uniqueAutotags() {{
      const hasHigh = papers.some((paper) => text(paper.relevance_label).trim().toLowerCase() === 'high');
      const targetLabel = hasHigh ? 'high' : 'medium';
      const values = [];
      const seen = new Set();
      for (const paper of papers) {{
        if (text(paper.relevance_label).trim().toLowerCase() !== targetLabel) continue;
        for (const tag of (paper.autotags || [])) {{
          const value = text(tag).trim();
          if (!value) continue;
          const key = value.toLowerCase();
          if (seen.has(key)) continue;
          seen.add(key);
          values.push(value);
        }}
      }}
      return values.sort((a, b) => a.localeCompare(b));
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

    async function apiGet(path) {{
      const controller = new AbortController();
      const timer = setTimeout(() => controller.abort(), 15000);
      let resp;
      try {{
        resp = await fetch(path, {{
          credentials: 'same-origin',
          signal: controller.signal
        }});
      }} catch (error) {{
        if (error && error.name === 'AbortError') {{
          throw new Error('扩展搜索请求超时，请稍后重试');
        }}
        throw error;
      }} finally {{
        clearTimeout(timer);
      }}
      const data = await resp.json().catch(() => ({{ ok: false, error: '请求失败' }}));
      if (!resp.ok || data.ok === false) {{
        throw new Error(data.error || '请求失败');
      }}
      return data;
    }}

    function sleep(ms) {{
      return new Promise((resolve) => setTimeout(resolve, ms));
    }}

    async function waitReferenceExpansionJob(jobId) {{
      const deadline = Date.now() + 20 * 60 * 1000;
      while (Date.now() < deadline) {{
        const data = await apiGet(`/api/papers/expand-references/jobs/${{encodeURIComponent(jobId)}}`);
        const job = data.job || {{}};
        const status = text(job.status).trim().toLowerCase();
        if (status === 'completed') return job;
        if (status === 'failed') {{
          throw new Error(job.error || job.step_message || '扩展搜索失败');
        }}
        const message = text(job.step_message).trim();
        if (message) {{
          showToast(message);
        }}
        await sleep(1200);
      }}
      throw new Error('扩展搜索等待超时，请稍后到时间线中查看结果');
    }}

    async function addCitation(index) {{
      const paper = papers.find((item) => item.csv_index === index);
      if (!paper) return;
      try {{
        if (!readingGroups.length) {{
          await loadReadingGroups();
        }}
        const data = await submitCitation(meta.output_slug || meta.slug || '', paper, meta.group_name || meta.summary_name || '');
        if (!data) return;
        if (data.error) throw new Error(data.error);
        if (data.reading_url) {{
          window.open(data.reading_url, '_blank', 'noopener');
        }}
        showToast(data.message || '已加入深度阅读。');
      }} catch (error) {{
        showToast(error.message);
      }}
    }}

    async function expandReferences(index) {{
      const paper = papers.find((item) => item.csv_index === index);
      if (!paper) return;
      const doi = normalizeDoi(paper.doi);
      if (doi && expansionIndex[doi] && expansionIndex[doi].site_url) {{
        showToast('已找到现有相关论文页，正在打开...');
        window.open(expansionIndex[doi].site_url, '_blank', 'noopener');
        return;
      }}
      showToast('已进入扩展搜索队列，正在准备结果...');
      try {{
        const started = await apiPost('/api/papers/expand-references', {{
          search_slug: meta.slug || '',
          paper
        }});
        const job = started.job || {{}};
        if (!job.id) {{
          throw new Error('扩展搜索任务创建失败');
        }}
        const finished = job.status === 'completed' ? job : await waitReferenceExpansionJob(job.id);
        if (doi && finished.site_url) {{
          expansionIndex[doi] = {{ site_url: finished.site_url }};
        }}
        if (!finished.site_url) {{
          throw new Error('扩展搜索结果未生成链接');
        }}
        showToast('延展搜索页面已生成，正在打开...');
        render(input.value);
        window.open(finished.site_url, '_blank', 'noopener');
      }} catch (error) {{
        showToast(error.message);
      }}
    }}

    window.addCitation = addCitation;
    window.expandReferences = expandReferences;

    function cardHtml(paper) {{
      const content = text(paper.content || '暂无内容');
      const previewText = content.length > 900 ? content.slice(0, 900).trimEnd() + ' ...' : content;
      const lineMeta = [
        paper.matched_kw ? `CSV #${{paper.csv_index}} · 命中词：${{paper.matched_kw}}` : `CSV #${{paper.csv_index}}`,
        paper.venue || '',
        paper.year || '',
        paper.authors || ''
      ].filter(Boolean).join(' · ');
      const link = paper.url ? `<a href="${{esc(paper.url)}}" target="_blank" rel="noreferrer">原文链接</a>` : '';
      const doi = paper.doi ? `DOI: ${{esc(paper.doi)}}` : '';
      const normalizedDoi = normalizeDoi(paper.doi);
      const expansion = normalizedDoi ? expansionIndex[normalizedDoi] : null;
      const expanded = Boolean(expansion && expansion.site_url);
      const expandLabel = expanded ? '查看相关论文' : '延展搜索';
      const expandClass = expanded ? 'action secondary related' : 'action secondary';
      const relevanceLabel = text(paper.relevance_label).trim().toLowerCase();
      const reviewPills = [];
      if (relevanceLabel) {{
        const score = paper.relevance_score !== '' && paper.relevance_score !== null && paper.relevance_score !== undefined
          ? ` · ${{paper.relevance_score}}`
          : '';
        reviewPills.push(`<span class="review-pill ${{esc(relevanceLabel)}}">相关性：${{esc(relevanceLabel)}}${{esc(score)}}</span>`);
      }}
      for (const tag of (paper.autotags || [])) {{
        const value = text(tag).trim();
        if (!value) continue;
        reviewPills.push(`<button class="review-pill" type="button" data-autotag-filter="${{esc(value.toLowerCase())}}">${{esc(value)}}</button>`);
      }}
      const reviewReason = text(paper.review_reason).trim();
      return `
        <article class="card${{expanded ? ' expanded' : ''}}">
          <div class="card-head">
            <div class="idx">#${{paper.csv_index}}</div>
            <h2 class="paper-title">${{esc(paper.title)}}</h2>
          </div>
          <div class="paper-meta">${{esc(lineMeta)}}</div>
          ${{reviewPills.length ? `<div class="review-row">${{reviewPills.join('')}}</div>` : ''}}
          <div class="paper-meta">${{[doi, link].filter(Boolean).join(' · ')}}</div>
          ${{reviewReason ? `<div class="paper-meta">复核说明：${{esc(reviewReason)}}</div>` : ''}}
          <div class="content">${{esc(previewText || '暂无内容')}}</div>
          <div class="actions">
            <button class="action" type="button" onclick="addCitation(${{paper.csv_index}})">加入深度阅读</button>
            <button class="${{expandClass}}" type="button" onclick="expandReferences(${{paper.csv_index}})">${{expandLabel}}</button>
          </div>
        </article>
      `;
    }}

    function renderKeywordFilters() {{
      const values = uniqueMatchedKeywords();
      if (!kwFilters) return;
      if (!values.length) {{
        kwFilters.innerHTML = '';
        return;
      }}
      kwFilters.innerHTML = [
        `<button class="tag${{activeMatchedKw === 'all' ? ' active' : ''}}" type="button" data-matched-filter="all">全部命中词</button>`,
        ...values.map((kw) => `<button class="tag${{activeMatchedKw === kw.toLowerCase() ? ' active' : ''}}" type="button" data-matched-filter="${{esc(kw.toLowerCase())}}">${{esc(kw)}}</button>`)
      ].join('');
      kwFilters.querySelectorAll('[data-matched-filter]').forEach((button) => {{
        button.addEventListener('click', () => {{
          activeMatchedKw = button.dataset.matchedFilter || 'all';
          renderKeywordFilters();
          render(input.value);
        }});
      }});
    }}

    function renderRelevanceFilters() {{
      const values = uniqueRelevanceLabels();
      if (!relevanceFilters) return;
      if (!values.length) {{
        relevanceFilters.innerHTML = '';
        return;
      }}
      relevanceFilters.innerHTML = [
        `<button class="tag${{activeRelevance === 'all' ? ' active' : ''}}" type="button" data-relevance-filter="all">全部相关性</button>`,
        ...values.map((label) => `<button class="tag${{activeRelevance === label ? ' active' : ''}}" type="button" data-relevance-filter="${{esc(label)}}">${{esc(label)}}</button>`)
      ].join('');
      relevanceFilters.querySelectorAll('[data-relevance-filter]').forEach((button) => {{
        button.addEventListener('click', () => {{
          activeRelevance = button.dataset.relevanceFilter || 'all';
          renderRelevanceFilters();
          render(input.value);
        }});
      }});
    }}

    function renderAutotagFilters() {{
      const values = uniqueAutotags();
      if (!autotagFilters) return;
      if (!values.length) {{
        autotagFilters.innerHTML = '';
        return;
      }}
      autotagFilters.innerHTML = [
        `<button class="tag${{activeAutotag === 'all' ? ' active' : ''}}" type="button" data-autotag-group-filter="all">全部标签</button>`,
        ...values.map((tag) => `<button class="tag${{activeAutotag === tag.toLowerCase() ? ' active' : ''}}" type="button" data-autotag-group-filter="${{esc(tag.toLowerCase())}}">${{esc(tag)}}</button>`)
      ].join('');
      autotagFilters.querySelectorAll('[data-autotag-group-filter]').forEach((button) => {{
        button.addEventListener('click', () => {{
          activeAutotag = button.dataset.autotagGroupFilter || 'all';
          renderAutotagFilters();
          render(input.value);
        }});
      }});
    }}

    function render(query = '') {{
      const q = query.trim().toLowerCase();
      const byText = !q ? papers : papers.filter((paper) => {{
        const haystack = [
          paper.title,
          paper.content,
          paper.matched_kw,
          paper.venue,
          paper.authors,
          paper.relevance_label,
          (paper.autotags || []).join(' '),
          paper.review_reason
        ].join('\\n').toLowerCase();
        return haystack.includes(q);
      }});
      const filtered = byText.filter((paper) => {{
        const matchedOk = activeMatchedKw === 'all' || text(paper.matched_kw).trim().toLowerCase() === activeMatchedKw;
        const relevanceOk = activeRelevance === 'all' || text(paper.relevance_label).trim().toLowerCase() === activeRelevance;
        const autotagOk = activeAutotag === 'all' || (paper.autotags || []).some((tag) => text(tag).trim().toLowerCase() === activeAutotag);
        return matchedOk && relevanceOk && autotagOk;
      }});

      count.textContent = filtered.length;
      if (!filtered.length) {{
        list.innerHTML = '';
        list.appendChild(emptyTemplate.content.cloneNode(true));
        return;
      }}
      list.innerHTML = filtered.map(cardHtml).join('');
    }}

    async function loadExpansions() {{
      try {{
        const resp = await fetch('/api/expansions', {{ credentials: 'same-origin' }});
        const data = await resp.json().catch(() => ({{ ok: false }}));
        if (!resp.ok || data.ok === false) {{
          return;
        }}
        expansionIndex = data.items || {{}};
      }} catch (error) {{
        expansionIndex = {{}};
      }}
    }}

    loadReadingGroups().catch(() => {{}});

    input.addEventListener('input', (event) => render(event.target.value));
    renderKeywordFilters();
    renderRelevanceFilters();
    renderAutotagFilters();
    list.addEventListener('click', (event) => {{
      const button = event.target.closest('[data-autotag-filter]');
      if (!button) return;
      activeAutotag = button.dataset.autotagFilter || 'all';
      renderAutotagFilters();
      render(input.value);
    }});
    loadExpansions().finally(() => {{
      if (Object.keys(expansionIndex || {{}}).length) {{
        render(input.value);
      }}
    }});
  </script>
</body>
</html>
"""
    with open(output_path, "w", encoding="utf-8") as f:
        f.write(html)


def build_site_url(out_dir: str, site_path: str) -> str:
    base_url = (os.getenv("PUBLIC_SITE_BASE_URL") or "").strip().rstrip("/")
    if not base_url:
        host = (os.getenv("PUBLIC_SITE_HOST") or "").strip()
        port = (os.getenv("PUBLIC_SITE_PORT") or "").strip()
        if host and port:
            base_url = f"http://{host}:{port}"
        elif host:
            base_url = f"http://{host}"

    if base_url:
        relative_dir = os.path.relpath(out_dir, DATA_DIR)
        relative_dir = quote(relative_dir.replace(os.sep, "/"))
        return f"{base_url}/{relative_dir}/site/"

    try:
        relative_dir = Path(out_dir).resolve().relative_to(DATA_DIR.resolve()).as_posix()
        return f"/{quote(relative_dir, safe='/')}/site/"
    except Exception:
        return ""


def cap_papers_with_bucket_coverage(papers: list[dict], limit: int) -> tuple[list[dict], dict]:
    if len(papers) <= limit:
        return papers, {
            "strategy": "full",
            "limit": limit,
            "input_total": len(papers),
            "output_total": len(papers),
            "bucket_count": 0,
        }

    buckets: "OrderedDict[tuple[str, str], list[dict]]" = OrderedDict()
    for paper in papers:
        bucket_key = (
            str(paper.get("_matched_kw") or paper.get("matched_kw") or "").strip(),
            str(paper.get("_matched_venue") or paper.get("matched_venue") or "").strip(),
        )
        buckets.setdefault(bucket_key, []).append(paper)

    selected: list[dict] = []
    bucket_index = 0
    bucket_items = list(buckets.items())
    while len(selected) < limit:
        progressed = False
        for _, bucket_papers in bucket_items:
            if bucket_index < len(bucket_papers):
                selected.append(bucket_papers[bucket_index])
                progressed = True
                if len(selected) >= limit:
                    break
        if not progressed:
            break
        bucket_index += 1

    kw_coverage = sorted(
        {
            str(paper.get("_matched_kw") or paper.get("matched_kw") or "").strip()
            for paper in selected
            if str(paper.get("_matched_kw") or paper.get("matched_kw") or "").strip()
        }
    )
    venue_coverage = sorted(
        {
            str(paper.get("_matched_venue") or paper.get("matched_venue") or "").strip()
            for paper in selected
            if str(paper.get("_matched_venue") or paper.get("matched_venue") or "").strip()
        }
    )

    return selected, {
        "strategy": "round_robin_by_keyword_and_venue",
        "limit": limit,
        "input_total": len(papers),
        "output_total": len(selected),
        "bucket_count": len(bucket_items),
        "covered_keywords": kw_coverage,
        "covered_venues": venue_coverage,
    }


def run_topic_search(
    *,
    keywords: list[str],
    venues: list[str] | None,
    slug: str,
    top: int = 100,
    year_from: int = 0,
    fetch_abstract: bool = True,
    summary_name: str = "",
    progress_callback=None,
) -> dict:
    keyword_groups = [k.strip() for k in (keywords or []) if str(k).strip()]
    if not keyword_groups:
        raise ValueError("至少需要一组关键词。")

    venue_list = [v.strip() for v in (venues or []) if str(v).strip()]
    search_venues = venue_list or [None]
    slug = (slug or "").strip()
    if not slug:
        raise ValueError("slug 不能为空。")
    summary_name = " ".join(str(summary_name or "").split()) or build_search_summary_name(
        slug.replace("-", " "),
        " ".join(keyword_groups),
        " ".join(venue_list),
    )

    dated_slug, out_dir = _build_search_output_dir(slug)
    os.makedirs(out_dir, exist_ok=True)
    csv_path = os.path.join(out_dir, "papers.csv")
    json_path = os.path.join(out_dir, "papers.json")
    meta_path = os.path.join(out_dir, "search.json")
    site_path = os.path.join(out_dir, "site", "index.html")
    tmp_dir = os.path.join(str(TMP_SEARCH_BASE_DIR), f"{date.today().isoformat()}_{slug}_{int(time.time() * 1000)}_{secrets.token_hex(2)}")

    def report(stage: str, message: str, **extra):
        if callable(progress_callback):
            progress_callback(stage, message, extra)

    slot_index = None
    slot_lock = None
    owner_id = f"{slug}-{os.getpid()}-{int(time.time() * 1000)}-{secrets.token_hex(2)}"
    try:
        slot_index, slot_lock = _acquire_research_slot(owner_id, status_callback=report)
        report(
            "searching",
            f"开始搜索，共 {len(keyword_groups)} 组关键词 × {len(search_venues)} 个范围。",
            keywords=keyword_groups,
            venues=[v for v in search_venues if v],
            slot_index=slot_index,
            max_concurrent_jobs=MAX_CONCURRENT_RESEARCH_JOBS,
        )

        with open(meta_path, "w", encoding="utf-8") as f:
            json.dump(
                {
                    "slug": slug,
                    "summary_name": summary_name,
                    "group_name": summary_name,
                    "keywords": keyword_groups,
                    "venues": [v for v in search_venues if v],
                    "top_per_group": top,
                    "year_from": year_from,
                    "fetch_abstract": fetch_abstract,
                    "date": date.today().isoformat(),
                    "output_slug": dated_slug,
                },
                f,
                ensure_ascii=False,
                indent=2,
            )

        all_papers: list[dict] = []
        seen_keys: set[str] = set()
        hit_counts: dict[str, int] = {}
        fallback_events: list[dict] = []

        for kw in keyword_groups:
            kw_hits = 0
            for venue in search_venues:
                label = venue or "全局"
                print(f"[search] '{kw}'  venue={label} ...")
                diagnostics: dict[str, object] = {}
                papers = dblp_search(kw, venue, top, year_from, diagnostics)
                if papers is None:
                    print("[search] 本次 DBLP 请求失败，当前组合改用 OpenAlex 回退。")
                    fallback_events.append(
                        {
                            "keyword": kw,
                            "venue": venue or "",
                            "fallback_engine": "openalex",
                            "source_engine": "dblp",
                            "error_type": str(diagnostics.get("error_type") or ""),
                            "error": str(diagnostics.get("error") or ""),
                        }
                    )
                    papers = openalex_search(kw, venue, top, year_from)
                new = 0
                for p in papers:
                    key = p.get("key") or p.get("title", "")
                    if key not in seen_keys:
                        seen_keys.add(key)
                        p["_matched_kw"] = kw
                        p["_matched_venue"] = venue or ""
                        all_papers.append(p)
                        new += 1
                kw_hits += new
                print(f"[search]   命中 {len(papers)} 篇，新增 {new} 篇（去重后）")
                report(
                    "searching",
                    f"关键词“{kw}”在 {label} 中命中 {len(papers)} 篇，新增 {new} 篇。",
                    keyword=kw,
                    venue=venue or "",
                    hit_count=len(papers),
                    added_count=new,
                    slot_index=slot_index,
                )
                if len(search_venues) > 1 or len(keyword_groups) > 1:
                    time.sleep(random.uniform(1.0, 2.0))
            hit_counts[kw] = kw_hits

        all_papers.sort(key=lambda p: -(p.get("year") or 0))
        uncapped_total = len(all_papers)
        cap_summary = {
            "strategy": "full",
            "limit": MAX_TOTAL_SEARCH_RESULTS,
            "input_total": uncapped_total,
            "output_total": uncapped_total,
            "bucket_count": 0,
            "covered_keywords": sorted(hit_counts.keys()),
            "covered_venues": sorted(v for v in search_venues if v),
        }
        if uncapped_total > MAX_TOTAL_SEARCH_RESULTS:
            all_papers, cap_summary = cap_papers_with_bucket_coverage(all_papers, MAX_TOTAL_SEARCH_RESULTS)
            print(
                f"\n[search] 合并去重后共 {uncapped_total} 篇，"
                f"按覆盖优先策略保留 {len(all_papers)} 篇"
            )
        total = len(all_papers)
        if uncapped_total <= MAX_TOTAL_SEARCH_RESULTS:
            print(f"\n[search] 合并去重后共 {total} 篇")
        for kw, n in hit_counts.items():
            print(f"  '{kw}': {n} 篇")
        report(
            "search_completed",
            f"搜索完成，合并去重后共 {uncapped_total} 篇，最终保留 {total} 篇。",
            total_papers=total,
            uncapped_total_papers=uncapped_total,
            hit_counts=hit_counts,
            slot_index=slot_index,
        )

        if fetch_abstract:
            has_abstract = sum(1 for p in all_papers if p.get("abstract"))
            need = total - has_abstract
            if need > 0:
                est_min = round(need * 3 / 60)
                print(f"\n[search] 开始爬取摘要（{need} 篇，预计约 {est_min} 分钟）...")
                report(
                    "fetching_abstracts",
                    f"开始爬取摘要，共 {need} 篇，预计约 {est_min} 分钟。",
                    total_papers=total,
                    pending_abstracts=need,
                    estimated_minutes=est_min,
                    slot_index=slot_index,
                )
                all_papers = fetch_abstracts_for_papers(all_papers, tmp_dir)
                fetched = sum(1 for p in all_papers if p.get("abstract"))
                print(f"[search] 摘要爬取完成：{fetched}/{total} 篇获得摘要")
                report(
                    "abstracts_completed",
                    f"摘要获取完成：{fetched}/{total} 篇获得摘要。",
                    abstract_success=fetched,
                    total_papers=total,
                    slot_index=slot_index,
                )
            else:
                print("[search] 所有论文已有摘要，跳过爬取")
                report("abstracts_completed", "所有论文已有摘要，跳过爬取。", abstract_success=total, total_papers=total, slot_index=slot_index)
        else:
            print("[search] 已跳过摘要爬取（--no-abstract）")
            report("abstracts_skipped", "已按要求跳过摘要爬取。", total_papers=total, slot_index=slot_index)

        output_meta = {
            "slug": slug,
            "summary_name": summary_name,
            "group_name": summary_name,
            "keywords": keyword_groups,
            "venues": [v for v in search_venues if v],
            "top_per_group": top,
            "max_total_results": MAX_TOTAL_SEARCH_RESULTS,
            "cap_summary": cap_summary,
            "year_from": year_from,
            "fetch_abstract": fetch_abstract,
            "date": date.today().isoformat(),
            "output_slug": dated_slug,
            "total_papers": total,
            "uncapped_total_papers": uncapped_total,
            "fallback_events": fallback_events,
        }

        write_csv(all_papers, csv_path)
        json_records = build_json_records(all_papers)
        write_json(json_records, json_path, output_meta)
        write_site(json_records, site_path, output_meta)
        with open(meta_path, "w", encoding="utf-8") as f:
            json.dump(output_meta, f, ensure_ascii=False, indent=2)

        site_url = build_site_url(out_dir, site_path)
        report(
            "completed",
            "搜索结果、JSON 和静态网页已生成。",
            site_url=site_url,
            csv_path=csv_path,
            json_path=json_path,
            site_path=site_path,
            total_papers=total,
            slot_index=slot_index,
        )
        return {
            "out_dir": out_dir,
            "csv_path": csv_path,
            "json_path": json_path,
            "meta_path": meta_path,
            "site_path": site_path,
            "site_url": site_url,
            "records": json_records,
            "papers": all_papers,
            "meta": output_meta,
            "hit_counts": hit_counts,
            "total_papers": total,
        }
    finally:
        if os.path.isdir(tmp_dir):
            shutil.rmtree(tmp_dir, ignore_errors=True)
        if slot_lock is not None and slot_index is not None:
            _release_research_slot(slot_index, slot_lock)


def main():
    parser = argparse.ArgumentParser(description="DBLP 关键词搜索 → CSV")
    parser.add_argument("--keywords", required=True,
                        help="关键词，多组用分号分隔。示例: 'physiological notification;biosignal alert'")
    parser.add_argument("--venues", default="",
                        help="逗号分隔的会议缩写，空则全 DBLP 范围搜索")
    parser.add_argument("--slug", required=True,
                        help="话题简称，用于命名输出目录，仅限英文/数字/连字符。示例: physio-ui")
    parser.add_argument("--summary-name", default="",
                        help="2-3 words 的概括名，用于搜索分组和后续深度阅读 group")
    parser.add_argument("--top", type=int, default=100,
                        help="每组关键词 × 每个 venue 各取最多 N 篇，合并去重（默认 100）")
    parser.add_argument("--year-from", type=int, default=0,
                        help="最早年份，0 表示不限")
    parser.add_argument("--no-abstract", action="store_true",
                        help="跳过摘要爬取（默认会爬取摘要）")
    args = parser.parse_args()

    result = run_topic_search(
        keywords=[k.strip() for k in args.keywords.split(";") if k.strip()],
        venues=[v.strip() for v in args.venues.split(",") if v.strip()],
        slug=args.slug,
        summary_name=args.summary_name,
        top=args.top,
        year_from=args.year_from,
        fetch_abstract=not args.no_abstract,
    )
    print(f"\n[search] 完成。输出目录: {result['out_dir']}")
    print(f"  papers.csv : {result['csv_path']}")
    print(f"  papers.json: {result['json_path']}")
    print(f"  search.json: {result['meta_path']}")
    print(f"  site.html  : {result['site_path']}")
    print(f"  site_url   : {result['site_url']}")


if __name__ == "__main__":
    main()
