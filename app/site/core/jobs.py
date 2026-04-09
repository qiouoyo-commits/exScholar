"""OpenClaw job orchestration and batch reading helpers for exScholar."""

from app.openclaw import (
    DEFAULT_OPENCLAW_IMAGE_FALLBACK_MODEL,
    DEFAULT_OPENCLAW_IMAGE_MODEL,
    generate_keywords_from_metadata,
    model_http_transport_mode,
    resolve_paper_lookup_from_image,
    resolve_paper_lookups_from_image,
    resolve_paper_lookup_from_title,
)

from .picsearch import append_picsearch_timeline, append_textsearch_timeline, append_titlesearch_timeline
from .shared import *


def ensure_openclaw_jobs_dir():
    OPENCLAW_JOBS_DIR.mkdir(parents=True, exist_ok=True)


OPENCLAW_STALE_JOB_TIMEOUT_SECONDS = 30 * 60
OPENCLAW_STALE_JOB_MESSAGE = "任务因服务重启或超时被中断，请重新发起。"


def build_openclaw_job_id() -> str:
    return f"ocjob_{datetime.utcnow().strftime('%Y%m%d_%H%M%S')}_{secrets.token_hex(3)}"


def openclaw_job_path(job_id: str) -> Path:
    ensure_openclaw_jobs_dir()
    return OPENCLAW_JOBS_DIR / f"{job_id}.json"


def parse_openclaw_job_timestamp(value: str) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        return datetime.strptime(text, "%Y-%m-%dT%H:%M:%SZ")
    except ValueError:
        return None


def refresh_openclaw_job_counters(job: dict, *, touch_updated: bool = False) -> dict:
    items = job.get("items") or []
    total = len(items)
    completed = sum(1 for item in items if (item.get("status") or "") == "completed")
    failed = sum(1 for item in items if (item.get("status") or "") == "failed")
    running = sum(1 for item in items if (item.get("status") or "") == "running")
    queued = sum(1 for item in items if (item.get("status") or "") == "queued")
    job["total"] = total
    job["completed"] = completed
    job["failed"] = failed
    job["running_count"] = running
    job["queued"] = queued
    if touch_updated:
        job["updated_at"] = utc_now()
    return job


def summarize_openclaw_job(job: dict) -> dict:
    return refresh_openclaw_job_counters(job, touch_updated=True)


def mark_stale_openclaw_reading_item(item: dict, reason: str):
    paper_id = str(item.get("paper_id") or "").strip()
    if not paper_id:
        return
    bundle = load_reading_bundle(paper_id)
    if not bundle:
        return
    paper_json_path = Path(bundle["workspace"]) / "paper.json"
    actions = item.get("requested_actions") or ["metadata", "analysis"]
    current_status = ((bundle.get("paper") or {}).get("status") or {})
    if "metadata" in actions:
        if (current_status.get("metadata") or "").strip() == "processing":
            update_paper_metadata_progress(
                paper_json_path,
                state="failed",
                message=reason,
            )
    if "analysis" in actions:
        if (current_status.get("analysis") or "").strip() == "in_progress":
            progress = current_status.get("analysis_progress")
            update_paper_analysis_progress(
                paper_json_path,
                state="failed",
                progress=progress if isinstance(progress, int) else 0,
                stage="failed",
                message=reason,
            )


def reconcile_openclaw_job_if_stale(job: dict, path: Path | None = None) -> dict:
    refresh_openclaw_job_counters(job, touch_updated=False)
    if not job.get("running"):
        return job
    updated_at = parse_openclaw_job_timestamp(job.get("updated_at") or job.get("created_at") or "")
    if not updated_at:
        return job
    age_seconds = (datetime.utcnow() - updated_at).total_seconds()
    if age_seconds < OPENCLAW_STALE_JOB_TIMEOUT_SECONDS:
        return job
    reason = OPENCLAW_STALE_JOB_MESSAGE
    kind = (job.get("kind") or "").strip()
    changed = False
    for item in (job.get("items") or []):
        item_status = (item.get("status") or "").strip()
        if item_status not in {"queued", "running"}:
            continue
        item["status"] = "failed"
        item["current_step"] = "failed"
        item["step_message"] = reason
        item["error"] = reason
        changed = True
        if kind in {"openclaw_refresh_paper", "openclaw_batch_pdf_intake"}:
            mark_stale_openclaw_reading_item(item, reason)
    if not changed:
        return job
    job["running"] = False
    job["status"] = "completed_with_errors"
    job["message"] = reason
    job["finished_at"] = utc_now()
    job["current_index"] = None
    job["current_title"] = ""
    summarize_openclaw_job(job)
    if path is not None:
        with OPENCLAW_JOB_LOCK:
            write_json_file_atomic(path, job)
    return job


def save_openclaw_job(job: dict):
    with OPENCLAW_JOB_LOCK:
        payload = summarize_openclaw_job(job)
        write_json_file_atomic(openclaw_job_path(payload["id"]), payload)


def load_openclaw_job(job_id: str) -> dict | None:
    path = openclaw_job_path(job_id)
    if not path.exists():
        return None
    job = read_json_file(path, None)
    if not job:
        return None
    return reconcile_openclaw_job_if_stale(job, path)


def list_openclaw_jobs(limit: int = 20) -> list[dict]:
    ensure_openclaw_jobs_dir()
    jobs = []
    for path in sorted(OPENCLAW_JOBS_DIR.glob("*.json"), reverse=True):
        job = read_json_file(path, None)
        if not job:
            continue
        jobs.append(reconcile_openclaw_job_if_stale(job, path))
        if len(jobs) >= limit:
            break
    return jobs


