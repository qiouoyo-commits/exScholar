#!/usr/bin/env python3
import json
from pathlib import Path

import requests
from pypdf import PdfReader


DEFAULT_OPENCLAW_CONFIG_PATH = Path.home() / ".openclaw" / "openclaw.json"
DEFAULT_OPENCLAW_MODEL = "joybuilder-plan/DeepSeek-V3.2"
DEFAULT_OPENCLAW_CHECK_MODEL = "joybuilder-plan/GLM-5"
DEFAULT_OPENCLAW_FALLBACK_MODEL = "joybuilder-plan/Kimi-K2.5"


class OpenClawIngestError(RuntimeError):
    pass


def _read_json(path: Path) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise OpenClawIngestError(f"OpenClaw 配置不存在: {path}") from exc
    except Exception as exc:
        raise OpenClawIngestError(f"无法读取 OpenClaw 配置: {path}") from exc


def resolve_openclaw_model(
    model_id: str = DEFAULT_OPENCLAW_MODEL,
    config_path: str | Path = DEFAULT_OPENCLAW_CONFIG_PATH,
) -> dict:
    payload = _read_json(Path(config_path))
    providers = (((payload.get("models") or {}).get("providers") or {}))
    aliases = (((payload.get("agents") or {}).get("defaults") or {}).get("models") or {})

    target = (model_id or "").strip()
    if not target:
        raise OpenClawIngestError("未指定 OpenClaw 模型。")

    if "/" not in target:
        alias = aliases.get(target) or {}
        resolved = (alias.get("alias") or "").strip()
        if resolved:
            target = resolved

    provider_name, _, provider_model = target.partition("/")
    provider = providers.get(provider_name)
    if not provider:
        raise OpenClawIngestError(f"OpenClaw 未配置 provider: {provider_name}")

    model_cfg = None
    for item in provider.get("models") or []:
        if (item.get("id") or "").strip() == provider_model:
            model_cfg = item
            break
    if not model_cfg:
        raise OpenClawIngestError(f"OpenClaw 未配置模型: {target}")

    return {
        "model_id": target,
        "provider_name": provider_name,
        "provider_model": provider_model,
        "provider": provider,
        "model": model_cfg,
    }


def _extract_json_object(text: str) -> dict:
    body = (text or "").strip()
    if not body:
        raise OpenClawIngestError("模型未返回内容。")
    try:
        return json.loads(body)
    except Exception:
        start = body.find("{")
        end = body.rfind("}")
        if start >= 0 and end > start:
            return json.loads(body[start : end + 1])
    raise OpenClawIngestError("模型返回的内容不是合法 JSON。")


def _normalize_metadata_payload(payload: dict) -> dict:
    authors = payload.get("authors") or []
    if isinstance(authors, str):
        authors = [part.strip() for part in authors.replace(";", ",").split(",") if part.strip()]
    elif isinstance(authors, list):
        authors = [" ".join(str(item).strip().split()) for item in authors if str(item).strip()]
    else:
        authors = []

    doi = " ".join(str(payload.get("doi") or "").strip().split()).lower()
    for prefix in ("https://doi.org/", "http://doi.org/", "doi:"):
        if doi.startswith(prefix):
            doi = doi[len(prefix) :]

    return {
        "title": " ".join(str(payload.get("title") or "").split()),
        "authors": authors,
        "venue": " ".join(str(payload.get("venue") or "").split()),
        "year": str(payload.get("year") or "").strip(),
        "doi": doi.strip(),
        "abstract": " ".join(str(payload.get("abstract") or "").split()),
    }


