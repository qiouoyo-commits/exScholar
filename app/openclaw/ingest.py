#!/home/ubuntu/miniconda3/envs/openclaw-analytics/bin/python
import base64
import io
import json
import os
import random
import re
import threading
import time
from pathlib import Path

import requests
from PIL import Image
from pypdf import PdfReader


DEFAULT_OPENCLAW_CONFIG_PATH = Path.home() / ".openclaw" / "openclaw.json"
DEFAULT_OPENCLAW_MODEL = "joybuilder-plan/DeepSeek-V3.2"
DEFAULT_OPENCLAW_CHECK_MODEL = "joybuilder-plan/GLM-5"
DEFAULT_OPENCLAW_FALLBACK_MODEL = "joybuilder-plan/Kimi-K2.5"
DEFAULT_OPENCLAW_IMAGE_MODEL = "joybuilder-plan/Kimi-K2.5"
DEFAULT_OPENCLAW_IMAGE_FALLBACK_MODEL = "joybuilder-plan/DeepSeek-V3.2"
DEFAULT_RESEARCH_TOP = 100
DEFAULT_OPENCLAW_MODEL_CONCURRENCY = 1
DEFAULT_REVIEW_CHUNK_SIZE = 12
DEFAULT_REVIEW_BATCH_THROTTLE_SECONDS = 0.8
DEFAULT_JOYBUILDER_MIN_INTERVAL_SECONDS = 0.8
DEFAULT_KIMI_MIN_INTERVAL_SECONDS = 1.2
ALLOWED_RESEARCH_VENUES = [
    "chi", "uist", "cscw", "ubicomp",
    "dis", "nime", "iui", "tei", "mobilehci", "assets", "imwut", "its", "hri", "gi", "muc",
    "aaai", "nips", "acl", "cvpr", "iccv", "icml", "ijcai", "iclr", "emnlp", "naacl", "coling", "eccv",
    "asplos", "osdi", "sosp", "eurosys", "usenix_atc", "fast", "isca", "micro", "hpca",
    "ccs", "sp", "uss", "ndss", "crypto", "eurocrypt",
    "sigmod", "kdd", "icde", "sigir", "vldb",
    "sigcomm", "mobicom", "infocom", "nsdi",
    "icse", "fse_esec", "ase", "issta",
    "siggraph", "mm", "vis",
    "stoc", "focs", "soda",
]

ACM_HCI_VENUES = [
    "chi",
    "uist",
    "cscw",
    "ubicomp",
    "imwut",
    "dis",
    "iui",
    "tei",
    "mobilehci",
    "assets",
]

_GENERIC_RESEARCH_KEYWORD_TOKENS = {
    "analysis", "assessment", "effect", "effects", "evaluation", "factor", "factors",
    "hci", "human", "impact", "influence", "interaction", "interactions", "computer",
    "user", "users",
}

_HCI_FACTOR_GUIDED_CORE_CONCEPTS = [
    "user experience",
    "usability",
    "interaction outcomes",
    "human factors",
    "behavioral predictors",
]

_HCI_FACTOR_GUIDED_KEYWORDS = [
    "user experience factors",
    "usability predictors",
    "interaction outcomes",
    "human factors in HCI",
    "empirical HCI study",
    "interaction effects",
    "behavioral predictors",
    "predictors of user engagement",
    "mechanisms of user behavior",
]

_HCI_FACTOR_GUIDED_AVOID = [
    "analysis",
    "factors",
    "impact",
    "evaluation",
    "mechanism analysis",
    "influencing factors",
]

_RESEARCH_TAG_STOPWORDS = {
    "a", "an", "and", "as", "at", "based", "by", "for", "from", "in", "into", "of",
    "on", "or", "the", "to", "using", "with", "via", "toward", "towards", "study",
    "research", "paper", "analysis", "approach", "method", "methods", "evaluation",
}

OPENCLAW_MODEL_CONCURRENCY = max(
    1,
    int((os.getenv("OPENCLAW_MODEL_CONCURRENCY") or str(DEFAULT_OPENCLAW_MODEL_CONCURRENCY)).strip() or str(DEFAULT_OPENCLAW_MODEL_CONCURRENCY)),
)
REVIEW_RESEARCH_RESULTS_CHUNK_SIZE = max(
    1,
    int((os.getenv("REVIEW_RESEARCH_RESULTS_CHUNK_SIZE") or str(DEFAULT_REVIEW_CHUNK_SIZE)).strip() or str(DEFAULT_REVIEW_CHUNK_SIZE)),
)
REVIEW_BATCH_THROTTLE_SECONDS = max(
    0.0,
    float((os.getenv("REVIEW_BATCH_THROTTLE_SECONDS") or str(DEFAULT_REVIEW_BATCH_THROTTLE_SECONDS)).strip() or str(DEFAULT_REVIEW_BATCH_THROTTLE_SECONDS)),
)
JOYBUILDER_REQUEST_MIN_INTERVAL_SECONDS = max(
    0.0,
    float((os.getenv("JOYBUILDER_REQUEST_MIN_INTERVAL_SECONDS") or str(DEFAULT_JOYBUILDER_MIN_INTERVAL_SECONDS)).strip() or str(DEFAULT_JOYBUILDER_MIN_INTERVAL_SECONDS)),
)
KIMI_REQUEST_MIN_INTERVAL_SECONDS = max(
    0.0,
    float((os.getenv("KIMI_REQUEST_MIN_INTERVAL_SECONDS") or str(DEFAULT_KIMI_MIN_INTERVAL_SECONDS)).strip() or str(DEFAULT_KIMI_MIN_INTERVAL_SECONDS)),
)

MODEL_REQUEST_SEMAPHORE = threading.Semaphore(OPENCLAW_MODEL_CONCURRENCY)
MODEL_REQUEST_SCHEDULE_LOCK = threading.Lock()
MODEL_PROVIDER_NEXT_REQUEST_AT: dict[str, float] = {}
MODEL_HTTP_SESSION = None
MODEL_HTTP_SESSION_LOCK = threading.Lock()


class OpenClawIngestError(RuntimeError):
    pass


def model_http_transport_mode() -> str:
    return "no_proxy"


def _get_model_http_session() -> requests.Session:
    global MODEL_HTTP_SESSION
    with MODEL_HTTP_SESSION_LOCK:
        if MODEL_HTTP_SESSION is None:
            session = requests.Session()
            session.trust_env = False
            MODEL_HTTP_SESSION = session
    return MODEL_HTTP_SESSION


def _provider_min_interval_seconds(model_id: str) -> float:
    provider_name = str(model_id or "").partition("/")[0].strip().lower()
    if provider_name == "kimi":
        return KIMI_REQUEST_MIN_INTERVAL_SECONDS
    return JOYBUILDER_REQUEST_MIN_INTERVAL_SECONDS


def _acquire_model_request_slot(model_id: str):
    provider_name = str(model_id or "").partition("/")[0].strip().lower() or "default"
    min_interval = _provider_min_interval_seconds(model_id)
    MODEL_REQUEST_SEMAPHORE.acquire()
    try:
        if min_interval > 0:
            with MODEL_REQUEST_SCHEDULE_LOCK:
                now = time.time()
                next_allowed = MODEL_PROVIDER_NEXT_REQUEST_AT.get(provider_name, 0.0)
                sleep_seconds = max(0.0, next_allowed - now)
                reserve_base = max(now, next_allowed)
                jitter = random.uniform(0.0, min(0.25, min_interval * 0.25))
                MODEL_PROVIDER_NEXT_REQUEST_AT[provider_name] = reserve_base + min_interval + jitter
            if sleep_seconds > 0:
                time.sleep(sleep_seconds)
    except Exception:
        MODEL_REQUEST_SEMAPHORE.release()
        raise


def _release_model_request_slot():
    MODEL_REQUEST_SEMAPHORE.release()


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
        "keywords": _normalize_keyword_list(payload.get("keywords") or []),
    }


