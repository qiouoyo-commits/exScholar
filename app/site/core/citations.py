"""Citation, keyword, grouping, and library helpers for exScholar."""

from .base import *
from .storage import *


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
            entry = grouped.setdefault(key, {"keyword": keyword, "count": 0, "papers": [], "latest_date": ""})
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
            entry = grouped.setdefault(key, {"keyword": keyword, "count": 0, "papers": [], "latest_date": ""})
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
        conn.execute("UPDATE citations SET tags = ? WHERE id = ?", (normalize_tags(tags), citation_id))
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
        conn.execute("UPDATE citations SET reading_paper_id = ? WHERE id = ?", (paper_id or None, citation_id))
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
        conn.execute("DELETE FROM citation_group_links WHERE citation_id = ? AND group_id = ?", (citation_id, group_id))
        conn.commit()


def list_citations_with_groups():
    ensure_db()
    citations = list_citations()
    for citation in citations:
        citation["groups"] = get_citation_groups(citation["id"])
    return citations


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
    file_item.file.seek(0)
    header = file_item.file.read(5)
    file_item.file.seek(0)
    is_pdf_payload = header.startswith(b"%PDF-")
    if suffix != ".pdf" and content_type != "application/pdf" and not is_pdf_payload:
        raise ValueError("仅支持上传 PDF 文件。")
    pdf_sha256 = compute_stream_sha256(file_item.file)
    existing = find_existing_pdf_by_hash(pdf_sha256)
    if existing:
        return {"pdf_path": existing["pdf_path"], "pdf_sha256": pdf_sha256, "reused": True}
    stem = safe_file_stem(title or Path(filename).stem or "paper")
    unique_name = f"{datetime.utcnow().strftime('%Y%m%d-%H%M%S')}-{secrets.token_hex(4)}-{stem}.pdf"
    dest = LIBRARY_DIR / unique_name
    file_item.file.seek(0)
    with dest.open("wb") as fh:
        shutil.copyfileobj(file_item.file, fh)
    return {"pdf_path": f"library/{unique_name}", "pdf_sha256": pdf_sha256, "reused": False}


def store_pdf_bytes(pdf_bytes: bytes, title: str = "", filename_hint: str = "") -> dict:
    ensure_db()
    payload = pdf_bytes or b""
    if not payload:
        raise ValueError("empty_response")
    if not payload.startswith(b"%PDF-"):
        raise ValueError("response_not_pdf")

    stream = io.BytesIO(payload)
    pdf_sha256 = compute_stream_sha256(stream)
    existing = find_existing_pdf_by_hash(pdf_sha256)
    if existing:
        return {"pdf_path": existing["pdf_path"], "pdf_sha256": pdf_sha256, "reused": True}

    hint_name = Path((filename_hint or "").split("?", 1)[0]).name
    stem = safe_file_stem(title or Path(hint_name).stem or "paper")
    unique_name = f"{datetime.utcnow().strftime('%Y%m%d-%H%M%S')}-{secrets.token_hex(4)}-{stem}.pdf"
    dest = LIBRARY_DIR / unique_name
    with dest.open("wb") as fh:
        fh.write(payload)
    return {"pdf_path": f"library/{unique_name}", "pdf_sha256": pdf_sha256, "reused": False}


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
