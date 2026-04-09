"""Daily lookup timeline helpers for lightweight paper lookup workflows."""

import json
from datetime import date
from pathlib import Path

from app.common import normalize_title
from app.pipeline.search import (
    build_json_records,
    build_search_summary_name,
    build_site_url,
    fetch_abstracts_for_papers,
    write_csv,
    write_json,
    write_site,
)

from .base import *
from .storage import read_json_file

PICSEARCH_KEYWORD = "picsearch"
TEXTSEARCH_KEYWORD = "textsearch"
PICSEARCH_SLUG = "Picsearch"
TEXTSEARCH_SLUG = "Textsearch"
LOOKUP_COLLECTION_SUFFIX = "Collection"


def _lookup_dir(slug: str) -> Path:
    return SEARCHES_DIR / f"{date.today().isoformat()}_{slug}"


def _timeline_config(keyword: str) -> tuple[str, str]:
    low = str(keyword or "").strip().lower()
    if low == PICSEARCH_KEYWORD:
        return PICSEARCH_SLUG, "Papers added from image-based lookup."
    if low == TEXTSEARCH_KEYWORD:
        return TEXTSEARCH_SLUG, "Papers added from text-based lookup."
    return build_search_summary_name(low or "lookup"), "Papers added from lightweight lookup."


def suggest_lookup_group_name(records: list[dict] | dict, *, keyword: str = "") -> str:
    items = records if isinstance(records, list) else [records]
    items = [item for item in items if isinstance(item, dict)]
    autotag_sources: list[str] = []
    autotag_counts: dict[str, tuple[int, str]] = {}
    title_sources: list[str] = []
    venue_sources: list[str] = []
    for item in items[:6]:
        for tag in item.get("autotags") or []:
            value = " ".join(str(tag or "").split())
            if value:
                autotag_sources.append(value)
                low = value.lower()
                count, original = autotag_counts.get(low, (0, value))
                autotag_counts[low] = (count + 1, original)
        title = " ".join(str(item.get("title") or "").split())
        if title:
            title_sources.append(title)
        venue = " ".join(str(item.get("venue") or "").split())
        if venue:
            venue_sources.append(venue)
    repeated_tags = sorted(
        (item for item in autotag_counts.values() if item[0] >= 2 and 1 <= len(item[1].split()) <= 4),
        key=lambda item: (-item[0], len(item[1]), item[1].lower()),
    )
    if repeated_tags:
        return repeated_tags[0][1]
    concise_tags = [tag for tag in autotag_sources if 1 <= len(tag.split()) <= 4]
    if concise_tags:
        return concise_tags[0]
    summary = build_search_summary_name(*(autotag_sources + title_sources + venue_sources))
    if summary and summary != "Research Topic":
        return summary
    fallback = build_search_summary_name(keyword or "lookup", *(title_sources[:2]))
    if fallback and fallback != "Research Topic":
        return fallback
    base = PICSEARCH_SLUG if keyword == PICSEARCH_KEYWORD else TEXTSEARCH_SLUG if keyword == TEXTSEARCH_KEYWORD else "Lookup"
    return f"{base} {LOOKUP_COLLECTION_SUFFIX}"


def _normalize_lookup_record(item: dict, *, keyword: str) -> dict:
    return {
        "title": item.get("title") or "",
        "venue": item.get("venue") or "",
        "year": item.get("year") or "",
        "authors": item.get("authors") or [],
        "doi": item.get("doi") or "",
        "ee": item.get("ee") or ([item.get("url")] if item.get("url") else []),
        "abstract": item.get("abstract") or item.get("content") or "",
        "_matched_kw": item.get("_matched_kw") or item.get("matched_kw") or keyword,
        "key": item.get("key") or item.get("doi") or item.get("url") or item.get("title") or "",
        "paper_id": item.get("paper_id") or item.get("paperId") or "",
        "_source_engine": item.get("_source_engine") or item.get("source_engine") or "",
        "relevance_label": item.get("relevance_label") or "",
        "relevance_score": item.get("relevance_score") or "",
        "autotags": item.get("autotags") or [],
        "review_reason": item.get("review_reason") or "",
    }