def _normalize_keyword_list(raw) -> list[str]:
    if isinstance(raw, str):
        parts = re.split(r"[;\n,，；]+", raw)
    elif isinstance(raw, list):
        parts = raw
    else:
        parts = []
    cleaned = []
    seen = set()
    for item in parts:
        value = " ".join(str(item or "").strip().split())
        low = value.lower()
        if not value or low in seen:
            continue
        seen.add(low)
        cleaned.append(value)
        if len(cleaned) >= 5:
            break
    return cleaned


def _keyword_phrase_candidates(text: str, *, max_terms: int = 5) -> list[str]:
    source = " ".join(str(text or "").split())
    if not source:
        return []
    pieces: list[str] = []
    for chunk in re.split(r"[:;|,()\\[\\]{}]+", source):
        value = " ".join(chunk.strip().split())
        if not value:
            continue
        lowered = value.lower()
        words = [w for w in re.findall(r"[A-Za-z0-9]+", lowered) if w]
        if not words:
            continue
        if all(word in _RESEARCH_TAG_STOPWORDS for word in words):
            continue
        if 1 <= len(words) <= 5:
            pieces.append(value)
    normalized = _normalize_keyword_list(pieces)
    return normalized[:max_terms]


def _heuristic_research_plan_from_suggestion(requirement_text: str, suggestion: dict) -> dict:
    normalized_suggestion = _apply_research_query_suggestion_heuristics(requirement_text, suggestion or {})
    normalized_suggestion["generation_mode"] = normalized_suggestion.get("generation_mode") or "heuristic"
    summary = _short_research_summary(
        normalized_suggestion.get("summary") or "",
        " ".join(normalized_suggestion.get("candidate_keywords") or []),
        requirement_text,
    )
    plan = {
        "summary": summary,
        "slug": _safe_slug(f"{summary} {requirement_text}"),
        "keywords": _merge_keyword_candidates(
            normalized_suggestion.get("candidate_keywords") or [],
            normalized_suggestion.get("core_concepts") or [],
            limit=6,
        ),
        "venues": [],
        "year_from": 0,
        "top": DEFAULT_RESEARCH_TOP,
        "fetch_abstract": True,
        "notes": normalized_suggestion.get("notes") or "",
        "query_suggestion": normalized_suggestion,
        "diagnostics": {
            "plan_generation_mode": "heuristic",
            "query_suggestion_mode": normalized_suggestion.get("generation_mode") or "heuristic",
            "model_http": model_http_transport_mode(),
        },
    }
    requirement_lower = " ".join(str(requirement_text or "").lower().split())
    year_matches = re.findall(r"\b(19\d{2}|20\d{2}|21\d{2})\b", requirement_lower)
    if year_matches:
        try:
            plan["year_from"] = max(0, min(int(year_matches[0]), 2100))
        except Exception:
            plan["year_from"] = 0
    plan = _prefer_acm_hci_venues_for_create(requirement_text, plan)
    plan = _apply_research_plan_heuristics(plan)
    return _normalize_research_plan_payload(plan)


def _normalize_research_query_suggestion(payload: dict) -> dict:
    if not isinstance(payload, dict):
        payload = {}
    return {
        "summary": " ".join(str(payload.get("summary") or "").split()),
        "core_concepts": _normalize_keyword_list(payload.get("core_concepts") or []),
        "candidate_keywords": _normalize_keyword_list(payload.get("candidate_keywords") or []),
        "avoid_keywords": _normalize_keyword_list(payload.get("avoid_keywords") or []),
        "notes": " ".join(str(payload.get("notes") or "").split()),
        "generation_mode": " ".join(str(payload.get("generation_mode") or "").split()),
    }


def _is_hci_factor_analysis_request(text: str) -> bool:
    lowered = " ".join(str(text or "").lower().split())
    if not lowered:
        return False
    hci_markers = (
        " hci ",
        "human-computer interaction",
        "human computer interaction",
        "人机交互",
    )
    padded = f" {lowered} "
    if not any(marker in padded for marker in hci_markers):
        return False
    factor_markers = (
        "factor",
        "factors",
        "impact",
        "influence",
        "effect",
        "effects",
        "evaluation",
        "assessment",
        "predictor",
        "predictors",
        "determinant",
        "determinants",
        "mechanism",
        "mechanisms",
        "作用机制",
        "影响因素",
        "决定因素",
        "预测因素",
        "机制",
    )
    return any(marker in lowered for marker in factor_markers)


def _keyword_looks_overly_generic_for_title_search(keyword: str) -> bool:
    tokens = re.findall(r"[a-z0-9]+", str(keyword or "").lower())
    if len(tokens) < 3:
        return False
    return all(token in _GENERIC_RESEARCH_KEYWORD_TOKENS for token in tokens)


def _merge_keyword_candidates(*groups, limit: int = 6) -> list[str]:
    merged: list[str] = []
    seen: set[str] = set()
    for group in groups:
        for item in group or []:
            value = " ".join(str(item or "").strip().split())
            lowered = value.lower()
            if not value or lowered in seen:
                continue
            seen.add(lowered)
            merged.append(value)
            if len(merged) >= limit:
                return merged
    return merged


def _apply_research_query_suggestion_heuristics(requirement_text: str, suggestion: dict) -> dict:
    normalized = _normalize_research_query_suggestion(suggestion)
    if not _is_hci_factor_analysis_request(requirement_text):
        return normalized

    filtered_candidates = [
        item for item in (normalized.get("candidate_keywords") or [])
        if not _keyword_looks_overly_generic_for_title_search(item)
    ]
    filtered_core = [
        item for item in (normalized.get("core_concepts") or [])
        if not _keyword_looks_overly_generic_for_title_search(item)
    ]
    normalized["core_concepts"] = _merge_keyword_candidates(
        filtered_core,
        _HCI_FACTOR_GUIDED_CORE_CONCEPTS,
        limit=5,
    )
    normalized["candidate_keywords"] = _merge_keyword_candidates(
        filtered_candidates,
        _HCI_FACTOR_GUIDED_KEYWORDS,
        normalized["core_concepts"],
        limit=6,
    )
    normalized["avoid_keywords"] = _merge_keyword_candidates(
        normalized.get("avoid_keywords") or [],
        _HCI_FACTOR_GUIDED_AVOID,
        limit=5,
    )
    if not normalized.get("summary"):
        normalized["summary"] = _short_research_summary(*normalized["candidate_keywords"], requirement_text)
    if not normalized.get("notes"):
        normalized["notes"] = "优先使用更像论文标题和摘要的 HCI 实证研究短语，避免解释型检索表达。"
    return normalized


_SUMMARY_STOPWORDS = {
    "a", "an", "and", "for", "from", "in", "of", "on", "the", "to", "with",
    "about", "since", "after", "before", "between", "using", "based", "study",
    "research", "papers", "paper", "latest", "recent", "new", "topic",
}
_SUMMARY_ACRONYMS = {
    "acm", "ai", "ar", "chi", "cscw", "cv", "cvpr", "dis", "gi", "hci", "hri",
    "iui", "its", "llm", "ml", "mm", "muc", "nime", "nlp", "sigir", "tei",
    "uist", "ui", "ux", "vis", "vr",
}


def _render_summary_token(token: str) -> str:
    value = str(token or "").strip()
    if not value:
        return ""
    lowered = value.lower()
    if lowered in _SUMMARY_ACRONYMS:
        return lowered.upper()
    if value.isdigit():
        return value
    return value.capitalize()


def _short_research_summary(*sources: str) -> str:
    candidates: list[str] = []
    seen: set[str] = set()
    fallback_tokens: list[str] = []
    for source in sources:
        for raw in re.findall(r"[A-Za-z0-9]+", str(source or "")):
            lowered = raw.lower()
            if lowered not in seen:
                seen.add(lowered)
                fallback_tokens.append(raw)
            if lowered in _SUMMARY_STOPWORDS:
                continue
            if lowered.isdigit() and len(lowered) == 4:
                continue
            if len(lowered) <= 1 and lowered not in _SUMMARY_ACRONYMS:
                continue
            if lowered not in {item.lower() for item in candidates}:
                candidates.append(raw)
            if len(candidates) >= 3:
                break
        if len(candidates) >= 3:
            break
    if len(candidates) < 2:
        for raw in fallback_tokens:
            lowered = raw.lower()
            if lowered.isdigit() and len(lowered) == 4:
                continue
            if lowered in {item.lower() for item in candidates}:
                continue
            candidates.append(raw)
            if len(candidates) >= 2:
                break
    if not candidates:
        return "Research Topic"
    return " ".join(_render_summary_token(item) for item in candidates[:3] if _render_summary_token(item))