def update_openclaw_job(job_id: str, mutate):
    with OPENCLAW_JOB_LOCK:
        path = openclaw_job_path(job_id)
        job = read_json_file(path, None)
        if not job:
            raise ValueError("OpenClaw 导入任务不存在")
        mutate(job)
        summarize_openclaw_job(job)
        write_json_file_atomic(path, job)
        return job


def sync_reading_workspace_metadata(citation_id: int, reading: dict, metadata: dict, *, page_count: int | None = None):
    refreshed = get_citation_by_id(citation_id)
    if not refreshed:
        return
    paper_json_path = Path(reading["paper_json_path"])
    paper = read_json_file(paper_json_path, {})
    paper["title"] = refreshed.get("title") or metadata.get("title") or paper.get("title") or ""
    paper["authors"] = split_authors(refreshed.get("authors") or ", ".join(metadata.get("authors") or []))
    paper["year"] = refreshed.get("year") or metadata.get("year") or paper.get("year")
    paper["venue"] = refreshed.get("venue") or metadata.get("venue") or paper.get("venue") or ""
    paper["doi"] = refreshed.get("doi") or metadata.get("doi") or paper.get("doi")
    paper["keywords"] = [part.strip() for part in (refreshed.get("tags") or "").split(",") if part.strip()]
    pdf_meta = paper.setdefault("pdf", {})
    if page_count is not None:
        pdf_meta["page_count"] = page_count
    paper["updated_at"] = utc_now()
    write_json_file(paper_json_path, paper)
    update_paper_metadata_progress(paper_json_path, state="completed", message="OpenClaw 元数据识别完成。")


def persist_openclaw_analysis_outputs(reading: dict, extracted: dict, analysis_result: dict):
    workspace = Path(reading["workspace"])
    paper_json_path = Path(reading["paper_json_path"])
    analysis_json_path = Path(reading["analysis_json_path"])
    source_dir = workspace / "source"
    full_text_json = source_dir / "full_text.json"
    sections_json = source_dir / "sections.json"
    write_json_file(
        full_text_json,
        {
            "text": extracted.get("text") or "",
            "pages": extracted.get("pages") or [],
            "page_count": extracted.get("page_count") or 0,
            "generated_by": "openclaw_ingest",
            "model": OPENCLAW_INGEST_MODEL,
        },
    )
    write_json_file(
        sections_json,
        {
            "sections": extracted.get("sections") or [],
            "generated_by": "openclaw_ingest",
            "model": OPENCLAW_INGEST_MODEL,
        },
    )
    final_analysis = coerce_analysis_result(analysis_result, reading["paper_id"])
    write_json_file(analysis_json_path, final_analysis)
    merge_analysis_theme_into_citation(reading["paper_id"], final_analysis)
    update_paper_status(paper_json_path, ingestion="completed", analysis="completed")
    update_paper_analysis_progress(
        paper_json_path,
        state="completed",
        progress=100,
        stage="completed",
        message="OpenClaw 结构化分析完成。",
    )
    paper = read_json_file(paper_json_path, {})
    pdf_meta = paper.setdefault("pdf", {})
    if extracted.get("page_count"):
        pdf_meta["page_count"] = extracted["page_count"]
    paper["updated_at"] = utc_now()
    write_json_file(paper_json_path, paper)
    return final_analysis


