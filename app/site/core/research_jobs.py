"""Natural-language research planning and background search jobs for exScholar."""

from .shared import *

RESEARCH_STALE_JOB_TIMEOUT_SECONDS = 10 * 60
RESEARCH_STALE_COMPLETION_MESSAGE = "搜索输出已生成，但后台复核阶段因服务重启或超时中断；结果已保留。"
RESEARCH_STALE_FAILURE_MESSAGE = "Research 任务因服务重启或超时被中断，请重新发起。"


def ensure_research_jobs_dir():
    RESEARCH_JOBS_DIR.mkdir(parents=True, exist_ok=True)


def build_research_job_id() -> str:
    return f"rjob_{datetime.utcnow().strftime('%Y%m%d_%H%M%S')}_{secrets.token_hex(3)}"


def research_job_path(job_id: str) -> Path:
    ensure_research_jobs_dir()
    return RESEARCH_JOBS_DIR / f"{job_id}.json"


def parse_research_job_timestamp(value: str) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        return datetime.strptime(text, "%Y-%m-%dT%H:%M:%SZ")
    except ValueError:
        return None


def _existing_research_output_urls(job: dict) -> dict:
    csv_path = Path(str(job.get("csv_path") or "").strip()) if job.get("csv_path") else None
    json_path = Path(str(job.get("json_path") or "").strip()) if job.get("json_path") else None
    meta_path = Path(str(job.get("meta_path") or "").strip()) if job.get("meta_path") else None
    site_path = Path(str(job.get("site_path") or "").strip()) if job.get("site_path") else None
    result = {
        "csv_url": _research_relative_url(csv_path) if csv_path and csv_path.exists() else "",
        "json_url": _research_relative_url(json_path) if json_path and json_path.exists() else "",
        "search_url": _research_relative_url(meta_path) if meta_path and meta_path.exists() else "",
        "site_relative_url": "",
    }
    if site_path and site_path.exists():
        try:
            relative_dir = site_path.parent.parent.relative_to(DATA_DIR).as_posix()
            result["site_relative_url"] = f"/{relative_dir}/site/"
        except Exception:
            result["site_relative_url"] = ""
    return result


def _infer_research_review_summary(job: dict) -> dict:
    summary = {"high": 0, "medium": 0, "low": 0}
    json_path = Path(str(job.get("json_path") or "").strip()) if job.get("json_path") else None
    meta_path = Path(str(job.get("meta_path") or "").strip()) if job.get("meta_path") else None
    if meta_path and meta_path.exists():
        meta_payload = read_json_file(meta_path, {})
        existing = meta_payload.get("review_summary")
        if isinstance(existing, dict):
            return {
                "high": int(existing.get("high") or 0),
                "medium": int(existing.get("medium") or 0),
                "low": int(existing.get("low") or 0),
            }
    if not json_path or not json_path.exists():
        return summary
    payload = read_json_file(json_path, {})
    for paper in payload.get("papers") or []:
        label = str(paper.get("relevance_label") or "").strip().lower()
        if label in summary:
            summary[label] += 1
    return summary


def reconcile_research_job_if_stale(job: dict, path: Path | None = None) -> dict:
    status = str(job.get("status") or "").strip().lower()
    if status in {"completed", "failed"}:
        return job
    updated_at = parse_research_job_timestamp(job.get("updated_at") or job.get("created_at") or "")
    if not updated_at:
        return job
    age_seconds = (datetime.utcnow() - updated_at).total_seconds()
    if age_seconds < RESEARCH_STALE_JOB_TIMEOUT_SECONDS:
        return job

    csv_path = Path(str(job.get("csv_path") or "").strip()) if job.get("csv_path") else None
    json_path = Path(str(job.get("json_path") or "").strip()) if job.get("json_path") else None
    meta_path = Path(str(job.get("meta_path") or "").strip()) if job.get("meta_path") else None
    site_path = Path(str(job.get("site_path") or "").strip()) if job.get("site_path") else None
    has_outputs = all(path_obj and path_obj.exists() for path_obj in (csv_path, json_path, meta_path, site_path))

    if has_outputs:
        meta_payload = read_json_file(meta_path, {}) if meta_path else {}
        output_urls = _existing_research_output_urls(job)
        review_summary = _infer_research_review_summary(job)
        job["status"] = "completed"
        job["current_step"] = "completed"
        job["step_message"] = RESEARCH_STALE_COMPLETION_MESSAGE
        job["warning"] = RESEARCH_STALE_COMPLETION_MESSAGE
        job["total_papers"] = meta_payload.get("total_papers") if isinstance(meta_payload, dict) else job.get("total_papers")
        job["review_summary"] = review_summary
        job.update(output_urls)
    else:
        job["status"] = "failed"
        job["current_step"] = "failed"
        job["step_message"] = RESEARCH_STALE_FAILURE_MESSAGE
        job["error"] = RESEARCH_STALE_FAILURE_MESSAGE

    if path is not None:
        with RESEARCH_JOB_LOCK:
            write_json_file_atomic(path, summarize_research_job(job))
    return job