def _research_query_suggestion_prompt(requirement_text: str) -> str:
    return f"""
你是一个学术检索词规划器。用户会用自然语言描述研究主题，你需要把它转成更贴合论文标题、摘要和学术检索的关键词建议。

只返回一个 JSON 对象，不要输出 Markdown、解释或代码块。

规则：
- `core_concepts` 是 2 到 5 个主题核心概念，优先英文名词短语。
- `candidate_keywords` 是 4 到 8 个更适合学术检索的英文关键词或短语。
- `avoid_keywords` 是 0 到 5 个应尽量避免的过泛词、歧义词或容易引入噪声的词。
- 关键词要尽量贴近学术论文标题和摘要中常出现的表达。
- 如果用户给的是口语表达，请转成更学术的写法。
- 不要优先输出解释型、方法型短语，例如 `impact analysis`、`factors influence`、`effects assessment`、`interaction evaluation`。
- 如果用户在中文里提到“影响因素 / 决定因素 / 预测因素 / 作用机制 / 影响分析”这类意图，优先改写成更适合标题检索的英文名词短语。
- 优先输出更像论文标题的名词短语，例如 `user experience factors`、`usability predictors`、`interaction outcomes`、`human factors in HCI`、`empirical HCI study`、`behavioral predictors`。
- 不要输出 venue 名、作者名、年份。

schema:
{{
  "summary": "",
  "core_concepts": ["", ""],
  "candidate_keywords": ["", "", ""],
  "avoid_keywords": ["", ""],
  "notes": ""
}}

用户需求：
{requirement_text}
""".strip()


def _research_query_suggestion_is_usable(payload: dict) -> bool:
    if not isinstance(payload, dict):
        return False
    return bool(payload.get("candidate_keywords"))


def suggest_research_queries(
    requirement_text: str,
    *,
    model_id: str = DEFAULT_OPENCLAW_MODEL,
    fallback_model_id: str | None = DEFAULT_OPENCLAW_FALLBACK_MODEL,
    config_path: str | Path = DEFAULT_OPENCLAW_CONFIG_PATH,
) -> dict:
    text = " ".join(str(requirement_text or "").split())
    if not text:
        raise OpenClawIngestError("研究需求不能为空。")

    def _run(target_model_id: str) -> dict:
        payload = _request_json_payload(
            model_id=target_model_id,
            messages=[
                {
                    "role": "system",
                    "content": "你负责把自然语言研究需求转换成更学术化的检索词建议。只返回严格合法的 JSON。",
                },
                {"role": "user", "content": _research_query_suggestion_prompt(text)},
            ],
            config_path=config_path,
            timeout=120,
            attempts=2,
            max_tokens=300,
        )
        normalized = _apply_research_query_suggestion_heuristics(text, payload)
        normalized["generation_mode"] = target_model_id
        return normalized

    try:
        suggestion = _run(model_id)
        if _research_query_suggestion_is_usable(suggestion):
            return suggestion
    except Exception:
        pass
    if fallback_model_id:
        try:
            suggestion = _run(fallback_model_id)
            if _research_query_suggestion_is_usable(suggestion):
                return suggestion
        except Exception:
            pass
    lowered = text.lower()
    heuristic_keywords = []
    for phrase in re.split(r"[，,;；/]|\\band\\b|\\bwith\\b", lowered):
        value = " ".join(phrase.strip().split())
        if value:
            heuristic_keywords.append(value)
    return _apply_research_query_suggestion_heuristics(
        text,
        {
            "summary": text,
            "core_concepts": heuristic_keywords[:4],
            "candidate_keywords": heuristic_keywords[:6],
            "avoid_keywords": [],
            "notes": "",
            "generation_mode": "heuristic",
        },
    )


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


def _keyword_prompt(metadata: dict) -> str:
    title = " ".join(str(metadata.get("title") or "").split())
    authors = ", ".join(metadata.get("authors") or [])
    venue = " ".join(str(metadata.get("venue") or "").split())
    year = str(metadata.get("year") or "").strip()
    doi = str(metadata.get("doi") or "").strip()
    abstract = " ".join(str(metadata.get("abstract") or "").split())[:4000]
    return f"""
你正在根据一篇学术论文的元数据规划 3 到 5 个学术关键词。
请只返回一个 JSON 对象，不要输出 Markdown、解释或代码块。

规则：
- 关键词应适合学术检索、主题归类和阅读分组。
- 优先输出英文名词短语。
- 不要输出过泛的词，例如 "paper"、"study"、"research"、"method"。
- 不要重复标题中的整句，不要输出作者名。
- 返回 3 到 5 个关键词。

schema:
{{
  "keywords": ["", "", ""]
}}

metadata:
title: {title}
authors: {authors}
venue: {venue}
year: {year}
doi: {doi}
abstract: {abstract}
""".strip()


def _heuristic_metadata_keywords(metadata: dict) -> list[str]:
    title = " ".join(str(metadata.get("title") or "").split())
    abstract = " ".join(str(metadata.get("abstract") or "").split())
    source = f"{title}. {abstract}".strip()
    if not source:
        return []
    candidates = []
    for part in re.split(r"[:;,.()\\[\\]|/]+", source):
        value = " ".join(part.strip().split())
        if not value:
            continue
        lower = value.lower()
        if len(lower) < 4:
            continue
        if lower in {"paper", "study", "research", "method", "approach", "analysis"}:
            continue
        if 1 <= len(value.split()) <= 6:
            candidates.append(value)
    normalized = _normalize_keyword_list(candidates)
    return normalized[:5]


def generate_keywords_from_metadata(
    metadata: dict,
    *,
    model_id: str = DEFAULT_OPENCLAW_MODEL,
    fallback_model_id: str | None = DEFAULT_OPENCLAW_FALLBACK_MODEL,
    config_path: str | Path = DEFAULT_OPENCLAW_CONFIG_PATH,
) -> list[str]:
    title = " ".join(str(metadata.get("title") or "").split())
    if not title:
        return []

    def _run(target_model_id: str) -> list[str]:
        payload = _request_json_payload(
            model_id=target_model_id,
            messages=[
                {
                    "role": "system",
                    "content": "你负责生成学术检索关键词。只返回严格合法的 JSON，不要输出任何额外说明。",
                },
                {"role": "user", "content": _keyword_prompt(metadata)},
            ],
            config_path=config_path,
            timeout=120,
            attempts=2,
            max_tokens=180,
        )
        return _normalize_keyword_list(payload.get("keywords") or [])

    try:
        keywords = _run(model_id)
        if 3 <= len(keywords) <= 5:
            return keywords
    except Exception:
        pass
    if fallback_model_id:
        try:
            keywords = _run(fallback_model_id)
            if 3 <= len(keywords) <= 5:
                return keywords
        except Exception:
            pass
    return _heuristic_metadata_keywords(metadata)


def _analysis_is_usable(payload: dict) -> bool:
    if not isinstance(payload, dict):
        return False
    required_keys = ("overview", "problem", "method", "results", "critique")
    if any(key not in payload for key in required_keys):
        return False
    return any(_has_meaningful_text(payload.get(key)) for key in required_keys)


def _safe_slug(value: str, fallback: str = "research-topic") -> str:
    chars: list[str] = []
    for ch in (value or "").strip().lower():
        if ch.isalnum():
            chars.append(ch)
        elif chars and chars[-1] != "-":
            chars.append("-")
    slug = "".join(chars).strip("-")
    return (slug[:60] or fallback).strip("-") or fallback