def _blank_analysis_payload() -> dict:
    return {
        "overview": {
            "paper_type": "",
            "research_theme": "",
            "core_problem": "",
            "core_approach": "",
            "contributions": [],
        },
        "problem": {
            "background": "",
            "gap": "",
            "importance": "",
            "research_goal": "",
            "paper_logic": [],
        },
        "method": {
            "object_of_study": "",
            "method_goal": "",
            "pipeline": [],
            "design_choices": [],
            "participants_or_data": "",
            "evaluation_setup": "",
            "analysis_method": "",
        },
        "results": {
            "findings": [],
            "key_figures": [],
            "author_claims": [],
            "claim_evidence_match": "",
        },
        "critique": {
            "strengths": [],
            "limitations": [],
            "hidden_assumptions": [],
            "weak_points": [],
            "future_directions": [],
            "research_positioning": "",
        },
    }


def _normalize_analysis_payload(payload: dict) -> dict:
    normalized = _blank_analysis_payload()
    if not isinstance(payload, dict):
        return normalized
    for key in normalized:
        incoming = payload.get(key)
        if isinstance(normalized[key], dict) and isinstance(incoming, dict):
            normalized[key].update(incoming)
        elif incoming is not None:
            normalized[key] = incoming
    return normalized


def _has_meaningful_text(value) -> bool:
    if isinstance(value, str):
        return bool(value.strip())
    if isinstance(value, dict):
        return any(_has_meaningful_text(item) for item in value.values())
    if isinstance(value, list):
        return any(_has_meaningful_text(item) for item in value)
    return bool(value)


def _metadata_is_usable(payload: dict) -> bool:
    if not isinstance(payload, dict):
        return False
    title = str(payload.get("title") or "").strip()
    if not title:
        return False
    supporting = [
        payload.get("authors") or [],
        payload.get("year") or "",
        payload.get("venue") or "",
        payload.get("doi") or "",
        payload.get("abstract") or "",
    ]
    return any(_has_meaningful_text(item) for item in supporting)


def _analysis_is_usable(payload: dict) -> bool:
    if not isinstance(payload, dict):
        return False
    required_keys = ("overview", "problem", "method", "results", "critique")
    if any(key not in payload for key in required_keys):
        return False
    return any(_has_meaningful_text(payload.get(key)) for key in required_keys)


def _review_prompt(
    *,
    task_name: str,
    source_text: str,
    candidate_payload: dict,
    criteria_text: str,
) -> str:
    sample = (source_text or "")[:35000]
    candidate_json = json.dumps(candidate_payload, ensure_ascii=False, indent=2)
    return f"""
你是一个严格的结果审核器。请检查下面这份 {task_name} JSON 是否符合要求，并只返回一个 JSON 对象。

返回 schema:
{{
  "pass": true,
  "issues": [""],
  "reason": "",
  "corrected_json": null
}}

规则：
- `pass=true` 仅在候选结果基本可信且满足要求时使用。
- 如果发现明显缺字段、内容空泛、与原文不符、JSON 结构不合要求，设为 `pass=false`。
- `issues` 用中文列出主要问题。
- 如果你可以直接在不编造的前提下修正候选结果，就把修正后的 JSON 放进 `corrected_json`；否则填 `null`。
- 不要输出 Markdown，不要解释过程。

审核标准：
{criteria_text}

候选 JSON：
{candidate_json}

原始 PDF 文本片段：
{sample}
""".strip()


def _review_answer_prompt(
    *,
    question: str,
    source_text: str,
    candidate_answer: str,
) -> str:
    sample = (source_text or "")[:30000]
    return f"""
你是一个严格的论文问答审核器。请检查下面这个回答是否只基于论文材料、是否清楚、是否没有编造，并只返回一个 JSON 对象。

schema:
{{
  "pass": true,
  "issues": [""],
  "reason": "",
  "corrected_answer": ""
}}

规则：
- `pass=true` 表示回答基本可信、贴合问题、未明显编造。
- 如果回答不够好、与论文证据不一致、过于空泛、或明显遗漏关键信息，设为 `pass=false`。
- 如果你可以在不编造的前提下修正回答，就在 `corrected_answer` 中给出修正版中文答案；否则填空字符串。
- 不要输出 Markdown，不要解释过程。

问题：
{question}

候选回答：
{candidate_answer}

论文文本片段：
{sample}
""".strip()


