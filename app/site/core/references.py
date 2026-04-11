"""Reference expansion, external APIs, and search listing helpers for exScholar."""

from app.common import get_no_proxy_session

from .shared import *

REFERENCE_JOB_LOCK = threading.Lock()
REFERENCE_STALE_JOB_TIMEOUT_SECONDS = 20 * 60
REFERENCE_STALE_JOB_MESSAGE = "扩展引用任务因服务重启或超时被中断，请重新发起。"


def ensure_reference_jobs_dir():
    REFERENCE_JOBS_DIR.mkdir(parents=True, exist_ok=True)


def build_relative_site_url(path: Path) -> str:
    return f"/{path.relative_to(DATA_DIR).as_posix()}/"


def build_reference_job_id() -> str:
    return f"xjob_{datetime.utcnow().strftime('%Y%m%d_%H%M%S')}_{secrets.token_hex(3)}"


def reference_job_path(job_id: str) -> Path:
    ensure_reference_jobs_dir()
    return REFERENCE_JOBS_DIR / f"{job_id}.json"


def parse_reference_job_timestamp(value: str) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        return datetime.strptime(text, "%Y-%m-%dT%H:%M:%SZ")
    except ValueError:
        return None


def summarize_reference_job(job: dict) -> dict:
    job["updated_at"] = utc_now()
    return job


def reconcile_reference_job_if_stale(job: dict, path: Path | None = None) -> dict:
    status = str(job.get("status") or "").strip().lower()
    if status in {"completed", "failed"}:
        return job
    updated_at = parse_reference_job_timestamp(job.get("updated_at") or job.get("created_at") or "")
    if not updated_at:
        return job
    age_seconds = (datetime.utcnow() - updated_at).total_seconds()
    if age_seconds < REFERENCE_STALE_JOB_TIMEOUT_SECONDS:
        return job
    job["status"] = "failed"
    job["current_step"] = "failed"
    job["step_message"] = REFERENCE_STALE_JOB_MESSAGE
    job["error"] = REFERENCE_STALE_JOB_MESSAGE
    if path is not None:
        with REFERENCE_JOB_LOCK:
            write_json_file_atomic(path, summarize_reference_job(job))
    return job


def save_reference_job(job: dict):
    with REFERENCE_JOB_LOCK:
        write_json_file_atomic(reference_job_path(job["id"]), summarize_reference_job(job))


def load_reference_job(job_id: str) -> dict | None:
    path = reference_job_path(job_id)
    if not path.exists():
        return None
    job = read_json_file(path, None)
    if not job:
        return None
    return reconcile_reference_job_if_stale(job, path)


def list_reference_jobs(limit: int = 20) -> list[dict]:
    ensure_reference_jobs_dir()
    jobs = []
    for path in sorted(REFERENCE_JOBS_DIR.glob("*.json"), reverse=True):
        payload = read_json_file(path, None)
        if not payload:
            continue
        jobs.append(reconcile_reference_job_if_stale(payload, path))
        if len(jobs) >= limit:
            break
    return jobs