def summarize_research_job(job: dict) -> dict:
    job["updated_at"] = utc_now()
    return job


def save_research_job(job: dict):
    with RESEARCH_JOB_LOCK:
        write_json_file_atomic(research_job_path(job["id"]), summarize_research_job(job))


def load_research_job(job_id: str) -> dict | None:
    path = research_job_path(job_id)
    if not path.exists():
        return None
    job = read_json_file(path, None)
    if not job:
        return None
    return reconcile_research_job_if_stale(job, path)


def list_research_jobs(limit: int = 20) -> list[dict]:
    ensure_research_jobs_dir()
    jobs = []
    for path in sorted(RESEARCH_JOBS_DIR.glob("*.json"), reverse=True):
        payload = read_json_file(path, None)
        if not payload:
            continue
        jobs.append(reconcile_research_job_if_stale(payload, path))
        if len(jobs) >= limit:
            break
    return jobs


def delete_research_job(job_id: str) -> bool:
    path = research_job_path(job_id)
    if not path.exists():
        return False
    path.unlink()
    return True


def update_research_job(job_id: str, mutate):
    with RESEARCH_JOB_LOCK:
        path = research_job_path(job_id)
        job = read_json_file(path, None)
        if not job:
            raise ValueError("research 任务不存在")
        mutate(job)
        write_json_file_atomic(path, summarize_research_job(job))
        return job


def _research_relative_url(abs_path: str | Path) -> str:
    path = Path(abs_path)
    try:
        relative = path.relative_to(DATA_DIR).as_posix()
    except Exception:
        return ""
    return f"/{relative}"


def _parse_search_cli_line(job_id: str, line: str):
    text = " ".join(str(line or "").split())
    if not text:
        return

    def mutate(payload):
        payload["status"] = "running"
        payload["stdout_tail"] = text
        logs = payload.setdefault("logs", [])
        logs.append({"at": utc_now(), "step": payload.get("current_step") or "running", "message": text})
        payload["logs"] = logs[-60:]
        progress = payload.setdefault("progress", {})

        if "开始搜索" in text:
            payload["current_step"] = "searching"
            payload["step_message"] = text
        elif text.startswith("[search] '") and "venue=" in text:
            payload["current_step"] = "searching"
            matched_scope = re.search(r"^\[search\]\s*'(.+?)'\s+venue=(.+?)\s*\.\.\.$", text)
            if matched_scope:
                progress["current_keyword"] = matched_scope.group(1).strip()
                venue_label = matched_scope.group(2).strip()
                progress["current_venue"] = "" if venue_label == "全局" else venue_label
            payload["step_message"] = text
        elif text.startswith("[search] 命中") or "新增" in text:
            payload["current_step"] = "searching"
            matched = re.search(r"命中\s*(\d+)\s*篇.*新增\s*(\d+)\s*篇", text)
            if matched:
                hit_count = int(matched.group(1) or 0)
                added_count = int(matched.group(2) or 0)
                progress["current_hit_count"] = hit_count
                progress["current_added_count"] = added_count
                progress["discovered_papers"] = int(progress.get("discovered_papers") or 0) + added_count
                payload["step_message"] = f"{text}（当前累计 {progress['discovered_papers']} 篇）"
            else:
                payload["step_message"] = text
        elif "合并去重后共" in text:
            payload["current_step"] = "search_completed"
            payload["step_message"] = text
            matched_total = re.search(r"合并去重后共\s*(\d+)\s*篇", text)
            if matched_total:
                progress["discovered_papers"] = int(matched_total.group(1) or 0)
        elif "开始爬取摘要" in text:
            payload["current_step"] = "fetching_abstracts"
            payload["step_message"] = text
        elif "摘要爬取完成" in text or "摘要获取完成" in text:
            payload["current_step"] = "abstracts_completed"
            payload["step_message"] = text
        elif "已跳过摘要爬取" in text:
            payload["current_step"] = "abstracts_skipped"
            payload["step_message"] = text
        elif "输出目录:" in text:
            payload["current_step"] = "writing_outputs"
            payload["step_message"] = "搜索已完成，正在整理输出文件。"
            payload["result_dir"] = text.split("输出目录:", 1)[1].strip()
        elif "site_url" in text:
            payload["site_url"] = text.split(":", 1)[1].strip()
        elif "papers.csv" in text:
            payload["csv_path"] = text.split(":", 1)[1].strip()
        elif "papers.json" in text:
            payload["json_path"] = text.split(":", 1)[1].strip()
        elif "search.json" in text:
            payload["meta_path"] = text.split(":", 1)[1].strip()
        elif "site.html" in text:
            payload["site_path"] = text.split(":", 1)[1].strip()

    update_research_job(job_id, mutate)