def _review_candidate(
    *,
    task_name: str,
    source_text: str,
    candidate_payload: dict,
    criteria_text: str,
    review_model_id: str | None,
    config_path: str | Path,
    timeout: int = 180,
) -> dict:
    if not review_model_id:
        return {"pass": True, "issues": [], "reason": "未配置检查模型。", "corrected_json": None}
    review = _request_json_payload(
        model_id=review_model_id,
        messages=[
            {
                "role": "system",
                "content": "你负责审核结构化 JSON 结果。只返回严格合法的 JSON，不要输出任何额外说明。",
            },
            {
                "role": "user",
                "content": _review_prompt(
                    task_name=task_name,
                    source_text=source_text,
                    candidate_payload=candidate_payload,
                    criteria_text=criteria_text,
                ),
            },
        ],
        config_path=config_path,
        timeout=timeout,
        attempts=2,
    )
    return {
        "pass": bool(review.get("pass")),
        "issues": review.get("issues") or [],
        "reason": str(review.get("reason") or "").strip(),
        "corrected_json": review.get("corrected_json"),
    }


def _request_chat_completion(
    *,
    model_id: str,
    messages: list[dict],
    temperature: float = 0,
    config_path: str | Path = DEFAULT_OPENCLAW_CONFIG_PATH,
    timeout: int = 240,
) -> str:
    resolved = resolve_openclaw_model(model_id=model_id, config_path=config_path)
    provider = resolved["provider"]
    base_url = (provider.get("baseUrl") or "").strip().rstrip("/")
    api_key = (provider.get("apiKey") or "").strip()
    provider_model = resolved["provider_model"]
    if not base_url or not api_key:
        raise OpenClawIngestError(f"OpenClaw 模型 {model_id} 缺少 baseUrl 或 apiKey 配置。")

    url = f"{base_url}/chat/completions"
    payload = {
        "model": provider_model,
        "messages": messages,
        "temperature": temperature,
    }
    response = requests.post(
        url,
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        json=payload,
        timeout=timeout,
    )
    try:
        response.raise_for_status()
    except requests.HTTPError as exc:
        detail = ""
        try:
            body = response.json()
            detail = (((body.get("error") or {}).get("message")) or "").strip()
        except Exception:
            detail = (response.text or "").strip()
        detail = detail or f"HTTP {response.status_code}"
        raise OpenClawIngestError(f"OpenClaw 模型调用失败: {detail}") from exc

    data = response.json()
    content = (((data.get("choices") or [{}])[0].get("message") or {}).get("content") or "").strip()
    if not content:
        raise OpenClawIngestError("OpenClaw 模型没有返回正文。")
    return content


def _request_json_payload(
    *,
    model_id: str,
    messages: list[dict],
    config_path: str | Path,
    timeout: int,
    attempts: int = 2,
) -> dict:
    last_error = None
    current_messages = list(messages)
    for attempt in range(1, max(attempts, 1) + 1):
        try:
            content = _request_chat_completion(
                model_id=model_id,
                config_path=config_path,
                temperature=0,
                messages=current_messages,
                timeout=timeout,
            )
            return _extract_json_object(content)
        except Exception as exc:
            last_error = exc
            if attempt >= attempts:
                break
            current_messages = list(messages) + [
                {
                    "role": "user",
                    "content": "你上一轮的输出没有被成功解析。请重新只输出一个严格合法的 JSON 对象，不要输出 Markdown、解释或代码块。",
                }
            ]
    raise OpenClawIngestError(str(last_error) if last_error else "模型 JSON 输出失败。")


def extract_pdf_bundle(pdf_path: str | Path) -> dict:
    path = Path(pdf_path)
    if not path.exists():
        raise OpenClawIngestError(f"PDF 不存在: {path}")

    reader = PdfReader(str(path))
    pages = []
    sections = []
    chunks = []
    for index, page in enumerate(reader.pages, start=1):
        try:
            text = (page.extract_text() or "").strip()
        except Exception:
            text = ""
        pages.append({"page": index, "text": text})
        if text:
            chunks.append(f"[Page {index}]\n{text}")
            sections.append({"id": f"P{index}", "heading": f"Page {index}", "content": text})

    full_text = "\n\n".join(chunk for chunk in chunks if chunk).strip()
    return {
        "text": full_text,
        "pages": pages,
        "page_count": len(reader.pages),
        "sections": sections,
    }