# OpenClaw addon intake and refresh pipeline
def process_openclaw_intake_item(job_id: str, index: int):
    job = load_openclaw_job(job_id)
    if not job:
        raise ValueError("OpenClaw 导入任务不存在")
    item = (job.get("items") or [])[index]
    citation_id = int(item["citation_id"])
    pdf_rel = (item.get("pdf_path") or "").strip()
    if not pdf_rel:
        raise ValueError("任务缺少 PDF 路径")
    pdf_abs = DATA_DIR / pdf_rel
    if not pdf_abs.exists():
        raise ValueError(f"PDF 文件不存在: {pdf_rel}")

    citation = get_citation_by_id(citation_id)
    if not citation:
        raise ValueError("对应 citation 不存在")
    requested_actions = item.get("requested_actions") or ["metadata", "analysis"]
    run_metadata = "metadata" in requested_actions
    run_analysis = "analysis" in requested_actions

    reading = ensure_reading_workspace_for_citation(citation_id)
    paper_json_path = Path(reading["paper_json_path"])

    def update_step(step: str, message: str):
        def mutate(payload):
            item_payload = payload["items"][index]
            item_payload["current_step"] = step
            item_payload["step_message"] = message
            payload["message"] = message
            payload["current_title"] = item_payload.get("filename") or item_payload.get("title") or ""
        update_openclaw_job(job_id, mutate)

    if run_metadata:
        update_step("metadata_processing", "正在识别元数据。")
        update_paper_metadata_progress(paper_json_path, state="processing", message="OpenClaw 正在识别元数据。")
    if run_analysis:
        update_paper_analysis_progress(
            paper_json_path,
            state="in_progress",
            progress=5,
            stage="extracting_pdf",
            message="正在提取 PDF 文本。",
        )

    update_step("extracting_pdf", "正在提取 PDF 文本。")
    extracted = extract_pdf_bundle(pdf_abs)
    metadata = {}
    target_citation_id = citation_id
    if run_metadata:
        update_step("extracting_metadata", f"正在调用主模型提取元数据，并由检查模型复核。（model_http={model_http_transport_mode()}）")
        metadata = extract_metadata_from_text(
            extracted.get("text") or "",
            filename=pdf_abs.name,
            model_id=OPENCLAW_INGEST_MODEL,
            reviewer_model_id=OPENCLAW_INGEST_CHECK_MODEL,
            fallback_model_id=OPENCLAW_INGEST_FALLBACK_MODEL,
            config_path=OPENCLAW_CONFIG_PATH,
        )
        metadata["keywords"] = generate_keywords_from_metadata(
            metadata,
            model_id=OPENCLAW_INGEST_MODEL,
            fallback_model_id=OPENCLAW_INGEST_FALLBACK_MODEL,
            config_path=OPENCLAW_CONFIG_PATH,
        )

        update_step("reconciling_citation", "正在匹配 citation 并更新数据库。")
        matched = match_existing_citation(
            title=(metadata.get("title") or "").strip(),
            doi=normalize_doi(metadata.get("doi") or ""),
            year=str(metadata.get("year") or "").strip(),
        )

        if matched and int(matched["id"]) != citation_id:
            merged = merge_citation_into_existing(citation_id, int(matched["id"]), metadata)
            if merged:
                target_citation_id = int(merged["id"])
        else:
            update_citation_metadata(citation_id, metadata)

        reading = ensure_reading_workspace_for_citation(target_citation_id)
        sync_reading_workspace_metadata(
            target_citation_id,
            reading,
            metadata,
            page_count=extracted.get("page_count"),
        )
    else:
        reading = ensure_reading_workspace_for_citation(target_citation_id)

    paper_payload = read_json_file(Path(reading["paper_json_path"]), {})
    if run_analysis:
        update_step("generating_analysis", f"正在生成论文结构化分析，并由检查模型复核。（model_http={model_http_transport_mode()}）")
        update_paper_analysis_progress(
            Path(reading["paper_json_path"]),
            state="in_progress",
            progress=50,
            stage="analyzing",
            message=f"正在调用 {OPENCLAW_INGEST_MODEL} 生成结构化分析。（model_http={model_http_transport_mode()}）",
        )
        analysis = generate_analysis_from_text(
            {
                "title": paper_payload.get("title") or metadata.get("title") or "",
                "authors": paper_payload.get("authors") or metadata.get("authors") or [],
                "venue": paper_payload.get("venue") or metadata.get("venue") or "",
                "year": paper_payload.get("year") or metadata.get("year") or "",
                "doi": paper_payload.get("doi") or metadata.get("doi") or "",
            },
            extracted.get("text") or "",
            model_id=OPENCLAW_INGEST_MODEL,
            reviewer_model_id=OPENCLAW_INGEST_CHECK_MODEL,
            fallback_model_id=OPENCLAW_INGEST_FALLBACK_MODEL,
            config_path=OPENCLAW_CONFIG_PATH,
        )
        update_step("finalizing", "正在写入阅读页面和结果文件。")
        persist_openclaw_analysis_outputs(reading, extracted, analysis)
    refreshed = get_citation_by_id(target_citation_id) or citation
    return {
        "citation_id": target_citation_id,
        "reading_url": reading["reading_url"],
        "paper_id": reading["paper_id"],
        "links": reading.get("links") or build_reading_links(reading["paper_id"]),
        "title": refreshed.get("title") or metadata.get("title") or item.get("title") or "",
        "metadata": metadata,
    }


def start_openclaw_refresh_job_for_paper(
    paper_id: str,
    *,
    run_metadata: bool = True,
    run_analysis: bool = True,
) -> dict:
    citation = get_citation_by_reading_paper_id(paper_id)
    if not citation:
        raise ValueError("未找到对应文献记录")
    citation_id = int(citation["id"])
    pdf_abs = citation_pdf_abspath(citation)
    if not pdf_abs:
        raise ValueError("当前文献缺少可访问的 PDF")
    reading = ensure_reading_workspace_for_citation(citation_id)
    actions = []
    if run_metadata:
        actions.append("metadata")
    if run_analysis:
        actions.append("analysis")
    if not actions:
        raise ValueError("未指定任何处理动作")
    item = {
        "filename": pdf_abs.name,
        "citation_id": citation_id,
        "paper_id": reading["paper_id"],
        "reading_url": reading["reading_url"],
        "links": reading.get("links") or build_reading_links(reading["paper_id"]),
        "pdf_path": citation.get("pdf_path") or "",
        "pdf_reused": True,
        "status": "queued",
        "current_step": "queued",
        "step_message": "等待进入导入队列。",
        "requested_actions": actions,
        "title": citation.get("title") or "",
        "error": "",
        "metadata": {},
    }
    paper_json_path = Path(reading["paper_json_path"])
    if run_metadata:
        update_paper_metadata_progress(
            paper_json_path,
            state="processing",
            message="已提交 OpenClaw 元数据识别任务，正在排队。",
        )
    if run_analysis:
        update_paper_analysis_progress(
            paper_json_path,
            state="in_progress",
            progress=5,
            stage="queued",
            message="已提交 OpenClaw 深度解析任务，正在排队。",
        )
    job = {
        "id": build_openclaw_job_id(),
        "username": current_username(),
        "kind": "openclaw_refresh_paper",
        "status": "queued",
        "running": True,
        "created_at": utc_now(),
        "updated_at": utc_now(),
        "finished_at": "",
        "model": OPENCLAW_INGEST_MODEL,
        "review_model": OPENCLAW_INGEST_CHECK_MODEL,
        "fallback_model": OPENCLAW_INGEST_FALLBACK_MODEL,
        "config_path": str(OPENCLAW_CONFIG_PATH),
        "message": "任务已提交，等待后台处理。",
        "current_index": None,
        "current_title": "",
        "links": {
            "reading_home": "/reading",
            "reading_home_absolute": build_public_url("/reading"),
        },
        "items": [item],
    }
    save_openclaw_job(job)
    thread = threading.Thread(target=run_openclaw_intake_job, args=(job["id"], job.get("username") or ""), daemon=True)
    thread.start()
    return load_openclaw_job(job["id"]) or job