def _apply_research_plan_heuristics(payload: dict) -> dict:
    normalized = dict(payload or {})
    keywords = [item for item in (normalized.get("keywords") or []) if str(item).strip()]
    suggestion = normalized.get("query_suggestion") if isinstance(normalized.get("query_suggestion"), dict) else {}
    suggestion_keywords = []
    for item in (suggestion.get("candidate_keywords") or []) + (suggestion.get("core_concepts") or []):
        value = " ".join(str(item or "").strip().split())
        if value:
            suggestion_keywords.append(value)
    venues = [item for item in (normalized.get("venues") or []) if str(item).strip()]
    theme_text = " ".join(
        [
            str(normalized.get("summary") or ""),
            str(normalized.get("notes") or ""),
            " ".join(str(item) for item in keywords),
        ]
    ).lower()

    filtered_keywords = [item for item in keywords if not _keyword_looks_overly_generic_for_title_search(item)]
    filtered_suggestion_keywords = [
        item for item in suggestion_keywords
        if not _keyword_looks_overly_generic_for_title_search(item)
    ]
    keywords = _merge_keyword_candidates(filtered_keywords, filtered_suggestion_keywords, limit=6)
    normalized["keywords"] = keywords

    audio_theme_markers = ("timbre", "sound", "audio", "music", "synth", "voice interaction")
    if any(marker in theme_text for marker in audio_theme_markers):
        preferred_audio_hci_venues = ["dis", "tei", "iui", "mobilehci", "assets", "chi", "uist", "mm"]
        reordered = []
        seen = set()
        for venue in preferred_audio_hci_venues + venues:
            if venue in seen:
                continue
            seen.add(venue)
            reordered.append(venue)
        normalized["venues"] = reordered[:8]

        keyword_expansions = []
        if "timbre" in theme_text:
            keyword_expansions.extend([
                "timbre exploration interface",
                "timbre interaction design",
                "sound authoring interface",
                "musical timbre interface",
            ])
        if "voice" in theme_text:
            keyword_expansions.append("voice interaction timbre")
        expanded_keywords = []
        seen_keywords = set()
        for keyword in keywords + keyword_expansions:
            lowered = str(keyword).strip().lower()
            if not lowered or lowered in seen_keywords:
                continue
            seen_keywords.add(lowered)
            expanded_keywords.append(" ".join(str(keyword).split()))
        normalized["keywords"] = expanded_keywords[:6]

    if _is_hci_factor_analysis_request(theme_text):
        normalized["keywords"] = _merge_keyword_candidates(
            suggestion.get("candidate_keywords") or [],
            suggestion.get("core_concepts") or [],
            _HCI_FACTOR_GUIDED_KEYWORDS,
            _HCI_FACTOR_GUIDED_CORE_CONCEPTS,
            normalized.get("keywords") or [],
            limit=6,
        )
        preferred_hci_venues = ["chi", "uist", "cscw", "dis", "iui", "mobilehci", "imwut", "ubicomp"]
        normalized["venues"] = _merge_keyword_candidates(preferred_hci_venues, normalized.get("venues") or [], limit=8)

    return normalized


def _prefer_acm_hci_venues_for_create(requirement_text: str, payload: dict) -> dict:
    normalized = dict(payload or {})
    text = " ".join(str(requirement_text or "").lower().split())
    if not text:
        return normalized

    hci_markers = (
        " hci ",
        "human-computer interaction",
        "human computer interaction",
        "人机交互",
    )
    padded = f" {text} "
    if not any(marker in padded for marker in hci_markers):
        return normalized

    explicit_venue_markers = set(ALLOWED_RESEARCH_VENUES) | {"acm", "springer", "ieee"}
    if any(marker in padded for marker in explicit_venue_markers):
        return normalized

    existing_keywords = normalized.get("keywords") or []
    audio_theme = " ".join(str(item) for item in existing_keywords).lower()
    if any(marker in audio_theme for marker in ("timbre", "sound", "audio", "music", "voice")):
        preferred = ["dis", "tei", "iui", "mobilehci", "assets", "chi", "uist", "mm"]
    else:
        preferred = ACM_HCI_VENUES[:8]
    normalized["venues"] = preferred[:8]
    return normalized


def _normalize_research_plan_payload(payload: dict) -> dict:
    keywords = payload.get("keywords") or []
    if isinstance(keywords, str):
        keywords = [part.strip() for part in keywords.split(";") if part.strip()]
    elif isinstance(keywords, list):
        keywords = [" ".join(str(item).strip().split()) for item in keywords if str(item).strip()]
    else:
        keywords = []
    keywords = keywords[:6]

    venues = payload.get("venues") or []
    if isinstance(venues, str):
        venues = [part.strip().lower() for part in venues.replace(";", ",").split(",") if part.strip()]
    elif isinstance(venues, list):
        venues = [str(item).strip().lower() for item in venues if str(item).strip()]
    else:
        venues = []
    allowed = set(ALLOWED_RESEARCH_VENUES)
    deduped_venues = []
    seen = set()
    for venue in venues:
        if venue not in allowed or venue in seen:
            continue
        deduped_venues.append(venue)
        seen.add(venue)
    venues = deduped_venues[:8]

    raw_year_from = payload.get("year_from")
    try:
        year_from = int(str(raw_year_from).strip()) if str(raw_year_from).strip() else 0
    except Exception:
        year_from = 0
    if year_from < 0:
        year_from = 0
    if year_from > 2100:
        year_from = 0

    raw_top = payload.get("top")
    try:
        top = int(str(raw_top).strip()) if str(raw_top).strip() else DEFAULT_RESEARCH_TOP
    except Exception:
        top = DEFAULT_RESEARCH_TOP
    top = min(max(top, 5), 200)

    slug_source = " ".join(
        part for part in [
            str(payload.get("slug") or "").strip(),
            str(payload.get("summary") or "").strip(),
            keywords[0] if keywords else "",
        ] if part
    )
    summary = _short_research_summary(
        str(payload.get("summary") or "").strip(),
        " ".join(keywords),
        str(payload.get("slug") or "").replace("-", " "),
    )

    normalized = {
        "summary": summary,
        "slug": _safe_slug(slug_source or "research-topic"),
        "keywords": keywords,
        "venues": venues,
        "year_from": year_from,
        "top": top,
        "fetch_abstract": bool(payload.get("fetch_abstract", True)),
        "notes": " ".join(str(payload.get("notes") or "").split()),
    }
    suggestion = payload.get("query_suggestion")
    if isinstance(suggestion, dict):
        normalized["query_suggestion"] = _normalize_research_query_suggestion(suggestion)
    return _apply_research_plan_heuristics(normalized)


def _research_plan_is_usable(payload: dict) -> bool:
    if not isinstance(payload, dict):
        return False
    return bool(payload.get("keywords")) and bool(payload.get("slug"))


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
    max_tokens: int | None = None,
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
    if max_tokens is not None:
        payload["max_tokens"] = max(1, int(max_tokens))
    response = _get_model_http_session().post(
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
    max_tokens: int | None = None,
) -> dict:
    last_error = None
    current_messages = list(messages)
    for attempt in range(1, max(attempts, 1) + 1):
        try:
            _acquire_model_request_slot(model_id)
            try:
                content = _request_chat_completion(
                    model_id=model_id,
                    config_path=config_path,
                    temperature=0,
                    messages=current_messages,
                    timeout=timeout,
                    max_tokens=max_tokens,
                )
            finally:
                _release_model_request_slot()
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