def _metadata_prompt(extracted_text: str, filename: str = "") -> str:
    sample = (extracted_text or "")[:40000]
    return f"""
你正在从一篇学术论文 PDF 中抽取书目信息。
请只返回一个 JSON 对象，不要输出 Markdown、解释或代码块。

规则：
- 以 PDF 提取文本为唯一主要依据。
- 如果不确定，返回空字符串。
- authors 必须是作者姓名数组。
- year 如果存在，必须是四位年份字符串。
- doi 必须标准化，不能包含 https://doi.org/ 前缀。
- abstract 尽量保持与原文一致。

schema:
{{
  "title": "",
  "authors": [""],
  "venue": "",
  "year": "",
  "doi": "",
  "abstract": ""
}}

文件名: {filename}

PDF 文本:
{sample}
""".strip()


def extract_metadata_from_text(
    extracted_text: str,
    *,
    filename: str = "",
    model_id: str = DEFAULT_OPENCLAW_MODEL,
    reviewer_model_id: str | None = DEFAULT_OPENCLAW_CHECK_MODEL,
    fallback_model_id: str | None = DEFAULT_OPENCLAW_FALLBACK_MODEL,
    config_path: str | Path = DEFAULT_OPENCLAW_CONFIG_PATH,
) -> dict:
    def _run_generation(target_model_id: str) -> dict:
        payload = _request_json_payload(
            model_id=target_model_id,
            messages=[
                {
                    "role": "system",
                    "content": "你负责抽取学术论文元数据。只返回严格合法的 JSON，不要输出任何额外说明。",
                },
                {"role": "user", "content": _metadata_prompt(extracted_text, filename)},
            ],
            config_path=config_path,
            timeout=180,
            attempts=2,
        )
        return _normalize_metadata_payload(payload)

    primary_error = None
    try:
        primary = _run_generation(model_id)
        if _metadata_is_usable(primary):
            review = _review_candidate(
                task_name="论文元数据",
                source_text=extracted_text,
                candidate_payload=primary,
                criteria_text="至少应识别出标题，并尽量识别作者、年份、venue、doi、abstract。不要凭空编造。",
                review_model_id=reviewer_model_id,
                config_path=config_path,
            )
            corrected = review.get("corrected_json")
            if review.get("pass"):
                if isinstance(corrected, dict):
                    normalized = _normalize_metadata_payload(corrected)
                    if _metadata_is_usable(normalized):
                        return normalized
                return primary
        else:
            review = {"pass": False, "issues": ["主模型输出缺少足够元数据。"], "reason": "", "corrected_json": None}
    except Exception as exc:
        primary_error = exc
        review = {"pass": False, "issues": [str(exc)], "reason": "主模型读取失败。", "corrected_json": None}

    if not fallback_model_id:
        detail = "; ".join(str(item) for item in review.get("issues") or [] if str(item).strip())
        if primary_error:
            raise OpenClawIngestError(f"元数据抽取失败: {detail or str(primary_error)}")
        raise OpenClawIngestError(f"元数据抽取未通过检查: {detail or '结果不符合要求'}")

    fallback = _run_generation(fallback_model_id)
    if not _metadata_is_usable(fallback):
        raise OpenClawIngestError("Kimi 回退后仍未得到可用的元数据结果。")
    return fallback