def _run_search_in_openclaw_analytics(job_id: str, plan: dict) -> dict:
    keywords = [str(item).strip() for item in (plan.get("keywords") or []) if str(item).strip()]
    venues = [str(item).strip() for item in (plan.get("venues") or []) if str(item).strip()]
    slug = " ".join(str(plan.get("slug") or "").split())
    if not keywords or not slug:
        raise ValueError("research 方案缺少可执行的 keywords 或 slug")

    command = [
        OPENCLAW_ANALYTICS_PYTHON,
        "-m",
        "app.pipeline.search",
        "--keywords",
        ";".join(keywords),
        "--slug",
        slug,
        "--summary-name",
        " ".join(str(plan.get("summary") or "").split()),
        "--top",
        str(int(plan.get("top") or 100)),
    ]
    if venues:
        command.extend(["--venues", ",".join(venues)])
    year_from = int(plan.get("year_from") or 0)
    if year_from > 0:
        command.extend(["--year-from", str(year_from)])
    if not bool(plan.get("fetch_abstract", True)):
        command.append("--no-abstract")

    update_research_job(
        job_id,
        lambda payload: payload.update(
            {
                "status": "running",
                "current_step": "queued",
                "step_message": f"正在切换到 openclaw-analytics 环境执行搜索：{OPENCLAW_ANALYTICS_PYTHON}",
                "command": command,
            }
        ),
    )

    process = subprocess.Popen(
        command,
        cwd=str(ROOT_DIR),
        env={
            **os.environ,
            "EXSCHOLAR_DATA_DIR": str(DATA_DIR),
            "EXSCHOLAR_SEARCHES_DIR": str(SEARCHES_DIR),
        },
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
    )
    assert process.stdout is not None
    for raw_line in process.stdout:
        _parse_search_cli_line(job_id, raw_line)
    return_code = process.wait()
    if return_code != 0:
        raise RuntimeError(f"搜索子进程退出失败，exit code={return_code}")

    job = load_research_job(job_id) or {}
    result_dir = job.get("result_dir") or ""
    csv_path = job.get("csv_path") or ""
    json_path = job.get("json_path") or ""
    meta_path = job.get("meta_path") or ""
    site_path = job.get("site_path") or ""
    site_url = job.get("site_url") or ""
    if not result_dir:
        raise RuntimeError("搜索执行完成，但未解析到输出目录。")
    required_outputs = {
        "papers.csv": csv_path,
        "papers.json": json_path,
        "search.json": meta_path,
    }
    missing_outputs = [name for name, path in required_outputs.items() if not path or not Path(path).exists()]
    if missing_outputs:
        raise RuntimeError(f"搜索执行完成，但缺少输出文件: {', '.join(missing_outputs)}")
    if not site_path or not Path(site_path).exists():
        fallback_site_path = Path(result_dir) / "site" / "index.html"
        if fallback_site_path.exists():
            site_path = str(fallback_site_path)
        else:
            raise RuntimeError("搜索执行完成，但缺少 site/index.html。")

    csv_url = _research_relative_url(csv_path)
    json_url = _research_relative_url(json_path)
    search_url = _research_relative_url(meta_path)
    site_relative_url = ""
    try:
        relative_dir = Path(result_dir).relative_to(DATA_DIR).as_posix()
        site_relative_url = f"/{relative_dir}/site/"
    except Exception:
        site_relative_url = ""

    total_papers = None
    hit_counts = {}
    try:
        meta_payload = read_json_file(meta_path, {})
        total_papers = meta_payload.get("total_papers")
    except Exception:
        total_papers = None

    return {
        "out_dir": result_dir,
        "csv_path": csv_path,
        "json_path": json_path,
        "meta_path": meta_path,
        "site_path": site_path,
        "site_url": site_url,
        "site_relative_url": site_relative_url,
        "csv_url": csv_url,
        "json_url": json_url,
        "search_url": search_url,
        "total_papers": total_papers,
        "hit_counts": hit_counts,
    }