def _record_match_key(item: dict) -> tuple[str, str]:
    return (
        (item.get("doi") or "").strip().lower(),
        normalize_title(item.get("title") or ""),
    )


def append_lookup_timeline(records: list[dict] | dict, *, keyword: str, notes: str) -> dict:
    timeline_slug, default_notes = _timeline_config(keyword)
    out_dir = _lookup_dir(timeline_slug)
    out_dir.mkdir(parents=True, exist_ok=True)
    csv_path = out_dir / "papers.csv"
    json_path = out_dir / "papers.json"
    search_json_path = out_dir / "search.json"
    site_path = out_dir / "site" / "index.html"
    tmp_dir = current_data_dir() / "tmp_lookup_abstracts"
    tmp_dir.mkdir(parents=True, exist_ok=True)

    incoming_records = records if isinstance(records, list) else [records]
    incoming_records = [item for item in incoming_records if isinstance(item, dict)]

    existing_payload = read_json_file(json_path, {"papers": []})
    existing_records = existing_payload.get("papers") if isinstance(existing_payload, dict) else []
    if not isinstance(existing_records, list):
        existing_records = []

    raw_records = []
    existing_keys: set[tuple[str, str]] = set()
    for item in existing_records:
        normalized = _normalize_lookup_record(item, keyword=keyword)
        normalized["authors"] = [part.strip() for part in str(item.get("authors") or "").split(",") if part.strip()] if not isinstance(normalized["authors"], list) else normalized["authors"]
        raw_records.append(normalized)
        existing_keys.add(_record_match_key(normalized))

    added_records: list[dict] = []
    for record in incoming_records:
        payload = _normalize_lookup_record(record, keyword=keyword)
        candidate_key = _record_match_key(payload)
        if candidate_key not in existing_keys:
            raw_records.insert(0, payload)
            added_records.append(payload)
            existing_keys.add(candidate_key)

    records_needing_abstract = [
        dict(item)
        for item in added_records
        if not str(item.get("abstract") or "").strip()
    ]
    if records_needing_abstract:
        try:
            fetched_records = fetch_abstracts_for_papers(records_needing_abstract, str(tmp_dir), max_concurrent=2)
            fetched_by_key = {_record_match_key(item): item for item in fetched_records}
            updated_records = []
            for item in raw_records:
                updated_records.append(fetched_by_key.get(_record_match_key(item), item))
            raw_records = updated_records
        except Exception:
            pass

    group_name = suggest_lookup_group_name(added_records or incoming_records or raw_records, keyword=keyword)
    meta = {
        "slug": timeline_slug,
        "output_slug": timeline_slug,
        "summary_name": timeline_slug,
        "group_name": group_name,
        "keywords": [keyword],
        "venues": [],
        "top_per_group": len(raw_records),
        "year_from": 0,
        "fetch_abstract": True,
        "date": date.today().isoformat(),
        "total_papers": len(raw_records),
        "notes": notes or default_notes,
    }
    write_csv(raw_records, str(csv_path))
    json_records = build_json_records(raw_records)
    write_json(json_records, str(json_path), meta)
    write_site(json_records, str(site_path), meta)
    search_json_path.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    return {
        "site_url": build_site_url(str(out_dir), str(site_path)),
        "relative_site_url": f"/{out_dir.relative_to(DATA_DIR).as_posix()}/site/",
        "total_papers": len(raw_records),
        "slug": timeline_slug,
        "group_name": group_name,
        "raw_records": raw_records,
        "records": json_records,
    }


def append_picsearch_timeline(record: dict) -> dict:
    return append_lookup_timeline(
        record,
        keyword=PICSEARCH_KEYWORD,
        notes="Papers added from image-based lookup.",
    )


def append_textsearch_timeline(record: dict) -> dict:
    return append_lookup_timeline(
        record,
        keyword=TEXTSEARCH_KEYWORD,
        notes="Papers added from text-based lookup.",
    )


def append_titlesearch_timeline(record: dict) -> dict:
    return append_textsearch_timeline(record)
