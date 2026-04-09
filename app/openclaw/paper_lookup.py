#!/home/ubuntu/miniconda3/envs/openclaw-analytics/bin/python
"""Paper lookup helpers for lightweight OpenClaw paper discovery workflows."""

import re
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse

import requests

from app.common import normalize_title, title_similarity
from app.pipeline.search import dblp_search

from .ingest import (
    DEFAULT_OPENCLAW_CONFIG_PATH,
    DEFAULT_OPENCLAW_IMAGE_FALLBACK_MODEL,
    DEFAULT_OPENCLAW_IMAGE_MODEL,
    OpenClawIngestError,
    extract_paper_candidate_from_image,
)

OFFICIAL_PUBLISHER_DOMAINS = {
    "doi.org": 12.0,
    "dl.acm.org": 11.0,
    "aclanthology.org": 11.0,
    "arxiv.org": 10.5,
    "openaccess.thecvf.com": 10.5,
    "proceedings.mlr.press": 10.5,
    "ieeexplore.ieee.org": 10.0,
    "link.springer.com": 10.0,
    "sciencedirect.com": 10.0,
    "www.sciencedirect.com": 10.0,
    "nature.com": 10.0,
    "www.nature.com": 10.0,
    "openreview.net": 9.5,
    "pubmed.ncbi.nlm.nih.gov": 9.0,
    "pmc.ncbi.nlm.nih.gov": 9.0,
}

LOW_TRUST_DOMAINS = {
    "researchgate.net": -8.0,
    "www.researchgate.net": -8.0,
    "scholar.google.com": -10.0,
    "semanticscholar.org": -3.0,
    "www.semanticscholar.org": -3.0,
}

GOOGLE_SCHOLAR_PROFILE_HOSTS = {
    "scholar.google.com",
    "scholar.googleusercontent.com",
}

def normalize_doi(value: str) -> str:
    text = " ".join(str(value or "").strip().split()).lower()
    for prefix in ("https://doi.org/", "http://doi.org/", "doi:"):
        if text.startswith(prefix):
            text = text[len(prefix) :]
    return text.strip().strip("/")


def is_google_scholar_profile_url(value: str) -> bool:
    text = " ".join(str(value or "").split()).strip()
    if not text:
        return False
    parsed = urlparse(text)
    host = (parsed.netloc or "").strip().lower()
    if host not in GOOGLE_SCHOLAR_PROFILE_HOSTS:
        return False
    path = (parsed.path or "").strip().lower()
    return path.startswith("/citations") and "user=" in parsed.query


def extract_titles_from_google_scholar_profile(url: str, *, limit: int = 100) -> list[str]:
    normalized = " ".join(str(url or "").split()).strip()
    if not is_google_scholar_profile_url(normalized):
        return []
    parsed = urlparse(normalized)
    query = parse_qs(parsed.query)
    user_id = (query.get("user") or [""])[0].strip()
    if not user_id:
        return []

    hl = (query.get("hl") or ["en"])[0].strip() or "en"
    base_url = f"{parsed.scheme or 'https'}://{parsed.netloc or 'scholar.google.com'}{parsed.path or '/citations'}"
    titles: list[str] = []
    seen: set[str] = set()
    page_size = 100
    user_agent = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    pattern = re.compile(r'<a[^>]+class="gsc_a_at"[^>]*>(.*?)</a>', re.I | re.S)

    for start in range(0, max(limit, 1), page_size):
        try:
            resp = requests.get(
                base_url,
                params={
                    "user": user_id,
                    "hl": hl,
                    "cstart": start,
                    "pagesize": min(page_size, max(limit, 1)),
                    "view_op": "list_works",
                },
                timeout=20,
                headers={"User-Agent": user_agent},
            )
            resp.raise_for_status()
        except Exception:
            break

        page_titles: list[str] = []
        for raw in pattern.findall(resp.text):
            title = re.sub(r"<.*?>", "", raw or "").strip()
            title = " ".join(title.split())
            key = normalize_title(title)
            if not title or not key or key in seen:
                continue
            seen.add(key)
            page_titles.append(title)
            titles.append(title)
            if len(titles) >= limit:
                return titles
        if not page_titles or len(page_titles) < page_size:
            break
    return titles


def split_textsearch_inputs(chunks: list[str]) -> list[str]:
    merged = "\n".join(str(chunk or "") for chunk in chunks)
    titles: list[str] = []
    for raw_line in merged.splitlines():
        line = " ".join(raw_line.split()).strip()
        if not line:
            continue
        line = re.sub(r"^\s*(?:[-*•]|\d+[.)])\s*", "", line).strip()
        if line:
            titles.append(line)
    if titles:
        return titles
    fallback = " ".join(merged.split()).strip()
    return [fallback] if fallback else []