def run_openclaw_intake_job(job_id: str, username: str = ""):
    with user_context(username):
        _run_openclaw_intake_job_inner(job_id)


def _run_openclaw_intake_job_inner(job_id: str):
    update_openclaw_job(job_id, lambda job: job.update({"status": "running", "running": True, "message": "OpenClaw 批量导入任务进行中。"}))
    job = load_openclaw_job(job_id) or {}
    any_failed = False
    for index, _ in enumerate(job.get("items") or []):
        def mark_running(payload):
            payload["current_index"] = index
            payload["current_title"] = (payload["items"][index].get("filename") or payload["items"][index].get("title") or "")
            payload["items"][index]["status"] = "running"
            payload["items"][index]["error"] = ""

        update_openclaw_job(job_id, mark_running)
        try:
            result = process_openclaw_intake_item(job_id, index)

            def mark_done(payload):
                item = payload["items"][index]
                item["status"] = "completed"
                item["citation_id"] = result["citation_id"]
                item["paper_id"] = result["paper_id"]
                item["reading_url"] = result["reading_url"]
                item["links"] = result.get("links") or {}
                item["title"] = result["title"]
                item["metadata"] = result["metadata"]
                item["current_step"] = "completed"
                item["step_message"] = "该 PDF 已处理完成。"

            update_openclaw_job(job_id, mark_done)
        except Exception as exc:
            any_failed = True

            def mark_failed(payload):
                item = payload["items"][index]
                item["status"] = "failed"
                item["error"] = str(exc)
                item["current_step"] = "failed"
                item["step_message"] = str(exc)

            update_openclaw_job(job_id, mark_failed)

    def finalize(payload):
        payload["running"] = False
        payload["status"] = "completed_with_errors" if any_failed else "completed"
        payload["message"] = "任务完成。" if not any_failed else "任务完成，但有部分 PDF 失败。"
        payload["finished_at"] = utc_now()
        payload["current_index"] = None
        payload["current_title"] = ""
        completed_items = [item for item in (payload.get("items") or []) if (item.get("status") or "") == "completed"]
        payload["links"] = {
            "reading_home": "/reading",
            "reading_home_absolute": build_public_url("/reading"),
            "paper_pages": [item.get("links", {}).get("paper_page") for item in completed_items if item.get("links", {}).get("paper_page")],
            "paper_pages_absolute": [item.get("links", {}).get("paper_page_absolute") for item in completed_items if item.get("links", {}).get("paper_page_absolute")],
        }

    update_openclaw_job(job_id, finalize)


def start_openclaw_intake_job(files: list[dict], group_id_raw: str | None = None) -> dict:
    items = []
    for file_item in files:
        pdf_record = store_uploaded_pdf(file_item, (getattr(file_item, "filename", "") or "").strip())
        pdf_path = pdf_record.get("pdf_path") or ""
        pdf_sha256 = pdf_record.get("pdf_sha256") or ""
        if not pdf_path:
            raise ValueError("缺少 PDF 文件")
        existing = find_existing_pdf_by_hash(pdf_sha256) if pdf_sha256 else None
        citation = get_citation_by_id(int(existing["id"])) if existing else None
        if not citation:
            citation_id = create_placeholder_citation_for_pdf(pdf_path, pdf_sha256)
            citation = get_citation_by_id(citation_id)
        if not citation:
            raise ValueError("无法创建文献记录")
        if group_id_raw not in (None, ""):
            try:
                add_citation_to_group(int(citation["id"]), int(group_id_raw))
            except Exception:
                pass
        reading = ensure_reading_workspace_for_citation(int(citation["id"]))
        items.append(
            {
                "filename": (getattr(file_item, "filename", "") or "").strip() or Path(pdf_path).name,
                "citation_id": int(citation["id"]),
                "paper_id": reading["paper_id"],
                "reading_url": reading["reading_url"],
                "links": reading.get("links") or build_reading_links(reading["paper_id"]),
                "pdf_path": pdf_path,
                "pdf_reused": bool(pdf_record.get("reused")),
                "status": "queued",
                "current_step": "queued",
                "step_message": "等待进入导入队列。",
                "title": citation.get("title") or "",
                "error": "",
                "metadata": {},
            }
        )

    job = {
        "id": build_openclaw_job_id(),
        "username": current_username(),
        "kind": "openclaw_batch_pdf_intake",
        "status": "queued",
        "running": True,
        "created_at": utc_now(),
        "updated_at": utc_now(),
        "finished_at": "",
        "model": OPENCLAW_INGEST_MODEL,
        "review_model": OPENCLAW_INGEST_CHECK_MODEL,
        "fallback_model": OPENCLAW_INGEST_FALLBACK_MODEL,
        "config_path": str(OPENCLAW_CONFIG_PATH),
        "message": "任务已提交，等待后台处理。",
        "current_index": None,
        "current_title": "",
        "links": {
            "reading_home": "/reading",
            "reading_home_absolute": build_public_url("/reading"),
        },
        "items": items,
    }
    save_openclaw_job(job)
    thread = threading.Thread(target=run_openclaw_intake_job, args=(job["id"], job.get("username") or ""), daemon=True)
    thread.start()
    return load_openclaw_job(job["id"]) or job


