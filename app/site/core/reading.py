"""Reading workspace and Q&A helpers for exScholar."""

from .base import *
from .storage import *
from .citations import *


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


def load_reading_full_text(paper_id: str) -> str:
    source_path = reading_workspace_path(paper_id) / "source" / "full_text.json"
    payload = read_json_file(source_path, {})
    text = (payload.get("text") or "").strip()
    if text:
        return text
    content = payload.get("content") or {}
    if isinstance(content, dict):
        normalized = normalize_full_text_payload(content)
        return normalized.get("text") or ""
    return ""


def append_reading_question_history(paper_id: str, question: str, answer: str) -> dict:
    path = reading_qa_history_path(paper_id)
    history = read_json_file(path, [])
    if not isinstance(history, list):
        history = []
    item = {
        "id": f"qa_{datetime.utcnow().strftime('%Y%m%d%H%M%S')}_{secrets.token_hex(3)}",
        "question": question,
        "answer": answer,
        "created_at": utc_now(),
    }
    history.append(item)
    write_json_file(path, history)
    return item


def delete_reading_question_history_item(paper_id: str, qa_id: str) -> bool:
    path = reading_qa_history_path(paper_id)
    history = read_json_file(path, [])
    if not isinstance(history, list):
        history = []
    kept = [item for item in history if str(item.get("id") or "") != qa_id]
    if len(kept) == len(history):
        return False
    write_json_file(path, kept)
    return True


def save_manual_note(paper_id: str, module_name: str, content: str) -> dict:
    allowed = {"overview", "problem", "method", "results", "critique"}
    if module_name not in allowed:
        raise ValueError("不支持的 Notes 模块")
    path = reading_notes_path(paper_id)
    notes = normalize_notes_payload(read_json_file(path, {}))
    notes[module_name] = content
    write_json_file(path, notes)
    return {"module": module_name, "content": content, "updated_at": utc_now()}


def answer_reading_question(paper_id: str, question: str) -> dict:
    bundle = load_reading_bundle(paper_id)
    if not bundle:
        raise ValueError("阅读工作区不存在")
    text = load_reading_full_text(paper_id)
    if not text:
        raise ValueError("当前阅读页还没有可用的论文文本，请先完成一次深度分析。")
    answer = answer_question_from_text(
        bundle["paper"],
        bundle["analysis"],
        text,
        question,
        model_id=OPENCLAW_INGEST_MODEL,
        reviewer_model_id=OPENCLAW_INGEST_CHECK_MODEL,
        fallback_model_id=OPENCLAW_INGEST_FALLBACK_MODEL,
        config_path=OPENCLAW_CONFIG_PATH,
    )
    return append_reading_question_history(paper_id, question, answer)