def expand_textsearch_inputs(chunks: list[str], *, scholar_limit: int = 100) -> list[str]:
    items = split_textsearch_inputs(chunks)
    expanded: list[str] = []
    seen: set[str] = set()
    for item in items:
        candidates = (
            extract_titles_from_google_scholar_profile(item, limit=scholar_limit)
            if is_google_scholar_profile_url(item)
            else [item]
        )
        for title in candidates:
            key = normalize_title(title)
            if not key or key in seen:
                continue
            seen.add(key)
            expanded.append(title)
    return expanded


def search_paper_candidates_via_web(query: str, limit: int = 20) -> list[dict]:
    text = " ".join(str(query or "").split())
    if not text:
        return []
    try:
        resp = requests.get(
            "https://html.duckduckgo.com/html/",
            params={"q": text},
            timeout=20,
            headers={"User-Agent": "exScholar-picsearch/1.0"},
        )
        resp.raise_for_status()
    except Exception:
        return []

    results = []
    seen = set()
    pattern = re.compile(r'<a[^>]+class="result__a"[^>]+href="([^"]+)"[^>]*>(.*?)</a>', re.I | re.S)
    for href, title_html in pattern.findall(resp.text):
        title = re.sub(r"<.*?>", "", title_html or "").strip()
        url = href
        parsed = urlparse(href)
        if "duckduckgo.com" in parsed.netloc:
            target = parse_qs(parsed.query).get("uddg", [""])[0]
            if target:
                url = unquote(target)
        key = url.lower()
        if not url or key in seen:
            continue
        seen.add(key)
        results.append({"title": title, "url": url})
        if len(results) >= limit:
            break
    return results


def web_result_domain_score(url: str) -> float:
    host = (urlparse(url).netloc or "").strip().lower()
    if not host:
        return 0.0
    if host in OFFICIAL_PUBLISHER_DOMAINS:
        return OFFICIAL_PUBLISHER_DOMAINS[host]
    if host in LOW_TRUST_DOMAINS:
        return LOW_TRUST_DOMAINS[host]
    if host.endswith(".acm.org"):
        return 10.5
    if host.endswith(".ieee.org"):
        return 9.5
    if host.endswith(".springer.com"):
        return 9.5
    if host.endswith(".nature.com"):
        return 9.5
    if host.endswith(".elsevier.com"):
        return 9.5
    if host.endswith(".openreview.net"):
        return 9.0
    if host.endswith(".researchgate.net"):
        return -8.0
    return 0.0


def find_best_web_match(query_title: str, query: str, limit: int = 20) -> dict | None:
    results = search_paper_candidates_via_web(query, limit=limit)
    if not results:
        return None
    title = (query_title or "").strip()
    scored_results = []
    for item in results:
        similarity = title_similarity(title, item.get("title") or "") if title else 0.0
        domain_score = web_result_domain_score(item.get("url") or "")
        total_score = similarity * 100.0 + domain_score
        scored_results.append((total_score, similarity, domain_score, item))
    scored_results.sort(key=lambda row: (row[0], row[1], row[2]), reverse=True)
    best_total, best_similarity, _, best_item = scored_results[0]
    if title:
        if best_similarity >= 0.45:
            return best_item
        return None
    return best_item


def find_best_dblp_match(title: str, year: str = "") -> dict | None:
    records = dblp_search(title, None, 5, int(year) if str(year).isdigit() else 0) or []
    best = None
    best_score = 0.0
    for item in records:
        score = title_similarity(title, item.get("title") or "")
        if score > best_score:
            best = item
            best_score = score
    return best if best and best_score >= 0.72 else None


def build_lookup_record(candidate: dict, url: str, title_text: str = "", *, matched_kw: str = "picsearch") -> dict:
    title = (title_text or candidate.get("title") or candidate.get("query") or "").strip()
    return {
        "title": title,
        "venue": candidate.get("venue") or "",
        "year": int(candidate["year"]) if str(candidate.get("year") or "").isdigit() else candidate.get("year") or "",
        "authors": candidate.get("authors") or [],
        "doi": candidate.get("doi") or "",
        "ee": [url] if url else [],
        "abstract": "",
        "_matched_kw": matched_kw,
        "key": candidate.get("doi") or url or title,
        "paper_id": "",
    }