def process_openclaw_picsearch_item(job_id: str, index: int):
    job = load_openclaw_job(job_id)
    if not job:
        raise ValueError("OpenClaw 图片任务不存在")
    item = (job.get("items") or [])[index]
    image_rel = (item.get("image_path") or "").strip()
    if not image_rel:
        raise ValueError("任务缺少图片路径")
    image_abs = current_data_dir() / image_rel
    if not image_abs.exists():
        raise ValueError(f"图片文件不存在: {image_rel}")

    def update_step(step: str, message: str):
        def mutate(payload):
            item_payload = payload["items"][index]
            item_payload["current_step"] = step
            item_payload["step_message"] = message
            payload["message"] = message
            payload["current_title"] = item_payload.get("filename") or item_payload.get("title") or ""
        update_openclaw_job(job_id, mutate)

    def enrich_from_timeline(records: list[dict], paper: dict) -> dict:
        if not paper:
            return {}
        target_doi = str(paper.get("doi") or "").strip().lower()
        target_title = " ".join(str(paper.get("title") or "").strip().lower().split())
        for item in records or []:
            item_doi = str(item.get("doi") or "").strip().lower()
            item_title = " ".join(str(item.get("title") or "").strip().lower().split())
            if target_doi and item_doi == target_doi:
                return dict(item)
            if target_title and item_title == target_title:
                return dict(item)
        return dict(paper)

    update_step("recognizing", "正在识别图片中的论文信息。")
    batch_result = resolve_paper_lookups_from_image(image_abs)
    update_step("updating_timeline", "正在写入今日 Picsearch timeline。")
    papers = []
    latest_timeline = {}
    sources = []
    result_records = []
    for result in batch_result.get("results") or []:
        record = result.get("record") or {}
        if record:
            result_records.append(record)
            papers.append(record)
        if result.get("source"):
            sources.append(result.get("source") or "")
    if result_records:
        latest_timeline = append_picsearch_timeline(result_records)
        enriched_records = latest_timeline.get("raw_records") or []
        if enriched_records:
            papers = [enrich_from_timeline(enriched_records, paper) for paper in papers]
    try:
        image_abs.unlink(missing_ok=True)
    except Exception:
        pass
    first_paper = papers[0] if papers else {}
    return {
        "paper": first_paper,
        "papers": papers,
        "candidate": batch_result.get("candidate") or {},
        "source": sources[0] if sources else "",
        "sources": sources,
        "timeline": latest_timeline,
        "mode": batch_result.get("mode") or "single_paper",
    }


def run_openclaw_picsearch_job(job_id: str, username: str = ""):
    with user_context(username):
        _run_openclaw_picsearch_job_inner(job_id)


def _run_openclaw_picsearch_job_inner(job_id: str):
    update_openclaw_job(job_id, lambda job: job.update({"status": "running", "running": True, "message": "OpenClaw 图片找论文任务进行中。"}))
    job = load_openclaw_job(job_id) or {}
    any_failed = False
    latest_timeline = {}
    scholar_list_hits = 0
    scholar_list_papers = 0
    for index, _ in enumerate(job.get("items") or []):
        def mark_running(payload):
            payload["current_index"] = index
            payload["current_title"] = (payload["items"][index].get("filename") or payload["items"][index].get("title") or "")
            payload["items"][index]["status"] = "running"
            payload["items"][index]["error"] = ""

        update_openclaw_job(job_id, mark_running)
        try:
            result = process_openclaw_picsearch_item(job_id, index)
            latest_timeline = result.get("timeline") or latest_timeline
            if (result.get("mode") or "") == "scholar_list":
                scholar_list_hits += 1
                scholar_list_papers += len(result.get("papers") or [])

            def mark_done(payload):
                item = payload["items"][index]
                paper = result.get("paper") or {}
                item["status"] = "completed"
                item["title"] = paper.get("title") or item.get("title") or ""
                item["paper"] = paper
                item["papers"] = result.get("papers") or ([paper] if paper else [])
                item["candidate"] = result.get("candidate") or {}
                item["source"] = result.get("source") or ""
                item["sources"] = result.get("sources") or []
                item["mode"] = result.get("mode") or "single_paper"
                item["timeline"] = result.get("timeline") or {}
                item["current_step"] = "completed"
                if (result.get("mode") or "") == "scholar_list":
                    item["step_message"] = f"该图片已处理完成，识别出 {len(result.get('papers') or [])} 篇论文。"
                else:
                    item["step_message"] = "该图片已处理完成。"

            update_openclaw_job(job_id, mark_done)
        except Exception as exc:
            any_failed = True

            def mark_failed(payload):
                item = payload["items"][index]
                item["status"] = "failed"
                item["error"] = str(exc)
                item["current_step"] = "failed"
                item["step_message"] = str(exc)

            update_openclaw_job(job_id, mark_failed)

    def finalize(payload):
        payload["running"] = False
        payload["status"] = "completed_with_errors" if any_failed else "completed"
        if scholar_list_hits:
            base_message = f"图片找论文任务完成；检测到 {scholar_list_hits} 张 Scholar 页面截图，共补链 {scholar_list_papers} 篇论文。"
        else:
            base_message = "图片找论文任务完成。"
        if any_failed:
            base_message += " 但有部分图片失败。"
        payload["message"] = base_message
        payload["finished_at"] = utc_now()
        payload["current_index"] = None
        payload["current_title"] = ""
        if latest_timeline:
            payload["links"] = {
                "timeline": latest_timeline.get("relative_site_url") or "",
                "timeline_absolute": latest_timeline.get("site_url") or "",
            }

    update_openclaw_job(job_id, finalize)


