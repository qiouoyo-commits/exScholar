"""Search discovery, filesystem, JSON, and utility helpers for exScholar."""

from app.common import normalize_title as common_normalize_title
from app.common import title_similarity as common_title_similarity

from .base import *


def ensure_db():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    LIBRARY_DIR.mkdir(parents=True, exist_ok=True)
    READING_DIR.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS citations (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                title TEXT NOT NULL,
                doi TEXT,
                url TEXT,
                authors TEXT,
                year TEXT,
                venue TEXT,
                abstract TEXT,
                matched_kw TEXT,
                tags TEXT DEFAULT '',
                source_search_slug TEXT,
                source_csv_index INTEGER,
                pdf_path TEXT,
                pdf_sha256 TEXT,
                reading_paper_id TEXT,
                created_at TEXT NOT NULL,
                UNIQUE(doi),
                UNIQUE(title, year)
            )
            """
        )
        columns = {row[1] for row in conn.execute("PRAGMA table_info(citations)").fetchall()}
        if "tags" not in columns:
            conn.execute("ALTER TABLE citations ADD COLUMN tags TEXT DEFAULT ''")
        if "pdf_path" not in columns:
            conn.execute("ALTER TABLE citations ADD COLUMN pdf_path TEXT")
        if "pdf_sha256" not in columns:
            conn.execute("ALTER TABLE citations ADD COLUMN pdf_sha256 TEXT")
        if "reading_paper_id" not in columns:
            conn.execute("ALTER TABLE citations ADD COLUMN reading_paper_id TEXT")
        conn.execute(
            """
            UPDATE citations
            SET tags = matched_kw
            WHERE (tags IS NULL OR tags = '')
              AND matched_kw IS NOT NULL
              AND matched_kw != ''
            """
        )
        rows = conn.execute(
            """
            SELECT id, pdf_path
            FROM citations
            WHERE pdf_path IS NOT NULL
              AND pdf_path != ''
              AND (pdf_sha256 IS NULL OR pdf_sha256 = '')
            """
        ).fetchall()
        for citation_id, pdf_path in rows:
            pdf_abs = DATA_DIR / pdf_path
            if not pdf_abs.exists():
                continue
            conn.execute(
                "UPDATE citations SET pdf_sha256 = ? WHERE id = ?",
                (compute_file_sha256(pdf_abs), citation_id),
            )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS reading_groups (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL UNIQUE,
                description TEXT,
                source_kind TEXT NOT NULL DEFAULT 'manual',
                created_at TEXT NOT NULL
            )
            """
        )
        group_columns = {row[1] for row in conn.execute("PRAGMA table_info(reading_groups)").fetchall()}
        if "source_kind" not in group_columns:
            conn.execute("ALTER TABLE reading_groups ADD COLUMN source_kind TEXT NOT NULL DEFAULT 'manual'")
        conn.execute(
            """
            UPDATE reading_groups
            SET source_kind = CASE
                WHEN description LIKE '自动为搜索%' THEN 'auto'
                ELSE 'manual'
            END
            WHERE source_kind IS NULL
               OR source_kind = ''
               OR source_kind NOT IN ('manual', 'auto')
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS citation_group_links (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                citation_id INTEGER NOT NULL,
                group_id INTEGER NOT NULL,
                created_at TEXT NOT NULL,
                UNIQUE(citation_id, group_id),
                FOREIGN KEY (citation_id) REFERENCES citations(id) ON DELETE CASCADE,
                FOREIGN KEY (group_id) REFERENCES reading_groups(id) ON DELETE CASCADE
            )
            """
        )
        conn.commit()


def iter_result_dirs():
    for base_dir in (SEARCHES_DIR, EXPANSIONS_DIR):
        if not base_dir.exists():
            continue
        for out_dir in sorted(base_dir.iterdir(), reverse=True):
            if out_dir.is_dir():
                yield out_dir


def iter_search_dirs():
    if not SEARCHES_DIR.exists():
        return
    for out_dir in sorted(SEARCHES_DIR.iterdir(), reverse=True):
        if out_dir.is_dir():
            yield out_dir


def find_result_dir_by_slug(slug: str) -> Path | None:
    slug = (slug or "").strip()
    if not slug:
        return None
    for out_dir in iter_result_dirs():
        search_json = out_dir / "search.json"
        if not search_json.exists():
            continue
        try:
            meta = json.loads(search_json.read_text(encoding="utf-8"))
        except Exception:
            continue
        if (meta.get("slug") or out_dir.name.split("_", 1)[-1]) == slug:
            return out_dir
        if out_dir.name == slug:
            return out_dir
    return None


def utc_now() -> str:
    return datetime.utcnow().isoformat(timespec="seconds") + "Z"