def _expand_research_plan_for_low_results(plan: dict, result: dict) -> dict | None:
    if not isinstance(plan, dict):
        return None
    total_papers = int(result.get("total_papers") or 0)
    if total_papers >= 80:
        return None

    existing_keywords = []
    seen = set()
    for item in plan.get("keywords") or []:
        value = " ".join(str(item or "").strip().split())
        lowered = value.lower()
        if not value or lowered in seen:
            continue
        seen.add(lowered)
        existing_keywords.append(value)

    suggestion = plan.get("query_suggestion") if isinstance(plan.get("query_suggestion"), dict) else {}
    candidate_pool = list(suggestion.get("candidate_keywords") or []) + list(suggestion.get("core_concepts") or [])
    extra_keywords = []
    for item in candidate_pool:
        value = " ".join(str(item or "").strip().split())
        lowered = value.lower()
        if not value or lowered in seen:
            continue
        seen.add(lowered)
        extra_keywords.append(value)
        if len(extra_keywords) >= 4:
            break

    if not extra_keywords:
        return None

    expanded = dict(plan)
    expanded["keywords"] = (existing_keywords + extra_keywords)[:8]
    expanded["notes"] = " ".join(
        part for part in [
            str(plan.get("notes") or "").strip(),
            f"Low-result auto expansion applied after initial recall of {total_papers} papers.",
        ] if part
    )
    return expanded


def _apply_research_result_review(job_id: str, prompt: str, plan: dict, result: dict) -> dict:
    json_path = Path(result.get("json_path") or "")
    csv_path = Path(result.get("csv_path") or "")
    site_path = Path(result.get("site_path") or "")
    meta_path = Path(result.get("meta_path") or "")
    if not json_path.exists() or not csv_path.exists() or not site_path.exists() or not meta_path.exists():
        return result

    payload = read_json_file(json_path, {})
    records = payload.get("papers") or []
    meta = payload.get("meta") or read_json_file(meta_path, {})
    if not isinstance(records, list) or not records:
        meta["review_summary"] = {"high": 0, "medium": 0, "low": 0}
        write_json(records, str(json_path), meta)
        write_site(records, str(site_path), meta)
        return {**result, "review_summary": meta["review_summary"]}

    def persist_review_progress(snapshot: list[dict], *, message: str):
        summary = {"high": 0, "medium": 0, "low": 0}
        for item in snapshot:
            label = str(item.get("relevance_label") or "").strip().lower()
            if label in summary:
                summary[label] += 1
        interim_meta = dict(meta)
        interim_meta["review_summary"] = summary
        interim_meta["total_papers"] = len(snapshot)
        write_csv(snapshot, str(csv_path))
        write_json(snapshot, str(json_path), interim_meta)
        write_site(snapshot, str(site_path), interim_meta)
        with meta_path.open("w", encoding="utf-8") as fh:
            json.dump(interim_meta, fh, ensure_ascii=False, indent=2)
        update_research_job(
            job_id,
            lambda payload: payload.update(
                {
                    "status": "running",
                    "current_step": "reviewing_results",
                    "step_message": message,
                    "review_summary": summary,
                    "total_papers": len(snapshot),
                }
            ),
        )

    def on_review_progress(event: dict):
        papers_snapshot = [dict(item or {}) for item in (event.get("papers") or [])]
        if not papers_snapshot:
            return
        message = str(event.get("message") or "正在复核搜索结果。").strip()
        persist_review_progress(papers_snapshot, message=message)

    reviewed = review_research_results(
        prompt,
        plan,
        records,
        model_id=OPENCLAW_INGEST_MODEL,
        fallback_model_id=OPENCLAW_INGEST_FALLBACK_MODEL,
        config_path=OPENCLAW_CONFIG_PATH,
        progress_callback=on_review_progress,
    )
    reviewed.sort(
        key=lambda item: (
            -(float(item.get("relevance_score") or 0)),
            -(int(item.get("year") or 0) if str(item.get("year") or "").isdigit() else 0),
            str(item.get("title") or ""),
        )
    )
    for index, item in enumerate(reviewed, start=1):
        item["csv_index"] = index

    summary = {"high": 0, "medium": 0, "low": 0}
    for item in reviewed:
        label = str(item.get("relevance_label") or "medium").strip().lower()
        if label not in summary:
            label = "medium"
        summary[label] += 1

    meta["review_summary"] = summary
    meta["total_papers"] = len(reviewed)
    write_csv(reviewed, str(csv_path))
    write_json(reviewed, str(json_path), meta)
    write_site(reviewed, str(site_path), meta)
    with meta_path.open("w", encoding="utf-8") as fh:
        json.dump(meta, fh, ensure_ascii=False, indent=2)
    return {**result, "review_summary": summary, "total_papers": len(reviewed)}


