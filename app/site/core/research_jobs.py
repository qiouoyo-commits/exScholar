"""Natural-language research planning and background search jobs for exScholar."""

from .shared import *


def ensure_research_jobs_dir():
    RESEARCH_JOBS_DIR.mkdir(parents=True, exist_ok=True)


def build_research_job_id() -> str:
    return f"rjob_{datetime.utcnow().strftime('%Y%m%d_%H%M%S')}_{secrets.token_hex(3)}"


def research_job_path(job_id: str) -> Path:
    ensure_research_jobs_dir()
    return RESEARCH_JOBS_DIR / f"{job_id}.json"


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
    return read_json_file(path, None)


def list_research_jobs(limit: int = 20) -> list[dict]:
    ensure_research_jobs_dir()
    jobs = []
    for path in sorted(RESEARCH_JOBS_DIR.glob("*.json"), reverse=True):
        payload = read_json_file(path, None)
        if not payload:
            continue
        jobs.append(summarize_research_job(payload))
        if len(jobs) >= limit:
            break
    return jobs


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


def _run_research_job(job_id: str):
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

        def on_progress(stage: str, message: str, extra: dict):
            update_research_job(
                job_id,
                lambda payload: payload.update(
                    {
                        "status": "running",
                        "current_step": stage,
                        "step_message": message,
                        "progress": extra or {},
                    }
                ),
            )

        result = run_topic_search(
            keywords=plan.get("keywords") or [],
            venues=plan.get("venues") or [],
            slug=plan.get("slug") or "",
            top=int(plan.get("top") or 100),
            year_from=int(plan.get("year_from") or 0),
            fetch_abstract=bool(plan.get("fetch_abstract", True)),
            progress_callback=on_progress,
        )

        csv_url = _research_relative_url(result["csv_path"])
        json_url = _research_relative_url(result["json_path"])
        search_url = _research_relative_url(result["meta_path"])
        site_relative_url = ""
        try:
            relative_dir = Path(result["out_dir"]).relative_to(DATA_DIR).as_posix()
            site_relative_url = f"/{relative_dir}/site/"
        except Exception:
            site_relative_url = ""

        update_research_job(
            job_id,
            lambda payload: payload.update(
                {
                    "status": "completed",
                    "current_step": "completed",
                    "step_message": "Research 搜索已完成。",
                    "result_dir": str(result["out_dir"]),
                    "site_url": result["site_url"],
                    "site_relative_url": site_relative_url,
                    "csv_url": csv_url,
                    "json_url": json_url,
                    "search_url": search_url,
                    "total_papers": result["total_papers"],
                    "hit_counts": result["hit_counts"],
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
    job = {
        "id": build_research_job_id(),
        "type": "natural_language_research",
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
    thread = threading.Thread(target=_run_research_job, args=(job["id"],), daemon=True)
    thread.start()
    return load_research_job(job["id"]) or job