def _prepare_image_data_url(
    image_path: str | Path,
    *,
    max_side: int = 1200,
    jpeg_quality: int = 72,
    min_side: int = 512,
    min_quality: int = 35,
    max_base64_chars: int = 260_000,
) -> str:
    path = Path(image_path)
    if not path.exists():
        raise OpenClawIngestError(f"图片不存在: {path}")

    try:
        with Image.open(path) as img:
            img = img.convert("RGB")
            width, height = img.size
            scale = min(1.0, max_side / float(max(width, height)))
            target_width = max(1, int(width * scale))
            target_height = max(1, int(height * scale))
            quality = jpeg_quality

            while True:
                candidate = img.resize((target_width, target_height), Image.Resampling.LANCZOS)
                buffer = io.BytesIO()
                candidate.save(buffer, format="JPEG", quality=quality, optimize=True)
                encoded = base64.b64encode(buffer.getvalue()).decode("ascii")
                if len(encoded) <= max_base64_chars:
                    return f"data:image/jpeg;base64,{encoded}"

                next_width = max(int(target_width * 0.82), min_side if target_width >= target_height else int(min_side * target_width / max(target_height, 1)))
                next_height = max(int(target_height * 0.82), min_side if target_height > target_width else int(min_side * target_height / max(target_width, 1)))
                shrunk = next_width < target_width or next_height < target_height
                lowered = quality > min_quality
                if lowered:
                    quality = max(min_quality, quality - 10)
                if shrunk:
                    target_width, target_height = next_width, next_height
                    continue
                if lowered:
                    continue
                return f"data:image/jpeg;base64,{encoded}"
    except OpenClawIngestError:
        raise
    except Exception as exc:
        raise OpenClawIngestError(f"无法处理图片: {exc}") from exc


def extract_paper_candidate_from_image(
    image_path: str | Path,
    *,
    model_id: str = DEFAULT_OPENCLAW_IMAGE_MODEL,
    fallback_model_id: str | None = DEFAULT_OPENCLAW_IMAGE_FALLBACK_MODEL,
    config_path: str | Path = DEFAULT_OPENCLAW_CONFIG_PATH,
    timeout: int = 180,
) -> dict:
    path = Path(image_path)
    if not path.exists():
        raise OpenClawIngestError(f"图片不存在: {path}")

    data_url = _prepare_image_data_url(path, max_base64_chars=180_000)
    prompt = """
你会看到一张图片。请判断它是否包含以下任一情况：
1. 单篇论文的截图、标题页、摘要页，或足以定位单篇论文的信息
2. Google Scholar 作者主页或论文列表截图，其中列出了多篇论文标题

只返回一个 JSON 对象，不要输出 Markdown、解释或代码块。

返回 schema：
{
  "is_paper_screenshot": true,
  "screenshot_kind": "single_paper",
  "confidence": 0,
  "title": "",
  "titles": ["", ""],
  "doi": "",
  "authors": ["", ""],
  "year": "",
  "venue": "",
  "query": "",
  "notes": ""
}

规则：
- 如果不是论文相关截图，`is_paper_screenshot` 必须为 false。
- 如果是 Google Scholar 论文列表截图，`screenshot_kind` 设为 `scholar_list`，并把可见论文标题写入 `titles`。
- 如果是单篇论文截图，`screenshot_kind` 设为 `single_paper`，优先提取 `title`。
- 如果无法判断类型但明显与论文有关，优先按 `single_paper` 处理。
- `titles` 最多返回 20 条。
- 不要凭空编造标题。
- `doi` 只有在图片中明确出现时才填写。
- `query` 用于后续检索链接，优先等于识别到的英文标题。
- `confidence` 取 0 到 1 之间的小数。
""".strip()

    messages = [
        {"role": "system", "content": "你负责识别图片中的论文信息。只返回严格合法的 JSON。"},
        {
            "role": "user",
            "content": [
                {"type": "text", "text": prompt},
                {"type": "image_url", "image_url": {"url": data_url}},
            ],
        },
    ]

    def _run(target_model_id: str) -> dict:
        payload = _request_json_payload(
            model_id=target_model_id,
            messages=messages,
            config_path=config_path,
            timeout=timeout,
            attempts=2,
            max_tokens=600,
        )
        authors = payload.get("authors") or []
        if not isinstance(authors, list):
            authors = [str(authors or "").strip()] if str(authors or "").strip() else []
        titles = payload.get("titles") or []
        if not isinstance(titles, list):
            titles = [str(titles or "").strip()] if str(titles or "").strip() else []
        normalized_titles = []
        seen = set()
        for item in titles:
            value = " ".join(str(item or "").split()).strip()
            key = value.lower()
            if not value or key in seen:
                continue
            seen.add(key)
            normalized_titles.append(value)
            if len(normalized_titles) >= 20:
                break
        return {
            "is_paper_screenshot": bool(payload.get("is_paper_screenshot")),
            "screenshot_kind": str(payload.get("screenshot_kind") or "single_paper").strip() or "single_paper",
            "confidence": float(payload.get("confidence") or 0),
            "title": str(payload.get("title") or "").strip(),
            "titles": normalized_titles,
            "doi": str(payload.get("doi") or "").strip(),
            "authors": [str(item).strip() for item in authors if str(item).strip()],
            "year": str(payload.get("year") or "").strip(),
            "venue": str(payload.get("venue") or "").strip(),
            "query": str(payload.get("query") or "").strip(),
            "notes": str(payload.get("notes") or "").strip(),
        }

    try:
        return _run(model_id)
    except Exception as exc:
        if not fallback_model_id:
            raise OpenClawIngestError(f"图片论文识别失败: {exc}") from exc
    return _run(fallback_model_id)


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


def _research_plan_prompt(requirement_text: str) -> str:
    allowed_venues = ", ".join(ALLOWED_RESEARCH_VENUES)
    default_hci_venues = ", ".join(ACM_HCI_VENUES)
    return f"""
你是一个论文 research 规划器。用户会用自然语言描述研究方向，你需要把它转换成 exScholar 可直接执行的搜索参数。

只返回一个 JSON 对象，不要输出 Markdown、解释或代码块。

规则：
- `keywords` 是英文关键词组数组，每组用于一次独立标题搜索，优先使用名词短语。
- 关键词要覆盖同义表述，但不要过宽；建议 2 到 4 组。
- `venues` 必须从允许列表里选，最多 6 到 8 个。
- `slug` 只能包含英文小写字母、数字和连字符。
- `year_from` 用四位年份；如果用户没有限制，返回 0。
- `fetch_abstract` 默认 true，除非用户明确只要快速看标题。
- `top` 默认 100；如果用户明显只想快速浏览，可以降到 30 或 50。
- 不要编造用户没表达过的非常具体技术细节。
- 如果用户只是笼统说 HCI / 人机交互，没有明确指定 venue，默认优先收窄到 ACM 收录的 HCI venues。

允许 venues：
{allowed_venues}

默认 ACM HCI venues：
{default_hci_venues}

返回 schema：
{{
  "summary": "",
  "slug": "",
  "keywords": ["", ""],
  "venues": ["chi", "uist"],
  "year_from": 0,
  "top": 100,
  "fetch_abstract": true,
  "notes": ""
}}

用户需求：
{requirement_text}
""".strip()


def _research_plan_prompt_with_suggestion(requirement_text: str, suggestion: dict | None) -> str:
    base = _research_plan_prompt(requirement_text)
    if not isinstance(suggestion, dict) or not (suggestion.get("candidate_keywords") or suggestion.get("core_concepts")):
        return base
    return (
        base
        + "\n\n检索词建议（请尽量参考，但不要机械复制）：\n"
        + json.dumps(suggestion, ensure_ascii=False, indent=2)
    )


def _review_research_results_prompt(requirement_text: str, plan: dict, papers: list[dict]) -> str:
    plan_json = json.dumps(
        {
            "summary": plan.get("summary") or "",
            "keywords": plan.get("keywords") or [],
            "venues": plan.get("venues") or [],
            "year_from": plan.get("year_from") or 0,
        },
        ensure_ascii=False,
        indent=2,
    )
    papers_json = json.dumps(papers, ensure_ascii=False, indent=2)
    return f"""
你是一个学术搜索结果复核器。现在有一批根据研究需求检索回来的论文候选，请你根据标题、venue 和摘要判断它们是否真的相关，并自动生成标签。

只返回一个 JSON 对象，不要输出 Markdown、解释或代码块。

规则：
- `relevance_label` 只能是 `high`、`medium`、`low`。
- `relevance_score` 是 0 到 1 之间的小数。
- `autotags` 输出 0 到 5 个英文标签，优先名词短语。
- 如果论文主题明显偏离需求，即使 venue 合适，也应该给 `low`。
- 如果论文主题接近但不是核心目标，给 `medium`。
- 如果标题和摘要都高度贴合，给 `high`。
- `reason` 用简短中文解释判断依据。

schema:
{{
  "items": [
    {{
      "index": 0,
      "relevance_label": "medium",
      "relevance_score": 0.5,
      "autotags": ["", ""],
      "reason": ""
    }}
  ]
}}

用户需求：
{requirement_text}

搜索方案：
{plan_json}

候选论文：
{papers_json}
""".strip()


