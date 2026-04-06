#!/usr/bin/env python3
import argparse
import json
import os
import re
from datetime import date, datetime
from difflib import SequenceMatcher
from pathlib import Path

from serve_searches import ROOT_DIR, list_keyword_entries


REVIEW_LOG_DIR = ROOT_DIR / "data" / "review_logs"


def slugify(text: str, fallback: str = "review") -> str:
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


def tokenize(text: str) -> list[str]:
    return [token for token in re.split(r"[^a-z0-9]+", (text or "").lower()) if token]


def score_keyword(query: str, keyword: str) -> float:
    query_norm = (query or "").strip().lower()
    keyword_norm = (keyword or "").strip().lower()
    if not query_norm or not keyword_norm:
        return 0.0

    q_tokens = set(tokenize(query_norm))
    k_tokens = set(tokenize(keyword_norm))
    overlap = len(q_tokens & k_tokens)
    ratio = SequenceMatcher(None, query_norm, keyword_norm).ratio()
    contains_bonus = 0.0
    if keyword_norm in query_norm or query_norm in keyword_norm:
        contains_bonus = 1.0

    return overlap * 2.0 + ratio + contains_bonus


def rank_keywords(query: str, entries: list[dict], top_keywords: int) -> list[dict]:
    scored = []
    for entry in entries:
        score = score_keyword(query, entry["keyword"])
        if score <= 0:
            continue
        scored.append(
            {
                "keyword": entry["keyword"],
                "count": entry["count"],
                "latest_date": entry.get("latest_date") or "",
                "score": round(score, 4),
            }
        )
    scored.sort(key=lambda item: (item["score"], item["latest_date"], item["count"]), reverse=True)
    return scored[:top_keywords]


def dedupe_papers(papers: list[dict]) -> list[dict]:
    seen = set()
    deduped = []
    for paper in papers:
        key = (
            (paper.get("doi") or "").strip().lower()
            or f"{(paper.get('title') or '').strip().lower()}::{paper.get('year') or ''}"
        )
        if not key or key in seen:
            continue
        seen.add(key)
        deduped.append(paper)
    return deduped


def collect_papers(selected_keywords: list[str], max_papers: int) -> tuple[list[dict], list[dict]]:
    entries = list_keyword_entries()
    selected = {kw.lower(): kw for kw in selected_keywords}
    matched_entries = [entry for entry in entries if entry["keyword"].lower() in selected]
    papers = []
    for entry in matched_entries:
        for paper in entry["papers"]:
            item = dict(paper)
            item["matched_kw"] = entry["keyword"]
            papers.append(item)
    papers.sort(
        key=lambda item: (
            item.get("source_date") or "",
            item.get("year") or "",
            item.get("title") or "",
        ),
        reverse=True,
    )
    papers = dedupe_papers(papers)[:max_papers]
    return matched_entries, papers


def build_citations(papers: list[dict]) -> list[dict]:
    citations = []
    for idx, paper in enumerate(papers, start=1):
        citations.append(
            {
                "id": idx,
                "title": paper.get("title") or "",
                "authors": paper.get("authors") or "",
                "year": paper.get("year") or "",
                "venue": paper.get("venue") or "",
                "doi": paper.get("doi") or "",
                "url": paper.get("url") or "",
                "matched_kw": paper.get("matched_kw") or "",
                "source_slug": paper.get("source_slug") or "",
                "source_date": paper.get("source_date") or "",
            }
        )
    return citations


def write_json(path: Path, payload: dict | list):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def main():
    parser = argparse.ArgumentParser(description="Match review query to existing keywords and export papers.")
    parser.add_argument("--query", default="", help="User review question / topic description.")
    parser.add_argument("--keywords", default="", help="Semicolon-separated explicit keywords to use.")
    parser.add_argument("--top-keywords", type=int, default=5, help="Top matched keywords to keep.")
    parser.add_argument("--max-papers", type=int, default=40, help="Maximum papers to export.")
    parser.add_argument("--slug", default="", help="Optional review slug.")
    parser.add_argument("--list-only", action="store_true", help="Only print available keywords and counts.")
    args = parser.parse_args()

    keyword_entries = list_keyword_entries()
    if args.list_only:
        payload = [
            {
                "keyword": entry["keyword"],
                "count": entry["count"],
                "latest_date": entry.get("latest_date") or "",
            }
            for entry in keyword_entries
        ]
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return

    explicit_keywords = [part.strip() for part in args.keywords.split(";") if part.strip()]
    ranked = rank_keywords(args.query, keyword_entries, args.top_keywords)
    selected_keywords = explicit_keywords or [item["keyword"] for item in ranked]
    if not selected_keywords:
        raise SystemExit("No matched keywords found. Try --keywords explicitly or broaden the query.")

    matched_entries, papers = collect_papers(selected_keywords, args.max_papers)
    citations = build_citations(papers)

    slug_source = args.slug or args.query or "-".join(selected_keywords)
    out_dir = REVIEW_LOG_DIR / f"{date.today().isoformat()}_{slugify(slug_source, 'review')}"
    out_dir.mkdir(parents=True, exist_ok=True)

    request_payload = {
        "generated_at": datetime.utcnow().isoformat(timespec="seconds") + "Z",
        "query": args.query,
        "explicit_keywords": explicit_keywords,
        "matched_keywords": ranked,
        "selected_keywords": selected_keywords,
        "total_papers": len(papers),
        "max_papers": args.max_papers,
        "top_keywords": args.top_keywords,
    }
    write_json(out_dir / "request.json", request_payload)
    write_json(
        out_dir / "papers.json",
        {
            "query": args.query,
            "selected_keywords": selected_keywords,
            "papers": papers,
        },
    )
    write_json(out_dir / "citations.json", {"citations": citations})

    review_md = out_dir / "review.md"
    if not review_md.exists():
        review_md.write_text(
            "# Review Draft\n\n请由 OpenClaw 根据 request.json、papers.json、citations.json 撰写综述，并把正文写回这里。\n",
            encoding="utf-8",
        )

    result = {
        "out_dir": str(out_dir),
        "request_json": str(out_dir / "request.json"),
        "papers_json": str(out_dir / "papers.json"),
        "citations_json": str(out_dir / "citations.json"),
        "review_md": str(review_md),
        "selected_keywords": selected_keywords,
        "total_papers": len(papers),
    }
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