def public_site_base() -> str:
    base_url = PUBLIC_SITE_BASE_URL
    if base_url:
        return base_url.rstrip("/")
    host = HOST if HOST not in ("0.0.0.0", "::") else "127.0.0.1"
    return f"http://{host}:{PORT}"


def build_public_url(path: str) -> str:
    path_text = "/" + str(path or "").lstrip("/")
    return f"{public_site_base()}{path_text}"


def safe_file_stem(name: str, fallback: str = "paper") -> str:
    chars = []
    for ch in (name or "").strip():
        if ch.isalnum():
            chars.append(ch.lower())
        elif chars and chars[-1] != "-":
            chars.append("-")
    value = "".join(chars).strip("-")
    return value[:80] or fallback


def compute_stream_sha256(stream) -> str:
    stream.seek(0)
    digest = hashlib.sha256()
    while True:
        chunk = stream.read(1024 * 1024)
        if not chunk:
            break
        digest.update(chunk)
    stream.seek(0)
    return digest.hexdigest()


def compute_file_sha256(path: Path) -> str:
    with path.open("rb") as fh:
        return compute_stream_sha256(fh)


def build_reading_links(paper_id: str) -> dict:
    reading_home = "/reading"
    reading_url = f"/reading/{paper_id}"
    return {
        "reading_home": reading_home,
        "reading_home_absolute": build_public_url(reading_home),
        "paper_page": reading_url,
        "paper_page_absolute": build_public_url(reading_url),
    }


def build_reading_paper_id(citation: dict) -> str:
    base = safe_file_stem(citation.get("title") or "", fallback="paper")
    return f"paper_{datetime.utcnow().strftime('%Y%m%d_%H%M%S')}_{secrets.token_hex(3)}_{base[:24]}"


def reading_workspace_path(paper_id: str) -> Path:
    return READING_DIR / paper_id


def reading_qa_history_path(paper_id: str) -> Path:
    return reading_workspace_path(paper_id) / "qa_history.json"


def reading_notes_path(paper_id: str) -> Path:
    return reading_workspace_path(paper_id) / "notes.json"


def normalize_notes_payload(payload) -> dict:
    if isinstance(payload, dict):
        return {str(key): str(value or "") for key, value in payload.items()}
    return {}


def default_analysis_payload(paper_id: str) -> dict:
    now = utc_now()
    empty_module = {"status": "pending", "version": 0, "generated_at": None, "data": {}}
    return {
        "paper_id": paper_id,
        "schema_version": "1.0.0",
        "analysis_version": 1,
        "pipeline_mode": "1_call",
        "modules": {
            "overview": dict(empty_module),
            "problem": dict(empty_module),
            "method": dict(empty_module),
            "results": dict(empty_module),
            "critique": dict(empty_module),
        },
        "calls": [
            {
                "call_id": "call_01",
                "module": "all_modules",
                "status": "pending",
                "started_at": None,
                "ended_at": None,
                "model": None,
                "input_scope": ["full_pdf"],
                "output_version": 0,
            }
        ],
        "updated_at": now,
    }


def split_authors(authors_text: str) -> list[str]:
    parts = []
    for item in (authors_text or "").replace(";", ",").split(","):
        name = " ".join(item.strip().split())
        if name:
            parts.append(name)
    return parts


def normalize_title_for_match(title: str) -> str:
    return common_normalize_title(title)


def title_similarity(a: str, b: str) -> float:
    return common_title_similarity(a, b)


def read_json_file(path: Path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


def sanitize_utf8_surrogates(value):
    if isinstance(value, str):
        if any(0xD800 <= ord(ch) <= 0xDFFF for ch in value):
            return "".join("\uFFFD" if 0xD800 <= ord(ch) <= 0xDFFF else ch for ch in value)
        return value
    if isinstance(value, list):
        return [sanitize_utf8_surrogates(item) for item in value]
    if isinstance(value, tuple):
        return [sanitize_utf8_surrogates(item) for item in value]
    if isinstance(value, dict):
        return {
            sanitize_utf8_surrogates(key) if isinstance(key, str) else key: sanitize_utf8_surrogates(item)
            for key, item in value.items()
        }
    return value


def write_json_file(path: Path, payload):
    path.parent.mkdir(parents=True, exist_ok=True)
    cleaned = sanitize_utf8_surrogates(payload)
    path.write_text(json.dumps(cleaned, ensure_ascii=False, indent=2), encoding="utf-8")


def write_json_file_atomic(path: Path, payload):
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = path.with_suffix(path.suffix + ".tmp")
    cleaned = sanitize_utf8_surrogates(payload)
    temp_path.write_text(json.dumps(cleaned, ensure_ascii=False, indent=2), encoding="utf-8")
    temp_path.replace(path)