def _run_research_job(job_id: str, username: str = ""):
    with user_context(username):
        _run_research_job_inner(job_id)


def _run_research_job_inner(job_id: str):
    def set_step(step: str, message: str, **extra):
        def mutate(job):
            job["status"] = "running"
            job["current_step"] = step
            job["step_message"] = message
            logs = job.setdefault("logs", [])
            logs.append({"at": utc_now(), "step": step, "message": message})
            job["logs"] = logs[-40:]
            for key, value in extra.items():
                job[key] = value
        update_research_job(job_id, mutate)

    acquired_slot = False
    try:
        job = load_research_job(job_id)
        if not job:
            raise ValueError("research 任务不存在")
        prompt = job.get("prompt") or ""
        plan = job.get("plan") or {}

        update_research_job(
            job_id,
            lambda payload: payload.update(
                {
                    "status": "queued",
                    "current_step": "queued",
                    "step_message": f"任务已进入队列，最多同时运行 {MAX_CONCURRENT_RESEARCH_JOBS} 个搜索任务。",
                }
            ),
        )
        RESEARCH_JOB_SEMAPHORE.acquire()
        acquired_slot = True

        if plan:
            set_step("planned", "已加载预览方案，开始执行搜索。")
        else:
            set_step("planning", "正在调用模型生成 research 搜索方案。")
            plan = plan_research_request(
                prompt,
                model_id=OPENCLAW_INGEST_MODEL,
                reviewer_model_id=OPENCLAW_INGEST_CHECK_MODEL,
                fallback_model_id=OPENCLAW_INGEST_FALLBACK_MODEL,
                config_path=OPENCLAW_CONFIG_PATH,
            )
            update_research_job(job_id, lambda payload: payload.update({"plan": plan}))
            set_step("planned", "已生成搜索方案，开始执行搜索。")

        result = _run_search_in_openclaw_analytics(job_id, plan)
        expanded_plan = _expand_research_plan_for_low_results(plan, result)
        if expanded_plan:
            update_research_job(
                job_id,
                lambda payload: payload.update(
                    {
                        "step_message": (
                            f"首次搜索仅得到 {int(result.get('total_papers') or 0)} 篇，"
                            "正在补充智能建议检索词后重试。"
                        ),
                        "plan": expanded_plan,
                    }
                ),
            )
            plan = expanded_plan
            result = _run_search_in_openclaw_analytics(job_id, plan)
        set_step("reviewing_results", "正在根据标题和摘要复核相关性，并自动打标签。")
        result = _apply_research_result_review(job_id, prompt, plan, result)

        update_research_job(
            job_id,
            lambda payload: payload.update(
                {
                    "status": "completed",
                    "current_step": "completed",
                    "step_message": "Research 搜索已完成。",
                    "result_dir": str(result["out_dir"]),
                    "site_url": result["site_url"],
                    "site_relative_url": result.get("site_relative_url") or "",
                    "csv_url": result.get("csv_url") or "",
                    "json_url": result.get("json_url") or "",
                    "search_url": result.get("search_url") or "",
                    "total_papers": result["total_papers"],
                    "hit_counts": result["hit_counts"],
                    "review_summary": result.get("review_summary") or {},
                }
            ),
        )
    except Exception as exc:
        update_research_job(
            job_id,
            lambda payload: payload.update(
                {
                    "status": "failed",
                    "current_step": "failed",
                    "step_message": f"Research 执行失败: {exc}",
                    "error": str(exc),
                }
            ),
        )
    finally:
        if acquired_slot:
            RESEARCH_JOB_SEMAPHORE.release()