def update_reference_job(job_id: str, mutate):
    with REFERENCE_JOB_LOCK:
        path = reference_job_path(job_id)
        job = read_json_file(path, None)
        if not job:
            raise ValueError("扩展引用任务不存在")
        mutate(job)
        write_json_file_atomic(path, summarize_reference_job(job))
        return job


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
        resp = get_no_proxy_session().get(
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


def fetch_reference_records(doi: str, limit: int, progress_callback=None) -> list[dict]:
    if progress_callback:
        progress_callback("crossref_lookup", "正在查询 Crossref 引用信息。")
    crossref_url = f"https://api.crossref.org/works/{doi}"
    try:
        resp = get_no_proxy_session().get(crossref_url, timeout=20, headers={"User-Agent": "ccf-crawler-site/1.0"})
        resp.raise_for_status()
        message = resp.json().get("message", {})
    except Exception:
        return []

    refs = message.get("reference", []) or []
    records = []
    sliced_refs = refs[:limit]
    for idx, ref in enumerate(sliced_refs, start=1):
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
            if progress_callback:
                progress_callback("openalex_enrich", f"正在补全引用元数据 {idx}/{len(sliced_refs)}。")
            try:
                openalex = get_no_proxy_session().get(
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
            "site_url": build_relative_site_url(site_path.parent),
            "slug": meta.get("slug", out_dir.name),
            "title": source_paper.get("title") or "",
            "source_slug": source_paper.get("source_slug") or "",
            "source_csv_index": source_paper.get("source_csv_index"),
            "expansion_source": meta.get("expansion_source") or "",
            "date": meta.get("date") or "",
        }
    return expansions


def create_reference_search(source_slug: str, paper: dict) -> str:
    return create_reference_search_with_progress(source_slug, paper)


def create_reference_search_with_progress(source_slug: str, paper: dict, progress_callback=None) -> str:
    title = (paper.get("title") or "").strip()
    doi = (paper.get("doi") or "").strip()
    source_kw = (paper.get("matched_kw") or "").strip()
    normalized_doi = normalize_doi(doi)
    if normalized_doi:
        existing = list_expansion_sites().get(normalized_doi)
        if existing:
            if progress_callback:
                progress_callback("completed", "已找到现有相关论文页。")
            return existing["site_url"]
    if progress_callback:
        progress_callback("ai4scholar_lookup", "正在查询 AI4Scholar 引用网络。")
    ref_records = fetch_ai4scholar_citation_records(paper, REFERENCE_LIMIT)
    keywords = [f"citations of {title}"]
    source_kind = "ai4scholar-citations"
    if not ref_records and doi:
        ref_records = fetch_reference_records(doi, REFERENCE_LIMIT, progress_callback=progress_callback)
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

    if progress_callback:
        progress_callback("writing_outputs", "正在整理扩展搜索结果。")
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
    refresh_keyword_graph_cache()
    return f"/{out_dir.relative_to(DATA_DIR).as_posix()}/site/"


def start_reference_expansion_job(search_slug: str, paper: dict) -> dict:
    title = " ".join(str((paper or {}).get("title") or "").split())
    doi = normalize_doi((paper or {}).get("doi") or "")
    normalized_slug = " ".join(str(search_slug or "").split()).strip("/")

    if doi:
        existing = list_expansion_sites().get(doi)
        if existing:
            job = {
                "id": build_reference_job_id(),
                "kind": "reference_expansion",
                "status": "completed",
                "current_step": "completed",
                "step_message": "已找到现有相关论文页。",
                "message": "扩展搜索结果已就绪。",
                "search_slug": normalized_slug,
                "paper_title": title,
                "paper_doi": doi,
                "site_url": existing.get("site_url") or "",
                "created_at": utc_now(),
                "updated_at": utc_now(),
                "finished_at": utc_now(),
                "logs": [{"at": utc_now(), "step": "completed", "message": "已找到现有相关论文页。"}],
            }
            save_reference_job(job)
            return job

    job = {
        "id": build_reference_job_id(),
        "kind": "reference_expansion",
        "status": "queued",
        "current_step": "queued",
        "step_message": "已收到扩展搜索请求，正在准备任务。",
        "message": "扩展搜索任务等待执行。",
        "search_slug": normalized_slug,
        "paper_title": title,
        "paper_doi": doi,
        "paper": dict(paper or {}),
        "site_url": "",
        "created_at": utc_now(),
        "updated_at": utc_now(),
        "logs": [{"at": utc_now(), "step": "queued", "message": "已收到扩展搜索请求，正在准备任务。"}],
    }
    save_reference_job(job)
    username = current_username()
    threading.Thread(
        target=run_reference_expansion_job,
        args=(job["id"], username),
        daemon=True,
    ).start()
    return job


def run_reference_expansion_job(job_id: str, username: str = ""):
    with user_context(username):
        _run_reference_expansion_job_inner(job_id)


def _run_reference_expansion_job_inner(job_id: str):
    def mark(step: str, message: str):
        def mutate(job):
            job["status"] = "running" if step != "completed" else "completed"
            job["current_step"] = step
            job["step_message"] = message
            job["message"] = message
            logs = job.setdefault("logs", [])
            logs.append({"at": utc_now(), "step": step, "message": message})
            job["logs"] = logs[-40:]
            if step == "completed":
                job["finished_at"] = utc_now()
        update_reference_job(job_id, mutate)

    job = load_reference_job(job_id)
    if not job:
        return
    search_slug = " ".join(str(job.get("search_slug") or "").split()).strip("/")
    paper = job.get("paper") or {}

    try:
        mark("running", "扩展搜索任务进行中。")
        site_url = create_reference_search_with_progress(search_slug, paper, progress_callback=mark)

        def finalize(payload):
            payload["status"] = "completed"
            payload["current_step"] = "completed"
            payload["step_message"] = "扩展搜索结果已生成。"
            payload["message"] = "扩展搜索结果已生成。"
            payload["site_url"] = site_url
            payload["finished_at"] = utc_now()
            logs = payload.setdefault("logs", [])
            logs.append({"at": utc_now(), "step": "completed", "message": "扩展搜索结果已生成。"})
            payload["logs"] = logs[-40:]
        update_reference_job(job_id, finalize)
    except Exception as exc:
        def fail(payload):
            payload["status"] = "failed"
            payload["current_step"] = "failed"
            payload["step_message"] = str(exc)
            payload["error"] = str(exc)
            payload["finished_at"] = utc_now()
            logs = payload.setdefault("logs", [])
            logs.append({"at": utc_now(), "step": "failed", "message": str(exc)})
            payload["logs"] = logs[-40:]
        update_reference_job(job_id, fail)


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


def delete_search_entry(relative_dir: str) -> bool:
    normalized = " ".join(str(relative_dir or "").split())
    if not normalized:
        return False
    target = (DATA_DIR / normalized.lstrip("/")).resolve()
    try:
        target.relative_to(DATA_DIR.resolve())
    except Exception:
        return False
    if not target.exists() or not target.is_dir():
        return False
    shutil.rmtree(target)
    return True
