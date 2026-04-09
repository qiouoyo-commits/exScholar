"""Daily webreading timeline helpers for image-based paper lookup."""

import json
from datetime import date
from pathlib import Path

from app.pipeline.search import build_json_records, build_site_url, write_csv, write_json, write_site

from .base import *
from .storage import read_json_file

PICSEARCH_KEYWORD = "picsearch"
WEBREADING_SLUG = "webreading"

def _normalize_title(value: str) -> str:
    text = " ".join(str(value or "").strip().lower().split())
    text = re.sub(r"[^a-z0-9]+", " ", text)
    return " ".join(text.split())


def _webreading_dir() -> Path:
    return SEARCHES_DIR / f"{date.today().isoformat()}_{WEBREADING_SLUG}"


def append_picsearch_timeline(record: dict) -> dict:
    out_dir = _webreading_dir()
    out_dir.mkdir(parents=True, exist_ok=True)
    csv_path = out_dir / "papers.csv"
    json_path = out_dir / "papers.json"
    search_json_path = out_dir / "search.json"
    site_path = out_dir / "site" / "index.html"

    existing_payload = read_json_file(json_path, {"papers": []})
    existing_records = existing_payload.get("papers") if isinstance(existing_payload, dict) else []
    if not isinstance(existing_records, list):
        existing_records = []

    raw_records = []
    for item in existing_records:
        raw_records.append(
            {
                "title": item.get("title") or "",
                "venue": item.get("venue") or "",
                "year": item.get("year") or "",
                "authors": [part.strip() for part in str(item.get("authors") or "").split(",") if part.strip()],
                "doi": item.get("doi") or "",
                "ee": [item.get("url")] if item.get("url") else [],
                "abstract": item.get("content") or "",
                "_matched_kw": item.get("matched_kw") or PICSEARCH_KEYWORD,
                "key": item.get("doi") or item.get("url") or item.get("title") or "",
                "paper_id": item.get("paper_id") or "",
            }
        )

    candidate_key = ((record.get("doi") or "").strip().lower(), _normalize_title(record.get("title") or ""))
    exists = False
    for item in raw_records:
        current_key = ((item.get("doi") or "").strip().lower(), _normalize_title(item.get("title") or ""))
        if candidate_key[0] and current_key[0] == candidate_key[0]:
            exists = True
            break
        if candidate_key[1] and current_key[1] == candidate_key[1]:
            exists = True
            break
    if not exists:
        payload = dict(record)
        payload["_matched_kw"] = PICSEARCH_KEYWORD
        raw_records.insert(0, payload)

    meta = {
        "slug": WEBREADING_SLUG,
        "keywords": [PICSEARCH_KEYWORD],
        "venues": [],
        "top_per_group": len(raw_records),
        "year_from": 0,
        "fetch_abstract": False,
        "date": date.today().isoformat(),
        "total_papers": len(raw_records),
        "notes": "Papers added from image-based lookup.",
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
        "slug": WEBREADING_SLUG,
    }