def start_openclaw_picsearch_job(files: list[dict]) -> dict:
    allowed_suffixes = {".png", ".jpg", ".jpeg", ".webp", ".bmp"}
    temp_dir = current_data_dir() / "tmp_picsearch"
    temp_dir.mkdir(parents=True, exist_ok=True)
    items = []
    for file_item in files:
        filename = (getattr(file_item, "filename", "") or "").strip()
        suffix = Path(filename).suffix.lower()
        if suffix not in allowed_suffixes:
            raise ValueError(f"仅支持 PNG/JPG/JPEG/WEBP/BMP 图片: {filename or 'unnamed'}")
        stored_name = f"{datetime.utcnow().strftime('%Y%m%d-%H%M%S')}-{secrets.token_hex(4)}{suffix or '.png'}"
        rel_path = Path("tmp_picsearch") / stored_name
        abs_path = current_data_dir() / rel_path
        file_item.file.seek(0)
        with abs_path.open("wb") as fh:
            shutil.copyfileobj(file_item.file, fh)
        items.append(
            {
                "filename": filename or stored_name,
                "image_path": rel_path.as_posix(),
                "status": "queued",
                "current_step": "queued",
                "step_message": "等待进入图片识别队列。",
                "title": "",
                "source": "",
                "paper": {},
                "candidate": {},
                "timeline": {},
                "error": "",
            }
        )
    if not items:
        raise ValueError("请至少上传一张图片文件")

    job = {
        "id": build_openclaw_job_id(),
        "username": current_username(),
        "kind": "openclaw_batch_picsearch",
        "status": "queued",
        "running": True,
        "created_at": utc_now(),
        "updated_at": utc_now(),
        "finished_at": "",
        "model": DEFAULT_OPENCLAW_IMAGE_MODEL,
        "fallback_model": DEFAULT_OPENCLAW_IMAGE_FALLBACK_MODEL,
        "config_path": str(OPENCLAW_CONFIG_PATH),
        "message": "图片找论文任务已提交，等待后台处理。",
        "current_index": None,
        "current_title": "",
        "links": {},
        "items": items,
    }
    save_openclaw_job(job)
    thread = threading.Thread(target=run_openclaw_picsearch_job, args=(job["id"], job.get("username") or ""), daemon=True)
    thread.start()
    return load_openclaw_job(job["id"]) or job


def process_openclaw_textsearch_item(job_id: str, index: int):
    job = load_openclaw_job(job_id)
    if not job:
        raise ValueError("OpenClaw 文本找论文任务不存在")
    item = (job.get("items") or [])[index]
    title = (item.get("title_query") or "").strip()
    if not title:
        raise ValueError("任务缺少论文标题")

    def update_step(step: str, message: str):
        def mutate(payload):
            item_payload = payload["items"][index]
            item_payload["current_step"] = step
            item_payload["step_message"] = message
            payload["message"] = message
            payload["current_title"] = item_payload.get("title_query") or item_payload.get("title") or ""
        update_openclaw_job(job_id, mutate)

    def enrich_from_timeline(records: list[dict], paper: dict) -> dict:
        if not paper:
            return {}
        target_doi = str(paper.get("doi") or "").strip().lower()
        target_title = " ".join(str(paper.get("title") or "").strip().lower().split())
        for item in records or []:
            item_doi = str(item.get("doi") or "").strip().lower()
            item_title = " ".join(str(item.get("title") or "").strip().lower().split())
            if target_doi and item_doi == target_doi:
                return dict(item)
            if target_title and item_title == target_title:
                return dict(item)
        return dict(paper)

    update_step("resolving", "正在根据标题匹配论文链接。")
    result = resolve_paper_lookup_from_title(title)
    update_step("updating_timeline", "正在写入今日 Textsearch timeline。")
    timeline = append_textsearch_timeline(result["record"])
    enriched = enrich_from_timeline(timeline.get("raw_records") or [], result.get("record") or {})
    return {
        "paper": enriched,
        "candidate": result.get("candidate") or {},
        "source": result.get("source") or "",
        "failure_reason": result.get("failure_reason") or "",
        "timeline": timeline,
    }


def process_openclaw_titlesearch_item(job_id: str, index: int):
    return process_openclaw_textsearch_item(job_id, index)


def run_openclaw_textsearch_job(job_id: str, username: str = ""):
    with user_context(username):
        _run_openclaw_textsearch_job_inner(job_id)


def run_openclaw_titlesearch_job(job_id: str, username: str = ""):
    return run_openclaw_textsearch_job(job_id, username)