def _analysis_prompt(paper: dict, extracted_text: str) -> str:
    sample = (extracted_text or "")[:120000]
    return f"""
你正在阅读一篇学术论文 PDF，并且必须只输出一个 JSON 对象。

顶层键必须是：
- overview
- problem
- method
- results
- critique

规则：
- 只输出合法 JSON，不要输出 Markdown、解释或代码块。
- 所有自然语言内容一律用中文输出。
- 专有名词、方法名、模型名、数据集名可以保留英文。
- 如果证据不足，用空字符串或空数组，不要编造。

论文元信息：
title: {paper.get("title") or ""}
authors: {", ".join(paper.get("authors") or [])}
venue: {paper.get("venue") or ""}
year: {paper.get("year") or ""}
doi: {paper.get("doi") or ""}

schema:
{{
  "overview": {{
    "paper_type": "",
    "research_theme": "",
    "core_problem": "",
    "core_approach": "",
    "contributions": ["", ""]
  }},
  "problem": {{
    "background": "",
    "gap": "",
    "importance": "",
    "research_goal": "",
    "paper_logic": [
      {{"step": 1, "label": "Problem", "content": ""}},
      {{"step": 2, "label": "Approach", "content": ""}},
      {{"step": 3, "label": "Evaluation", "content": ""}},
      {{"step": 4, "label": "Findings", "content": ""}},
      {{"step": 5, "label": "Implications", "content": ""}}
    ]
  }},
  "method": {{
    "object_of_study": "",
    "method_goal": "",
    "pipeline": ["", ""],
    "design_choices": [
      {{"choice": "", "why_it_matters": ""}}
    ],
    "participants_or_data": "",
    "evaluation_setup": "",
    "analysis_method": ""
  }},
  "results": {{
    "findings": [
      {{"id": "F1", "claim": "", "evidence": "", "figure_refs": [], "support_level": ""}}
    ],
    "key_figures": [
      {{"figure_id": "", "title": "", "what_it_shows": "", "why_it_matters": ""}}
    ],
    "author_claims": [""],
    "claim_evidence_match": ""
  }},
  "critique": {{
    "strengths": [""],
    "limitations": [""],
    "hidden_assumptions": [""],
    "weak_points": [""],
    "future_directions": [""],
    "research_positioning": ""
  }}
}}

PDF 文本:
{sample}
""".strip()


def generate_analysis_from_text(
    paper: dict,
    extracted_text: str,
    *,
    model_id: str = DEFAULT_OPENCLAW_MODEL,
    reviewer_model_id: str | None = DEFAULT_OPENCLAW_CHECK_MODEL,
    fallback_model_id: str | None = DEFAULT_OPENCLAW_FALLBACK_MODEL,
    config_path: str | Path = DEFAULT_OPENCLAW_CONFIG_PATH,
) -> dict:
    def _run_generation(target_model_id: str) -> dict:
        payload = _request_json_payload(
            model_id=target_model_id,
            messages=[
                {
                    "role": "system",
                    "content": "你是一个严谨的论文精读助手。你只输出严格合法的 JSON，并且所有自然语言分析内容都使用中文。",
                },
                {"role": "user", "content": _analysis_prompt(paper, extracted_text)},
            ],
            config_path=config_path,
            timeout=240,
            attempts=2,
        )
        return _normalize_analysis_payload(payload)

    primary_error = None
    try:
        primary = _run_generation(model_id)
        if _analysis_is_usable(primary):
            review = _review_candidate(
                task_name="论文结构化分析",
                source_text=extracted_text,
                candidate_payload=primary,
                criteria_text="必须包含 overview、problem、method、results、critique 五个顶层部分，并且内容应尽量基于原文、有实际信息量、不能空泛编造。",
                review_model_id=reviewer_model_id,
                config_path=config_path,
                timeout=240,
            )
            corrected = review.get("corrected_json")
            if review.get("pass"):
                if isinstance(corrected, dict):
                    normalized = _normalize_analysis_payload(corrected)
                    if _analysis_is_usable(normalized):
                        return normalized
                return primary
        else:
            review = {"pass": False, "issues": ["主模型输出结构化分析内容不足。"], "reason": "", "corrected_json": None}
    except Exception as exc:
        primary_error = exc
        review = {"pass": False, "issues": [str(exc)], "reason": "主模型分析失败。", "corrected_json": None}

    if not fallback_model_id:
        detail = "; ".join(str(item) for item in review.get("issues") or [] if str(item).strip())
        if primary_error:
            raise OpenClawIngestError(f"论文分析失败: {detail or str(primary_error)}")
        raise OpenClawIngestError(f"论文分析未通过检查: {detail or '结果不符合要求'}")

    fallback = _run_generation(fallback_model_id)
    if not _analysis_is_usable(fallback):
        raise OpenClawIngestError("Kimi 回退后仍未得到可用的论文分析结果。")
    return fallback


