"""OpenClaw integration package for exScholar."""

from .ingest import (
    DEFAULT_OPENCLAW_CHECK_MODEL,
    DEFAULT_OPENCLAW_CONFIG_PATH,
    DEFAULT_OPENCLAW_FALLBACK_MODEL,
    DEFAULT_OPENCLAW_MODEL,
    OpenClawIngestError,
    answer_question_from_text,
    extract_metadata_from_text,
    extract_pdf_bundle,
    generate_analysis_from_text,
    resolve_openclaw_model,
)

__all__ = [
    "DEFAULT_OPENCLAW_CONFIG_PATH",
    "answer_question_from_text",
    "DEFAULT_OPENCLAW_CHECK_MODEL",
    "DEFAULT_OPENCLAW_FALLBACK_MODEL",
    "DEFAULT_OPENCLAW_MODEL",
    "OpenClawIngestError",
    "extract_metadata_from_text",
    "extract_pdf_bundle",
    "generate_analysis_from_text",
    "resolve_openclaw_model",
]