def _run_openclaw_textsearch_job_inner(job_id: str):
    update_openclaw_job(job_id, lambda job: job.update({"status": "running", "running": True, "message": "OpenClaw 文本找论文任务进行中。"}))
    job = load_openclaw_job(job_id) or {}
    any_failed = False
    latest_timeline = {}
    for index, _ in enumerate(job.get("items") or []):
        def mark_running(payload):
            payload["current_index"] = index
            payload["current_title"] = (payload["items"][index].get("title_query") or payload["items"][index].get("title") or "")
            payload["items"][index]["status"] = "running"
            payload["items"][index]["error"] = ""

        update_openclaw_job(job_id, mark_running)
        try:
            result = process_openclaw_textsearch_item(job_id, index)
            latest_timeline = result.get("timeline") or latest_timeline

            def mark_done(payload):
                item = payload["items"][index]
                paper = result.get("paper") or {}
                item["status"] = "completed"
                item["title"] = paper.get("title") or item.get("title_query") or ""
                item["paper"] = paper
                item["candidate"] = result.get("candidate") or {}
                item["source"] = result.get("source") or ""
                item["failure_reason"] = result.get("failure_reason") or ""
                item["timeline"] = result.get("timeline") or {}
                item["current_step"] = "completed"
                item["step_message"] = "该标题已处理完成。"

            update_openclaw_job(job_id, mark_done)
        except Exception as exc:
            any_failed = True

            def mark_failed(payload):
                item = payload["items"][index]
                item["status"] = "failed"
                item["error"] = str(exc)
                item["failure_reason"] = (
                    str(exc).split("failure_reason=", 1)[1].strip()
                    if "failure_reason=" in str(exc)
                    else ""
                )
                item["current_step"] = "failed"
                item["step_message"] = str(exc)

            update_openclaw_job(job_id, mark_failed)

    def finalize(payload):
        payload["running"] = False
        payload["status"] = "completed_with_errors" if any_failed else "completed"
        payload["message"] = "文本找论文任务完成。" if not any_failed else "文本找论文任务完成，但有部分条目失败。"
        payload["finished_at"] = utc_now()
        payload["current_index"] = None
        payload["current_title"] = ""
        if latest_timeline:
            payload["links"] = {
                "timeline": latest_timeline.get("relative_site_url") or "",
                "timeline_absolute": latest_timeline.get("site_url") or "",
            }

    update_openclaw_job(job_id, finalize)


def _build_textsearch_job(title_items: list[str], *, kind: str, message_prefix: str) -> dict:
    items = []
    for raw in title_items:
        title = " ".join(str(raw or "").split())
        if not title:
            continue
        items.append(
            {
                "title_query": title,
                "status": "queued",
                "current_step": "queued",
                "step_message": "等待进入标题匹配队列。",
                "title": "",
                "source": "",
                "failure_reason": "",
                "paper": {},
                "candidate": {},
                "timeline": {},
                "error": "",
            }
        )
    if not items:
        raise ValueError("请至少提供一个论文标题")

    job = {
        "id": build_openclaw_job_id(),
        "username": current_username(),
        "kind": kind,
        "status": "queued",
        "running": True,
        "created_at": utc_now(),
        "updated_at": utc_now(),
        "finished_at": "",
        "message": message_prefix,
        "current_index": None,
        "current_title": "",
        "links": {},
        "items": items,
    }
    save_openclaw_job(job)
    thread = threading.Thread(target=run_openclaw_textsearch_job, args=(job["id"], job.get("username") or ""), daemon=True)
    thread.start()
    return load_openclaw_job(job["id"]) or job


def start_openclaw_textsearch_job(texts: list[str]) -> dict:
    return _build_textsearch_job(
        texts,
        kind="openclaw_batch_textsearch",
        message_prefix="文本找论文任务已提交，等待后台处理。",
    )


def _run_openclaw_titlesearch_job_inner(job_id: str):
    return _run_openclaw_textsearch_job_inner(job_id)


def start_openclaw_titlesearch_job(titles: list[str]) -> dict:
    return _build_textsearch_job(
        titles,
        kind="openclaw_batch_titlesearch",
        message_prefix="标题找论文任务已提交，等待后台处理。",
    )


# Reading batch maintenance
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


def reading_paper_needs_metadata(paper_id: str) -> bool:
    bundle = load_reading_bundle(paper_id)
    if not bundle:
        return False
    paper = bundle.get("paper") or {}
    status = paper.get("status") or {}
    if (status.get("metadata") or "").strip() != "completed":
        return True
    title = " ".join(str(paper.get("title") or "").split())
    authors = paper.get("authors") or []
    year = str(paper.get("year") or "").strip()
    venue = " ".join(str(paper.get("venue") or "").split())
    # DOI 允许为空，但标题/作者/年份/venue 长期缺失通常说明元数据还不完整。
    if not title or not authors or not year or not venue:
        return True
    return False


def analysis_has_content(analysis_payload: dict | None) -> bool:
    modules = (analysis_payload or {}).get("modules") or {}
    return any(bool(((modules.get(name) or {}).get("data") or {})) for name in ("overview", "problem", "method", "results", "critique"))


def reading_paper_needs_analysis(paper_id: str) -> bool:
    bundle = load_reading_bundle(paper_id)
    if not bundle:
        return False
    paper = bundle.get("paper") or {}
    status = paper.get("status") or {}
    if (status.get("analysis") or "").strip() == "completed" and analysis_has_content(bundle.get("analysis")):
        return False
    return not analysis_has_content(bundle.get("analysis"))


def get_batch_reading_job_payload() -> dict:
    with BATCH_READING_JOB_LOCK:
        return dict(BATCH_READING_JOB)


def update_batch_reading_job(**updates):
    with BATCH_READING_JOB_LOCK:
        BATCH_READING_JOB.update(updates)


def reset_batch_reading_job():
    update_batch_reading_job(
        status="idle",
        running=False,
        stage="",
        message="",
        started_at="",
        ended_at="",
        current_paper_id="",
        current_title="",
        total=0,
        processed=0,
        metadata_total=0,
        metadata_completed=0,
        analysis_total=0,
        analysis_completed=0,
        error="",
    )


def run_batch_generation_job(username: str = ""):
    with user_context(username):
        _run_batch_generation_job_inner()