def answer_question_from_text(
    paper: dict,
    analysis: dict,
    extracted_text: str,
    question: str,
    *,
    model_id: str = DEFAULT_OPENCLAW_MODEL,
    reviewer_model_id: str | None = DEFAULT_OPENCLAW_CHECK_MODEL,
    fallback_model_id: str | None = DEFAULT_OPENCLAW_FALLBACK_MODEL,
    config_path: str | Path = DEFAULT_OPENCLAW_CONFIG_PATH,
) -> str:
    overview = (((analysis or {}).get("modules") or {}).get("overview") or {}).get("data") or {}
    problem = (((analysis or {}).get("modules") or {}).get("problem") or {}).get("data") or {}
    method = (((analysis or {}).get("modules") or {}).get("method") or {}).get("data") or {}
    results = (((analysis or {}).get("modules") or {}).get("results") or {}).get("data") or {}
    critique = (((analysis or {}).get("modules") or {}).get("critique") or {}).get("data") or {}
    sample = (extracted_text or "")[:45000]
    prompt = f"""
你是一个严谨的论文问答助手。只根据给定论文材料回答，输出中文。

要求：
- 只基于论文材料回答，不要编造实验结果、数字或结论。
- 回答尽量具体、清楚，必要时分点。

论文标题：{paper.get("title") or ""}
作者：{", ".join(paper.get("authors") or [])}
venue：{paper.get("venue") or ""}
年份：{paper.get("year") or ""}
DOI：{paper.get("doi") or ""}

已有分析摘要：
overview: {json.dumps(overview, ensure_ascii=False)}
problem: {json.dumps(problem, ensure_ascii=False)}
method: {json.dumps(method, ensure_ascii=False)}
results: {json.dumps(results, ensure_ascii=False)}
critique: {json.dumps(critique, ensure_ascii=False)}

论文原文摘录：
{sample}

用户问题：
{question}
""".strip()

    def _run_answer(target_model_id: str) -> str:
        content = _request_chat_completion(
            model_id=target_model_id,
            config_path=config_path,
            temperature=0,
            messages=[
                {"role": "system", "content": "你是一个严谨的论文问答助手。只根据给定论文材料回答，输出中文。"},
                {"role": "user", "content": prompt},
            ],
            timeout=180,
        )
        answer = (content or "").strip()
        if not answer:
            raise OpenClawIngestError("模型没有返回问答内容。")
        return answer

    primary_error = None
    try:
        primary = _run_answer(model_id)
        if reviewer_model_id:
            review = _request_json_payload(
                model_id=reviewer_model_id,
                messages=[
                    {
                        "role": "system",
                        "content": "你负责审核论文问答回答。只返回严格合法的 JSON，不要输出任何额外说明。",
                    },
                    {
                        "role": "user",
                        "content": _review_answer_prompt(
                            question=question,
                            source_text=extracted_text,
                            candidate_answer=primary,
                        ),
                    },
                ],
                config_path=config_path,
                timeout=180,
                attempts=2,
            )
            if bool(review.get("pass")):
                corrected = str(review.get("corrected_answer") or "").strip()
                return corrected or primary
        else:
            return primary
    except Exception as exc:
        primary_error = exc

    if not fallback_model_id:
        raise OpenClawIngestError(str(primary_error) if primary_error else "问答生成失败。")
    fallback = _run_answer(fallback_model_id)
    if not fallback.strip():
        raise OpenClawIngestError("Kimi 回退后仍未得到可用问答结果。")
    return fallback