def start_research_job(prompt: str) -> dict:
    text = " ".join(str(prompt or "").split())
    if not text:
        raise ValueError("research 需求不能为空")
    return start_research_job_with_plan(text, None)


def preview_research_plan(prompt: str) -> dict:
    text = " ".join(str(prompt or "").split())
    if not text:
        raise ValueError("research 需求不能为空")
    return plan_research_request(
        text,
        model_id=OPENCLAW_INGEST_MODEL,
        reviewer_model_id=OPENCLAW_INGEST_CHECK_MODEL,
        fallback_model_id=OPENCLAW_INGEST_FALLBACK_MODEL,
        config_path=OPENCLAW_CONFIG_PATH,
    )


def compose_research_plan_request(latest_input: str, current_prompt: str = "", current_plan: dict | None = None) -> dict:
    return compose_research_plan(
        latest_input,
        current_prompt=current_prompt,
        current_plan=current_plan,
        model_id=OPENCLAW_INGEST_MODEL,
        reviewer_model_id=OPENCLAW_INGEST_CHECK_MODEL,
        fallback_model_id=OPENCLAW_INGEST_FALLBACK_MODEL,
        config_path=OPENCLAW_CONFIG_PATH,
        fast_preview=True,
    )


def revise_research_plan(prompt: str, current_plan: dict, modify_request: str) -> dict:
    text = " ".join(str(prompt or "").split())
    if not text:
        raise ValueError("research 需求不能为空")
    return refine_research_plan(
        text,
        current_plan,
        modify_request,
        model_id=OPENCLAW_INGEST_MODEL,
        reviewer_model_id=OPENCLAW_INGEST_CHECK_MODEL,
        fallback_model_id=OPENCLAW_INGEST_FALLBACK_MODEL,
        config_path=OPENCLAW_CONFIG_PATH,
    )


def verify_research_plan(prompt: str, current_plan: dict) -> dict:
    text = " ".join(str(prompt or "").split())
    if not text:
        raise ValueError("research 需求不能为空")
    return validate_research_plan(
        text,
        current_plan,
        reviewer_model_id=OPENCLAW_INGEST_CHECK_MODEL,
        fallback_model_id=OPENCLAW_INGEST_FALLBACK_MODEL,
        config_path=OPENCLAW_CONFIG_PATH,
    )


def start_research_job_with_plan(prompt: str, plan: dict | None) -> dict:
    text = " ".join(str(prompt or "").split())
    if not text:
        raise ValueError("research 需求不能为空")
    ensure_research_jobs_dir()
    username = current_username() or openclaw_default_username()
    job = {
        "id": build_research_job_id(),
        "type": "natural_language_research",
        "username": username,
        "status": "queued",
        "prompt": text,
        "plan": plan or None,
        "current_step": "queued",
        "step_message": f"已收到需求，等待队列调度。最多同时运行 {MAX_CONCURRENT_RESEARCH_JOBS} 个搜索任务。",
        "progress": {},
        "logs": [{"at": utc_now(), "step": "queued", "message": f"已收到需求，等待队列调度。最多同时运行 {MAX_CONCURRENT_RESEARCH_JOBS} 个搜索任务。"}],
        "created_at": utc_now(),
        "updated_at": utc_now(),
    }
    save_research_job(job)
    thread = threading.Thread(target=_run_research_job, args=(job["id"], job.get("username") or ""), daemon=True)
    thread.start()
    return load_research_job(job["id"]) or job