def _normalize_research_result_review_item(item: dict) -> dict:
    if not isinstance(item, dict):
        item = {}
    label = str(item.get("relevance_label") or "").strip().lower()
    if label not in {"high", "medium", "low"}:
        label = "medium"
    try:
        score = float(item.get("relevance_score") or 0)
    except Exception:
        score = 0.0
    score = max(0.0, min(score, 1.0))
    return {
        "index": int(item.get("index") or 0),
        "relevance_label": label,
        "relevance_score": score,
        "autotags": _normalize_keyword_list(item.get("autotags") or []),
        "reason": " ".join(str(item.get("reason") or "").split()),
    }


def _heuristic_review_tags(requirement_text: str, plan: dict, paper: dict) -> dict:
    title = " ".join(str(paper.get("title") or "").lower().split())
    abstract = " ".join(str(paper.get("content") or paper.get("abstract") or "").lower().split())
    haystack = f"{title} {abstract}".strip()
    title_text = " ".join(str(paper.get("title") or "").split())
    abstract_text = " ".join(str(paper.get("content") or paper.get("abstract") or "").split())
    tags = []
    for keyword in (plan.get("keywords") or []):
        value = " ".join(str(keyword or "").strip().split())
        low = value.lower()
        if low and low in haystack:
            tags.append(value)
    for token in ("timbre", "sonification", "auditory", "sound design", "musical interface", "voice interaction"):
        if token in haystack:
            tags.append(token.title() if " " in token else token)
    tags.extend(_keyword_phrase_candidates(title_text, max_terms=3))
    if abstract_text:
        tags.extend(_keyword_phrase_candidates(abstract_text[:320], max_terms=3))
    tags = _normalize_keyword_list(tags)
    score = 0.18
    if any(low in haystack for low in [str(k).lower() for k in (plan.get("keywords") or []) if str(k).strip()]):
        score = 0.58
    if "timbre" in haystack or "sonification" in haystack:
        score = max(score, 0.78)
    if "human-robot interaction" in haystack or "social robot" in haystack or "companion robot" in haystack:
        score = max(score, 0.62)
    if score >= 0.75:
        label = "high"
    elif score >= 0.4:
        label = "medium"
    else:
        label = "low"
    return {
        "relevance_label": label,
        "relevance_score": score,
        "autotags": tags[:5],
        "reason": "基于标题和摘要中的关键词重合进行启发式判断。",
    }