def resolve_paper_lookup_from_candidate(candidate: dict, *, matched_kw: str = "picsearch") -> dict:
    title = (candidate.get("title") or "").strip()
    query = (title or candidate.get("query") or "").strip()
    if not query:
        raise ValueError("没有提供足够清晰的论文标题。")

    dblp_record = find_best_dblp_match(title or query, candidate.get("year") or "")
    if dblp_record:
        dblp_record = dict(dblp_record)
        dblp_record["_matched_kw"] = matched_kw
        return {"candidate": candidate, "record": dblp_record, "source": "dblp"}

    best_web = find_best_web_match(title or query, query)
    if best_web:
        return {
            "candidate": candidate,
            "record": build_lookup_record(candidate, best_web.get("url") or "", best_web.get("title") or "", matched_kw=matched_kw),
            "source": "websearch",
        }

    doi = normalize_doi(candidate.get("doi") or "")
    if doi:
        doi_url = f"https://doi.org/{doi}"
        return {
            "candidate": candidate,
            "record": build_lookup_record(candidate, doi_url, title or query, matched_kw=matched_kw),
            "source": "doi_fallback",
        }

    raise ValueError("没有找到可用的论文链接。")


def resolve_paper_lookup_from_title(
    title: str,
    *,
    year: str = "",
    doi: str = "",
    matched_kw: str = "textsearch",
) -> dict:
    candidate = {
        "title": " ".join(str(title or "").split()),
        "query": " ".join(str(title or "").split()),
        "year": str(year or "").strip(),
        "doi": normalize_doi(doi or ""),
        "authors": [],
        "venue": "",
    }
    if not candidate["title"]:
        raise ValueError("没有提供论文标题。")
    return resolve_paper_lookup_from_candidate(candidate, matched_kw=matched_kw)


def resolve_paper_lookup_from_image(
    image_path: str | Path,
    *,
    model_id: str = DEFAULT_OPENCLAW_IMAGE_MODEL,
    fallback_model_id: str | None = DEFAULT_OPENCLAW_IMAGE_FALLBACK_MODEL,
    config_path: str | Path = DEFAULT_OPENCLAW_CONFIG_PATH,
    timeout: int = 180,
    matched_kw: str = "picsearch",
) -> dict:
    candidate = extract_paper_candidate_from_image(
        image_path,
        model_id=model_id,
        fallback_model_id=fallback_model_id,
        config_path=config_path,
        timeout=timeout,
    )
    return resolve_paper_lookup_from_candidate(candidate, matched_kw=matched_kw)


def resolve_paper_lookups_from_image(
    image_path: str | Path,
    *,
    model_id: str = DEFAULT_OPENCLAW_IMAGE_MODEL,
    fallback_model_id: str | None = DEFAULT_OPENCLAW_IMAGE_FALLBACK_MODEL,
    config_path: str | Path = DEFAULT_OPENCLAW_CONFIG_PATH,
    timeout: int = 180,
    matched_kw: str = "picsearch",
) -> dict:
    candidate = extract_paper_candidate_from_image(
        image_path,
        model_id=model_id,
        fallback_model_id=fallback_model_id,
        config_path=config_path,
        timeout=timeout,
    )
    kind = str(candidate.get("screenshot_kind") or "").strip().lower()
    titles = [
        " ".join(str(item or "").split()).strip()
        for item in (candidate.get("titles") or [])
        if " ".join(str(item or "").split()).strip()
    ]
    if kind == "scholar_list" and titles:
        results = []
        for title in titles:
            resolved = resolve_paper_lookup_from_title(title, matched_kw=matched_kw)
            results.append(resolved)
        return {"candidate": candidate, "results": results, "mode": "scholar_list"}
    return {
        "candidate": candidate,
        "results": [resolve_paper_lookup_from_candidate(candidate, matched_kw=matched_kw)],
        "mode": "single_paper",
    }


__all__ = [
    "OpenClawIngestError",
    "build_lookup_record",
    "expand_textsearch_inputs",
    "extract_titles_from_google_scholar_profile",
    "find_best_dblp_match",
    "find_best_web_match",
    "is_google_scholar_profile_url",
    "normalize_title",
    "normalize_doi",
    "resolve_paper_lookup_from_candidate",
    "resolve_paper_lookup_from_image",
    "resolve_paper_lookups_from_image",
    "resolve_paper_lookup_from_title",
    "split_textsearch_inputs",
    "search_paper_candidates_via_web",
    "title_similarity",
    "web_result_domain_score",
]