def ensure_reading_workspace_for_citation(citation_id: int):
    ensure_db()
    citation = get_citation_by_id(citation_id)
    if not citation:
        raise ValueError("Citation 不存在")
    library_pdf = citation_pdf_abspath(citation)
    if not library_pdf:
        raise ValueError("该文献尚未上传 PDF，暂时不能进入深度阅读。")

    paper_id = citation.get("reading_paper_id") or build_reading_paper_id(citation)
    workspace = reading_workspace_path(paper_id)
    source_dir = workspace / "source"
    source_dir.mkdir(parents=True, exist_ok=True)

    paper_json_path = workspace / "paper.json"
    analysis_json_path = workspace / "analysis.json"
    qa_history_path = reading_qa_history_path(paper_id)
    notes_path = reading_notes_path(paper_id)
    source_pdf_path = f"/{library_pdf.relative_to(DATA_DIR).as_posix()}"

    for stale_pdf in source_dir.glob("*.pdf"):
        try:
            stale_pdf.unlink()
        except Exception:
            pass

    now = utc_now()
    paper_payload = {
        "paper_id": paper_id,
        "title": citation.get("title") or "",
        "authors": split_authors(citation.get("authors") or ""),
        "year": int(citation["year"]) if str(citation.get("year") or "").isdigit() else citation.get("year") or None,
        "venue": citation.get("venue") or "",
        "doi": citation.get("doi") or None,
        "keywords": [part.strip() for part in (citation.get("tags") or "").split(",") if part.strip()],
        "pdf": {
            "file_name": Path(source_pdf_path).name if source_pdf_path else "",
            "file_path": source_pdf_path,
            "page_count": None,
            "uploaded_at": citation.get("created_at") or now,
        },
        "text_source": {
            "full_text_path": f"/reading/{paper_id}/source/full_text.json",
            "sections_path": f"/reading/{paper_id}/source/sections.json",
        },
        "status": {
            "ingestion": "completed" if source_pdf_path else "pending",
            "analysis": "pending",
            "metadata": "completed" if citation.get("title") else "pending",
        },
        "created_at": citation.get("created_at") or now,
        "updated_at": now,
    }
    write_json_file(paper_json_path, paper_payload)

    if not analysis_json_path.exists():
        write_json_file(analysis_json_path, default_analysis_payload(paper_id))
    if not qa_history_path.exists():
        qa_history_path.write_text("[]", encoding="utf-8")
    if not notes_path.exists():
        notes_path.write_text("{}", encoding="utf-8")

    with sqlite3.connect(DB_PATH) as conn:
        conn.execute("UPDATE citations SET reading_paper_id = ? WHERE id = ?", (paper_id, citation_id))
        conn.commit()

    return {
        "paper_id": paper_id,
        "workspace": workspace,
        "paper_json_path": paper_json_path,
        "analysis_json_path": analysis_json_path,
        "reading_url": f"/reading/{paper_id}",
        "links": build_reading_links(paper_id),
    }


def load_reading_bundle(paper_id: str):
    workspace = reading_workspace_path(paper_id)
    paper_json_path = workspace / "paper.json"
    analysis_json_path = workspace / "analysis.json"
    qa_history_path = reading_qa_history_path(paper_id)
    notes_path = reading_notes_path(paper_id)
    if not paper_json_path.exists() or not analysis_json_path.exists():
        return None
    try:
        paper = json.loads(paper_json_path.read_text(encoding="utf-8"))
        analysis = json.loads(analysis_json_path.read_text(encoding="utf-8"))
        qa_history = json.loads(qa_history_path.read_text(encoding="utf-8")) if qa_history_path.exists() else []
        notes = json.loads(notes_path.read_text(encoding="utf-8")) if notes_path.exists() else {}
    except Exception:
        return None
    return {
        "paper": paper,
        "analysis": analysis,
        "qa_history": qa_history if isinstance(qa_history, list) else [],
        "notes": normalize_notes_payload(notes),
        "workspace": workspace,
    }


def reading_json_ready(paper_id: str) -> bool:
    return bool(load_reading_bundle(paper_id))


def remove_reading_workspace_for_citation(citation_id: int):
    citation = get_citation_by_id(citation_id)
    if not citation:
        raise ValueError("Citation 不存在")
    paper_id = (citation.get("reading_paper_id") or "").strip()
    pdf_rel = (citation.get("pdf_path") or "").strip()
    pdf_sha256 = (citation.get("pdf_sha256") or "").strip()

    if paper_id:
        workspace = reading_workspace_path(paper_id)
        if workspace.exists():
            shutil.rmtree(workspace, ignore_errors=True)

    should_delete_pdf = False
    pdf_abs = None
    if pdf_rel:
        pdf_abs = DATA_DIR / pdf_rel
        ensure_db()
        with sqlite3.connect(DB_PATH) as conn:
            row = conn.execute(
                """
                SELECT COUNT(1)
                FROM citations
                WHERE id != ?
                  AND (
                    pdf_path = ?
                    OR (? != '' AND pdf_sha256 = ?)
                  )
                """,
                (citation_id, pdf_rel, pdf_sha256, pdf_sha256),
            ).fetchone()
        should_delete_pdf = not bool((row or [0])[0])

    delete_citation_group_links(citation_id)
    delete_citation(citation_id)

    if should_delete_pdf and pdf_abs and pdf_abs.exists():
        try:
            pdf_abs.unlink()
        except Exception:
            pass
    return True