def review_research_results(
    requirement_text: str,
    plan: dict,
    papers: list[dict],
    *,
    model_id: str = DEFAULT_OPENCLAW_MODEL,
    fallback_model_id: str | None = DEFAULT_OPENCLAW_FALLBACK_MODEL,
    config_path: str | Path = DEFAULT_OPENCLAW_CONFIG_PATH,
    chunk_size: int = REVIEW_RESEARCH_RESULTS_CHUNK_SIZE,
    progress_callback=None,
) -> list[dict]:
    text = " ".join(str(requirement_text or "").split())
    if not text:
        return papers
    if not isinstance(papers, list) or not papers:
        return papers

    reviewed_records = [dict(item or {}) for item in papers]

    def _run(target_model_id: str, chunk: list[dict]) -> list[dict]:
        payload = _request_json_payload(
            model_id=target_model_id,
            messages=[
                {
                    "role": "system",
                    "content": "你负责复核学术检索结果并输出相关性与自动标签。只返回严格合法的 JSON。",
                },
                {
                    "role": "user",
                    "content": _review_research_results_prompt(text, plan, chunk),
                },
            ],
            config_path=config_path,
            timeout=75,
            attempts=2,
            max_tokens=700,
        )
        items = payload.get("items") or []
        return [_normalize_research_result_review_item(item) for item in items]

    total_chunks = max(1, (len(reviewed_records) + max(1, chunk_size) - 1) // max(1, chunk_size))
    completed_chunks = 0
    for start in range(0, len(reviewed_records), max(1, chunk_size)):
        chunk_records = reviewed_records[start : start + max(1, chunk_size)]
        chunk_payload = []
        for local_index, paper in enumerate(chunk_records):
            chunk_payload.append(
                {
                    "index": local_index,
                    "title": paper.get("title") or "",
                    "venue": paper.get("venue") or "",
                    "year": paper.get("year") or "",
                    "matched_kw": paper.get("matched_kw") or paper.get("_matched_kw") or "",
                    "abstract": (paper.get("content") or paper.get("abstract") or "")[:800],
                }
            )
        chunk_number = completed_chunks + 1
        if callable(progress_callback):
            progress_callback(
                {
                    "stage": "reviewing_results",
                    "completed_chunks": completed_chunks,
                    "total_chunks": total_chunks,
                    "message": f"正在复核第 {chunk_number}/{total_chunks} 批结果，并生成相关性标签。",
                    "papers": reviewed_records,
                }
            )
        review_items = []
        used_fallback = False
        try:
            review_items = _run(model_id, chunk_payload)
        except Exception:
            if fallback_model_id:
                try:
                    review_items = _run(fallback_model_id, chunk_payload)
                except Exception:
                    used_fallback = True
                    review_items = []
            else:
                used_fallback = True
        review_by_index = {item["index"]: item for item in review_items if isinstance(item, dict)}
        for local_index, paper in enumerate(chunk_records):
            review = review_by_index.get(local_index) or _heuristic_review_tags(text, plan, paper)
            paper["relevance_label"] = review.get("relevance_label") or "medium"
            paper["relevance_score"] = review.get("relevance_score") or 0
            paper["autotags"] = review.get("autotags") or []
            paper["review_reason"] = review.get("reason") or ""
        completed_chunks += 1
        if callable(progress_callback):
            progress_callback(
                {
                    "stage": "reviewing_results",
                    "completed_chunks": completed_chunks,
                    "total_chunks": total_chunks,
                    "used_fallback": used_fallback,
                    "message": (
                        f"第 {completed_chunks}/{total_chunks} 批结果复核完成。"
                        + (" 本批已退回启发式标签。" if used_fallback else "")
                    ),
                    "papers": reviewed_records,
                }
            )
        if completed_chunks < total_chunks and REVIEW_BATCH_THROTTLE_SECONDS > 0:
            time.sleep(REVIEW_BATCH_THROTTLE_SECONDS + random.uniform(0.0, min(0.4, REVIEW_BATCH_THROTTLE_SECONDS * 0.5)))

    return reviewed_records


def _research_refine_prompt(requirement_text: str, current_plan: dict, modify_request: str) -> str:
    allowed_venues = ", ".join(ALLOWED_RESEARCH_VENUES)
    current_plan_json = json.dumps(_normalize_research_plan_payload(current_plan or {}), ensure_ascii=False, indent=2)
    return f"""
你是一个论文 research 规划器。现在已经有一份 exScholar 搜索方案，用户又用自然语言提出修改要求。你的任务是基于现有方案进行修改。

只返回一个 JSON 对象，不要输出 Markdown、解释或代码块。

规则：
- 保留用户没有要求改动的合理部分。
- 按修改要求更新 `keywords`、`venues`、`year_from`、`slug`、`top`、`fetch_abstract`。
- `keywords` 仍然应该是英文关键词组数组。
- `venues` 必须从允许列表里选。
- `slug` 只能包含英文小写字母、数字和连字符。
- 不要忽略用户显式提出的限制。
- 如果用户要求缩小范围，就收紧；如果要求扩展，就增加相关同义表述或 venues。

允许 venues：
{allowed_venues}

当前方案：
{current_plan_json}

原始需求：
{requirement_text}

用户修改要求：
{modify_request}

返回 schema：
{{
  "summary": "",
  "slug": "",
  "keywords": ["", ""],
  "venues": ["chi", "uist"],
  "year_from": 0,
  "top": 100,
  "fetch_abstract": true,
  "notes": ""
}}
""".strip()


def _research_compose_prompt(current_prompt: str, current_plan: dict, latest_input: str) -> str:
    allowed_venues = ", ".join(ALLOWED_RESEARCH_VENUES)
    current_plan_json = json.dumps(_normalize_research_plan_payload(current_plan or {}), ensure_ascii=False, indent=2)
    return f"""
你是一个论文 research 规划器。用户当前已经有一份 exScholar 搜索方案，但这次只给了你一段新的输入。
你需要判断这段输入到底是在：

- `create`：表达一个全新的研究需求，应该重新生成方案
- `revise`：在当前方案基础上做补充、删减或约束修改

只返回一个 JSON 对象，不要输出 Markdown、解释或代码块。

规则：
- 如果新输入明显是一个新话题、新研究方向、新领域、新目标，请选择 `create`
- 如果新输入更像是在缩小范围、扩大范围、改 venues、改年份、补关键词、强调排除项，请选择 `revise`
- `plan` 必须是最终可执行的方案
- `prompt` 必须是最终用于代表该方案的 research 需求文本
- `venues` 必须从允许列表里选
- `slug` 只能包含英文小写字母、数字和连字符
- `keywords` 必须是英文关键词组数组

允许 venues：
{allowed_venues}

当前需求：
{current_prompt}

当前方案：
{current_plan_json}

用户这次输入：
{latest_input}

返回 schema：
{{
  "mode": "create",
  "prompt": "",
  "message": "",
  "plan": {{
    "summary": "",
    "slug": "",
    "keywords": ["", ""],
    "venues": ["chi", "uist"],
    "year_from": 0,
    "top": 100,
    "fetch_abstract": true,
    "notes": ""
  }}
}}
""".strip()


def plan_research_request(
    requirement_text: str,
    *,
    model_id: str = DEFAULT_OPENCLAW_MODEL,
    reviewer_model_id: str | None = DEFAULT_OPENCLAW_CHECK_MODEL,
    fallback_model_id: str | None = DEFAULT_OPENCLAW_FALLBACK_MODEL,
    config_path: str | Path = DEFAULT_OPENCLAW_CONFIG_PATH,
    skip_review: bool = False,
    timeout: int = 180,
    attempts: int = 2,
) -> dict:
    text = " ".join(str(requirement_text or "").split())
    if not text:
        raise OpenClawIngestError("研究需求不能为空。")
    query_suggestion = suggest_research_queries(
        text,
        model_id=model_id,
        fallback_model_id=fallback_model_id,
        config_path=config_path,
    )

    def _run_generation(target_model_id: str) -> dict:
        payload = _request_json_payload(
            model_id=target_model_id,
            messages=[
                {
                    "role": "system",
                    "content": "你负责把自然语言研究需求转换为 exScholar 搜索参数。只返回严格合法的 JSON。",
                },
                {"role": "user", "content": _research_plan_prompt_with_suggestion(text, query_suggestion)},
            ],
            config_path=config_path,
            timeout=timeout,
            attempts=attempts,
        )
        normalized = _normalize_research_plan_payload(payload)
        normalized = _prefer_acm_hci_venues_for_create(text, normalized)
        normalized["query_suggestion"] = query_suggestion
        normalized["diagnostics"] = {
            "plan_generation_mode": target_model_id,
            "query_suggestion_mode": query_suggestion.get("generation_mode") or "",
            "model_http": model_http_transport_mode(),
        }
        return normalized

    primary_error = None
    try:
        primary = _run_generation(model_id)
        if _research_plan_is_usable(primary):
            if skip_review or not reviewer_model_id:
                return primary
            review = _review_candidate(
                task_name="论文 research 搜索方案",
                source_text=text,
                candidate_payload=primary,
                criteria_text="必须给出可执行的 keywords 和 slug。venues 只能来自允许列表，year_from 应该合理，不能过度臆测用户意图。",
                review_model_id=reviewer_model_id,
                config_path=config_path,
            )
            corrected = review.get("corrected_json")
            if review.get("pass"):
                if isinstance(corrected, dict):
                    normalized = _normalize_research_plan_payload(corrected)
                    if _research_plan_is_usable(normalized):
                        normalized["query_suggestion"] = query_suggestion
                        normalized["diagnostics"] = {
                            "plan_generation_mode": "reviewer_corrected",
                            "query_suggestion_mode": query_suggestion.get("generation_mode") or "",
                            "model_http": model_http_transport_mode(),
                        }
                        return normalized
                return primary
        else:
            review = {"pass": False, "issues": ["主模型没有生成可执行的搜索方案。"], "reason": "", "corrected_json": None}
    except Exception as exc:
        primary_error = exc
        review = {"pass": False, "issues": [str(exc)], "reason": "主模型规划失败。", "corrected_json": None}

    if not fallback_model_id:
        detail = "; ".join(str(item) for item in review.get("issues") or [] if str(item).strip())
        if primary_error:
            heuristic_plan = _heuristic_research_plan_from_suggestion(text, query_suggestion)
            if _research_plan_is_usable(heuristic_plan):
                heuristic_plan["diagnostics"] = {
                    "plan_generation_mode": "heuristic",
                    "query_suggestion_mode": query_suggestion.get("generation_mode") or "",
                    "fallback_reason": detail or str(primary_error),
                    "model_http": model_http_transport_mode(),
                }
                return heuristic_plan
            raise OpenClawIngestError(f"research 方案生成失败: {detail or str(primary_error)}")
        raise OpenClawIngestError(f"research 方案未通过检查: {detail or '结果不符合要求'}")

    try:
        fallback = _run_generation(fallback_model_id)
        if _research_plan_is_usable(fallback):
            return fallback
    except Exception:
        pass
    heuristic_plan = _heuristic_research_plan_from_suggestion(text, query_suggestion)
    if _research_plan_is_usable(heuristic_plan):
        heuristic_plan["diagnostics"] = {
            "plan_generation_mode": "heuristic",
            "query_suggestion_mode": query_suggestion.get("generation_mode") or "",
            "fallback_reason": "primary_and_fallback_model_unavailable_or_invalid",
            "model_http": model_http_transport_mode(),
        }
        return heuristic_plan
    raise OpenClawIngestError("模型回退后仍未得到可执行的 research 方案。")


def refine_research_plan(
    requirement_text: str,
    current_plan: dict,
    modify_request: str,
    *,
    model_id: str = DEFAULT_OPENCLAW_MODEL,
    reviewer_model_id: str | None = DEFAULT_OPENCLAW_CHECK_MODEL,
    fallback_model_id: str | None = DEFAULT_OPENCLAW_FALLBACK_MODEL,
    config_path: str | Path = DEFAULT_OPENCLAW_CONFIG_PATH,
) -> dict:
    requirement = " ".join(str(requirement_text or "").split())
    modify = " ".join(str(modify_request or "").split())
    if not requirement:
        raise OpenClawIngestError("原始 research 需求不能为空。")
    if not isinstance(current_plan, dict) or not _research_plan_is_usable(_normalize_research_plan_payload(current_plan)):
        raise OpenClawIngestError("当前方案不可用，无法执行自然语言修改。")
    if not modify:
        raise OpenClawIngestError("修改要求不能为空。")

    def _run_generation(target_model_id: str) -> dict:
        payload = _request_json_payload(
            model_id=target_model_id,
            messages=[
                {
                    "role": "system",
                    "content": "你负责根据自然语言修改要求更新 exScholar 搜索方案。只返回严格合法的 JSON。",
                },
                {"role": "user", "content": _research_refine_prompt(requirement, current_plan, modify)},
            ],
            config_path=config_path,
            timeout=180,
            attempts=2,
        )
        return _normalize_research_plan_payload(payload)

    primary_error = None
    try:
        primary = _run_generation(model_id)
        if _research_plan_is_usable(primary):
            review = _review_candidate(
                task_name="更新后的论文 research 搜索方案",
                source_text=f"原始需求：{requirement}\n修改要求：{modify}",
                candidate_payload=primary,
                criteria_text="必须尊重用户的修改要求，且给出可执行的 keywords 和 slug。venues 只能来自允许列表。",
                review_model_id=reviewer_model_id,
                config_path=config_path,
            )
            corrected = review.get("corrected_json")
            if review.get("pass"):
                if isinstance(corrected, dict):
                    normalized = _normalize_research_plan_payload(corrected)
                    if _research_plan_is_usable(normalized):
                        return normalized
                return primary
        else:
            review = {"pass": False, "issues": ["主模型没有生成可执行的更新方案。"], "reason": "", "corrected_json": None}
    except Exception as exc:
        primary_error = exc
        review = {"pass": False, "issues": [str(exc)], "reason": "主模型更新方案失败。", "corrected_json": None}

    if not fallback_model_id:
        detail = "; ".join(str(item) for item in review.get("issues") or [] if str(item).strip())
        if primary_error:
            raise OpenClawIngestError(f"research 方案修改失败: {detail or str(primary_error)}")
        raise OpenClawIngestError(f"research 方案修改未通过检查: {detail or '结果不符合要求'}")

    fallback = _run_generation(fallback_model_id)
    if not _research_plan_is_usable(fallback):
        raise OpenClawIngestError("Kimi 回退后仍未得到可执行的更新方案。")
    return fallback


def validate_research_plan(
    requirement_text: str,
    current_plan: dict,
    *,
    reviewer_model_id: str | None = DEFAULT_OPENCLAW_CHECK_MODEL,
    fallback_model_id: str | None = DEFAULT_OPENCLAW_FALLBACK_MODEL,
    config_path: str | Path = DEFAULT_OPENCLAW_CONFIG_PATH,
) -> dict:
    requirement = " ".join(str(requirement_text or "").split())
    normalized = _normalize_research_plan_payload(current_plan or {})
    if not requirement:
        raise OpenClawIngestError("原始 research 需求不能为空。")
    if not _research_plan_is_usable(normalized):
        raise OpenClawIngestError("当前方案不可用，无法验证。")

    review = _review_candidate(
        task_name="手动编辑后的论文 research 搜索方案",
        source_text=requirement,
        candidate_payload=normalized,
        criteria_text="检查这份手工编辑方案是否仍然可执行、是否符合原始需求方向、venues 是否在允许列表中、slug 是否合法、keywords 是否适合标题检索。如有必要可直接修正。",
        review_model_id=reviewer_model_id,
        config_path=config_path,
    )
    corrected = review.get("corrected_json")
    if isinstance(corrected, dict):
        normalized_corrected = _normalize_research_plan_payload(corrected)
        if _research_plan_is_usable(normalized_corrected):
            return normalized_corrected
    if review.get("pass"):
        return normalized

    if not fallback_model_id:
        detail = "; ".join(str(item) for item in review.get("issues") or [] if str(item).strip())
        raise OpenClawIngestError(f"research 方案验证未通过: {detail or '结果不符合要求'}")

    fallback = refine_research_plan(
        requirement,
        normalized,
        "请在尽量保留当前手工编辑意图的前提下，把这份方案修正为可执行且合理的 exScholar 搜索方案。",
        model_id=fallback_model_id,
        reviewer_model_id=reviewer_model_id,
        fallback_model_id=None,
        config_path=config_path,
    )
    if not _research_plan_is_usable(fallback):
        raise OpenClawIngestError("模型验证后仍未得到可执行的方案。")
    return fallback


def compose_research_plan(
    latest_input: str,
    *,
    current_prompt: str = "",
    current_plan: dict | None = None,
    model_id: str = DEFAULT_OPENCLAW_MODEL,
    reviewer_model_id: str | None = DEFAULT_OPENCLAW_CHECK_MODEL,
    fallback_model_id: str | None = DEFAULT_OPENCLAW_FALLBACK_MODEL,
    config_path: str | Path = DEFAULT_OPENCLAW_CONFIG_PATH,
    fast_preview: bool = False,
) -> dict:
    latest = " ".join(str(latest_input or "").split())
    current_prompt = " ".join(str(current_prompt or "").split())
    normalized_current_plan = _normalize_research_plan_payload(current_plan or {}) if current_plan else {}

    if not latest:
        raise OpenClawIngestError("research 输入不能为空。")

    if not normalized_current_plan or not _research_plan_is_usable(normalized_current_plan) or not current_prompt:
        plan = plan_research_request(
            latest,
            model_id=model_id,
            reviewer_model_id=reviewer_model_id,
            fallback_model_id=fallback_model_id,
            config_path=config_path,
            skip_review=fast_preview,
            timeout=60 if fast_preview else 180,
            attempts=1 if fast_preview else 2,
        )
        return {
            "mode": "create",
            "prompt": latest,
            "message": "已根据你的需求生成一份搜索方案草案。" if fast_preview else "已根据新的 research 需求生成方案。",
            "plan": plan,
            "diagnostics": plan.get("diagnostics") or {},
        }

    def _run_generation(target_model_id: str) -> dict:
        return _request_json_payload(
            model_id=target_model_id,
            messages=[
                {
                    "role": "system",
                    "content": "你负责判断用户输入是在创建新 research 方案还是修改当前方案，并输出最终可执行方案。只返回严格合法的 JSON。",
                },
                {
                    "role": "user",
                    "content": _research_compose_prompt(current_prompt, normalized_current_plan, latest),
                },
            ],
            config_path=config_path,
            timeout=60 if fast_preview else 180,
            attempts=1 if fast_preview else 2,
        )

    raw = None
    try:
        raw = _run_generation(model_id)
    except Exception:
        if not fallback_model_id:
            return {
                "mode": "create",
                "prompt": latest,
                "message": "上游模型暂时不可用，已根据智能建议检索词生成本地方案草案。",
                "plan": _heuristic_research_plan_from_suggestion(latest, suggest_research_queries(
                    latest,
                    model_id=model_id,
                    fallback_model_id=fallback_model_id,
                    config_path=config_path,
                )),
            }
        try:
            raw = _run_generation(fallback_model_id)
        except Exception:
            return {
                "mode": "create",
                "prompt": latest,
                "message": "上游模型暂时不可用，已根据智能建议检索词生成本地方案草案。",
                "plan": _heuristic_research_plan_from_suggestion(latest, suggest_research_queries(
                    latest,
                    model_id=model_id,
                    fallback_model_id=fallback_model_id,
                    config_path=config_path,
                )),
            }

    mode = str(raw.get("mode") or "").strip().lower()
    if mode not in {"create", "revise"}:
        mode = "revise"
    prompt = " ".join(str(raw.get("prompt") or "").split()) or (latest if mode == "create" else current_prompt)
    message = " ".join(str(raw.get("message") or "").split())
    candidate_plan = _normalize_research_plan_payload(raw.get("plan") or {})
    if fast_preview:
        if _research_plan_is_usable(candidate_plan):
            validated_plan = candidate_plan
        elif fallback_model_id:
            validated_plan = plan_research_request(
                latest if mode == "create" else prompt,
                model_id=fallback_model_id,
                reviewer_model_id=None,
                fallback_model_id=None,
                config_path=config_path,
                skip_review=True,
                timeout=60,
                attempts=1,
            )
        else:
            raise OpenClawIngestError("生成方案草案失败，请重试。")
    else:
        validated_plan = validate_research_plan(
            prompt,
            candidate_plan,
            reviewer_model_id=reviewer_model_id,
            fallback_model_id=fallback_model_id,
            config_path=config_path,
        )
    return {
        "mode": mode,
        "prompt": prompt,
        "message": message or (
            "已重新生成方案草案。" if fast_preview and mode == "create"
            else "已更新当前方案草案。" if fast_preview
            else "已重新生成方案。" if mode == "create"
            else "已基于当前方案更新。"
        ),
        "plan": validated_plan,
        "diagnostics": validated_plan.get("diagnostics") or {},
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
  "abstract": "",
  "keywords": ["", "", ""]
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
