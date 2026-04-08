"""Reference expansion, external APIs, and search listing helpers for exScholar."""

from .shared import *


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