def _run_batch_generation_job_inner():
    try:
        citations = list_citations()
        work_items = []
        metadata_total = 0
        analysis_total = 0
        for citation in citations:
            if not citation_has_pdf(citation):
                continue
            try:
                reading = ensure_reading_workspace_for_citation(int(citation["id"]))
            except Exception:
                continue
            paper_id = reading["paper_id"]
            needs_metadata = reading_paper_needs_metadata(paper_id)
            needs_analysis = reading_paper_needs_analysis(paper_id)
            if not needs_metadata and not needs_analysis:
                continue
            work_items.append(
                {
                    "citation_id": int(citation["id"]),
                    "paper_id": paper_id,
                    "title": citation.get("title") or paper_id,
                    "needs_metadata": needs_metadata,
                    "needs_analysis": needs_analysis,
                }
            )
            metadata_total += 1 if needs_metadata else 0
            analysis_total += 1 if needs_analysis else 0

        update_batch_reading_job(
            status="running",
            running=True,
            stage="queued",
            message="批处理任务已启动，准备逐篇处理。",
            total=len(work_items),
            processed=0,
            metadata_total=metadata_total,
            metadata_completed=0,
            analysis_total=analysis_total,
            analysis_completed=0,
            error="",
        )

        processed = 0
        metadata_completed = 0
        analysis_completed = 0
        for item in work_items:
            paper_id = item["paper_id"]
            citation = get_citation_by_reading_paper_id(paper_id) or get_citation_by_id(item["citation_id"])
            if not citation:
                processed += 1
                update_batch_reading_job(processed=processed)
                continue

            current_title = citation.get("title") or item["title"]
            pdf_rel = (citation.get("pdf_path") or "").strip()
            if item["needs_metadata"]:
                update_batch_reading_job(
                    stage="metadata",
                    current_paper_id=paper_id,
                    current_title=current_title,
                    message=f"正在识别元数据：{current_title}",
                )
                if pdf_rel:
                    job = start_openclaw_refresh_job_for_paper(paper_id, run_metadata=True, run_analysis=False)
                    wait_for_openclaw_job(job["id"], timeout_seconds=900)
                    metadata_completed += 1
                    update_batch_reading_job(metadata_completed=metadata_completed)

            if item["needs_analysis"]:
                update_batch_reading_job(
                    stage="analysis",
                    current_paper_id=paper_id,
                    current_title=current_title,
                    message=f"正在生成分析：{current_title}",
                )
                job = start_openclaw_refresh_job_for_paper(paper_id, run_metadata=False, run_analysis=True)
                wait_for_openclaw_job(job["id"], timeout_seconds=1800)
                analysis_completed += 1
                update_batch_reading_job(analysis_completed=analysis_completed)

            processed += 1
            update_batch_reading_job(processed=processed)

        update_batch_reading_job(
            status="completed",
            running=False,
            stage="completed",
            message="批处理已完成。",
            ended_at=utc_now(),
            current_paper_id="",
            current_title="",
        )
    except Exception as exc:
        update_batch_reading_job(
            status="failed",
            running=False,
            stage="failed",
            message=f"批处理失败：{exc}",
            ended_at=utc_now(),
            error=str(exc),
        )


def start_batch_generation_job() -> dict:
    payload = get_batch_reading_job_payload()
    if payload.get("running"):
        return {"started": False, "status": payload}
    reset_batch_reading_job()
    update_batch_reading_job(
        status="running",
        running=True,
        username=current_username(),
        stage="starting",
        message="正在收集待处理文献。",
        started_at=utc_now(),
        ended_at="",
    )
    thread = threading.Thread(target=run_batch_generation_job, args=(current_username(),), daemon=True)
    thread.start()
    return {"started": True, "status": get_batch_reading_job_payload()}


def wait_for_openclaw_job(job_id: str, timeout_seconds: float = 1800.0, poll_interval: float = 2.0) -> dict:
    deadline = time.time() + timeout_seconds
    while time.time() < deadline:
        job = load_openclaw_job(job_id)
        if job and not job.get("running"):
            if (job.get("status") or "") in ("completed", "completed_with_errors"):
                first_failed = next((item for item in (job.get("items") or []) if (item.get("status") or "") == "failed"), None)
                if first_failed:
                    raise ValueError(first_failed.get("error") or "OpenClaw 任务失败")
            return job
        time.sleep(poll_interval)
    raise TimeoutError(f"等待 OpenClaw 任务超时: {job_id}")


def start_metadata_job_for_paper(paper_id: str) -> dict:
    bundle = load_reading_bundle(paper_id)
    if not bundle:
        raise ValueError("阅读工作区不存在")
    paper_json_path = bundle["workspace"] / "paper.json"
    current_status = ((read_json_file(paper_json_path, {}).get("status") or {}).get("metadata") or "").strip()
    if current_status == "processing":
        return {"started": False, "status": get_reading_status_payload(paper_id), "job": None}
    update_paper_metadata_progress(
        paper_json_path,
        state="processing",
        message="已提交 OpenClaw 元数据识别任务，等待后台开始处理。",
    )
    job = start_openclaw_refresh_job_for_paper(paper_id, run_metadata=True, run_analysis=False)
    return {"started": True, "status": get_reading_status_payload(paper_id), "job": job}


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
                "model": OPENCLAW_INGEST_MODEL,
                "input_scope": ["full_pdf"],
                "output_version": 1,
            }
        ],
        "updated_at": now,
    }


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


def start_analysis_job(paper_id: str) -> dict:
    payload = get_reading_status_payload(paper_id)
    if payload["analysis"] == "in_progress":
        return {"started": False, "status": payload, "job": None}
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
        message="已提交 OpenClaw 分析任务，等待后台开始处理。",
    )
    job = start_openclaw_refresh_job_for_paper(paper_id, run_metadata=False, run_analysis=True)
    return {"started": True, "status": get_reading_status_payload(paper_id), "job": job}


# External reference enrichment and related-search generation
