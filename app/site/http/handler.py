"""HTTP handlers and server entrypoint for the exScholar site."""

from dataclasses import dataclass
from email.parser import BytesParser
from email.policy import default
from urllib.parse import parse_qs, urlsplit

from ..core import *
from ..ui.pages import *


def _load_search_meta_for_slug(search_slug: str) -> dict:
    normalized = " ".join(str(search_slug or "").split()).strip().strip("/")
    if not normalized:
        return {}
    searches_dir = Path(current_searches_dir())
    direct = searches_dir / normalized / "search.json"
    if direct.exists():
        return read_json_file(direct, {}) or {}
    matches: list[tuple[float, dict]] = []
    for meta_path in searches_dir.glob("*/search.json"):
        payload = read_json_file(meta_path, {}) or {}
        if payload.get("output_slug") == normalized or payload.get("slug") == normalized:
            try:
                score = meta_path.stat().st_mtime
            except Exception:
                score = 0.0
            matches.append((score, payload))
    if not matches:
        return {}
    matches.sort(key=lambda item: item[0], reverse=True)
    return matches[0][1]


def _resolve_search_group_name(search_slug: str, paper: dict | None = None) -> str:
    meta = _load_search_meta_for_slug(search_slug)
    source_slug = " ".join(str(meta.get("slug") or "").split())
    source_keywords = [str(item or "").strip().lower() for item in (meta.get("keywords") or []) if str(item or "").strip()]
    if source_slug in {PICSEARCH_SLUG, TEXTSEARCH_SLUG} or any(item in {PICSEARCH_KEYWORD, TEXTSEARCH_KEYWORD} for item in source_keywords):
        derived = suggest_lookup_group_name(paper or {}, keyword=(paper or {}).get("matched_kw") or (source_keywords[0] if source_keywords else ""))
        if derived:
            return derived
    if meta:
        name = " ".join(str(meta.get("group_name") or meta.get("summary_name") or "").split())
        if name:
            return name
    fallback = build_search_summary_name(
        (paper or {}).get("matched_kw") or "",
        (paper or {}).get("title") or "",
        str(search_slug or "").replace("-", " "),
    )
    return fallback


@dataclass
class UploadedFormFile:
    name: str
    filename: str
    type: str
    file: io.BytesIO


class SearchSiteHandler(SimpleHTTPRequestHandler):
    _ALLOWED_STATIC_SEARCH_EXTS = {".html", ".csv", ".json"}
    _ALLOWED_STATIC_BINARY_EXTS = {".pdf"}

    # Request parsing and response helpers
    def _normalize_request_target_inplace(self) -> None:
        raw = str(getattr(self, "path", "") or "")
        if not raw:
            return
        if raw.startswith(("http://", "https://")):
            parsed = urlsplit(raw)
            normalized = parsed.path or "/"
            if parsed.query:
                normalized = f"{normalized}?{parsed.query}"
            self.path = normalized

    def is_openclaw_public_api(self) -> bool:
        path = self.path.split("?", 1)[0].split("#", 1)[0]
        return (
            path == "/api/openclaw-intake/upload"
            or path == "/api/openclaw-image-intake/upload"
            or path == "/api/openclaw-intake/jobs"
            or path == "/api/openclaw-image-intake/jobs"
            or path.startswith("/api/openclaw-intake/jobs/")
            or path.startswith("/api/openclaw-image-intake/jobs/")
        )

    def _resolve_data_request_path(self, path: str) -> Path | None:
        normalized = unquote(path.split("?", 1)[0].split("#", 1)[0]).strip()
        cleaned = normalized.lstrip("/")
        candidate = (DATA_DIR / cleaned).resolve()
        try:
            candidate.relative_to(DATA_DIR.resolve())
        except Exception:
            return None
        return candidate

    def _is_allowed_static_data_path(self, path: Path) -> bool:
        try:
            relative = path.relative_to(DATA_DIR.resolve())
        except Exception:
            return False
        parts = relative.parts
        if not parts:
            return False
        suffix = path.suffix.lower()
        if path.name.endswith(".sqlite3"):
            return False

        searches_root = Path(current_searches_dir()).resolve()
        expansions_root = Path(current_expansions_dir()).resolve()
        library_root = Path(current_library_dir()).resolve()
        reading_root = Path(current_reading_dir()).resolve()

        try:
            rel = path.relative_to(searches_root)
            if path.is_dir():
                return "site" in rel.parts
            return suffix in self._ALLOWED_STATIC_SEARCH_EXTS
        except Exception:
            pass
        try:
            rel = path.relative_to(expansions_root)
            if path.is_dir():
                return "site" in rel.parts
            return suffix in self._ALLOWED_STATIC_SEARCH_EXTS
        except Exception:
            pass
        try:
            path.relative_to(library_root)
            return suffix in self._ALLOWED_STATIC_BINARY_EXTS
        except Exception:
            pass
        try:
            rel = path.relative_to(reading_root)
            return suffix in self._ALLOWED_STATIC_BINARY_EXTS and "source" in rel.parts
        except Exception:
            pass
        return False

    def translate_path(self, path):
        resolved = self._resolve_data_request_path(path)
        if not resolved:
            return str((DATA_DIR / "__forbidden__").resolve())
        return str(resolved)

    @staticmethod
    def _append_form_value(data: dict, name: str, value):
        if name in data:
            current = data[name]
            if isinstance(current, list):
                current.append(value)
            else:
                data[name] = [current, value]
        else:
            data[name] = value

    def _parse_multipart_form_data(self, raw: bytes, content_type: str) -> dict:
        header = f"Content-Type: {content_type}\r\nMIME-Version: 1.0\r\n\r\n".encode("utf-8")
        message = BytesParser(policy=default).parsebytes(header + raw)
        data: dict[str, object] = {}
        if not message.is_multipart():
            return data
        for part in message.iter_parts():
            if part.get_content_disposition() != "form-data":
                continue
            field_name = part.get_param("name", header="content-disposition") or ""
            if not field_name:
                continue
            filename = part.get_filename()
            payload = part.get_payload(decode=True) or b""
            if filename:
                value = UploadedFormFile(
                    name=field_name,
                    filename=filename,
                    type=part.get_content_type() or "",
                    file=io.BytesIO(payload),
                )
            else:
                charset = part.get_content_charset() or "utf-8"
                value = payload.decode(charset, errors="replace")
            self._append_form_value(data, field_name, value)
        return data

    def parse_body(self):
        length = int(self.headers.get("Content-Length", "0") or "0")
        raw = self.rfile.read(length) if length else b""
        content_type = self.headers.get("Content-Type", "")
        if "multipart/form-data" in content_type:
            return self._parse_multipart_form_data(raw, content_type)
        if "application/json" in content_type:
            return json.loads(raw.decode("utf-8") or "{}")
        if "application/x-www-form-urlencoded" in content_type:
            parsed = parse_qs(raw.decode("utf-8"), keep_blank_values=True)
            return {key: values[0] if len(values) == 1 else values for key, values in parsed.items()}
        return {}

    def send_json(self, payload: dict, status: int = 200, extra_headers: dict | None = None):
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("Pragma", "no-cache")
        if extra_headers:
            for key, value in extra_headers.items():
                self.send_header(key, value)
        self.end_headers()
        self.wfile.write(data)

    def send_html(self, html: str, status: int = 200, extra_headers: dict | None = None):
        data = html.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("Pragma", "no-cache")
        if extra_headers:
            for key, value in extra_headers.items():
                self.send_header(key, value)
        self.end_headers()
        self.wfile.write(data)

    def current_session(self):
        if not require_password():
            return {"id": "no-password", "created_at": time.time(), "username": ""}
        cookie_header = self.headers.get("Cookie", "")
        jar = cookies.SimpleCookie()
        jar.load(cookie_header)
        token = jar.get(SESSION_COOKIE)
        if not token:
            return None
        return get_session(token.value)

    def current_username(self) -> str:
        session = self.current_session() or {}
        return sanitize_username(session.get("username") or "")

    def is_authenticated(self) -> bool:
        return self.current_session() is not None

    def reject_unauthorized(self):
        if self.path.startswith("/api/"):
            self.send_json({"ok": False, "error": "unauthorized"}, status=401)
        else:
            self.send_html(build_login_html(has_users=has_users() or bool(PASSWORD_SALT and PASSWORD_HASH)), status=200)

    def handle_server_error(self, exc: Exception):
        error_log = Path("/tmp/exscholar-serve-errors.log")
        message = (
            f"[{utc_now()}] {self.command} {self.path}\n"
            f"{traceback.format_exc()}\n"
        )
        try:
            error_log.parent.mkdir(parents=True, exist_ok=True)
            with error_log.open("a", encoding="utf-8") as fh:
                fh.write(message)
        except Exception:
            pass
        if self.path.startswith("/api/"):
            self.send_json({"ok": False, "error": f"server_error: {exc}"}, status=500)
        else:
            self.send_html("<!doctype html><html lang='zh-CN'><body><h1>Server Error</h1></body></html>", status=500)

    def _handle_auth_post(self) -> bool:
        if self.path == "/api/auth/login":
            data = self.parse_body()
            username = data.get("username", "")
            password = data.get("password", "")
            user = authenticate_user(username, password)
            if not user:
                if "application/json" in self.headers.get("Content-Type", ""):
                    self.send_json({"ok": False, "error": "invalid_credentials"}, status=403)
                else:
                    self.send_html(build_login_html("用户名或密码错误，请重试。"), status=403)
                return True

            token, _session = create_session(user["username"])
            headers = {
                "Set-Cookie": f"{SESSION_COOKIE}={token}; Path=/; HttpOnly; SameSite=Lax",
            }
            if "application/json" in self.headers.get("Content-Type", ""):
                self.send_json({"ok": True, "username": user["username"]}, extra_headers=headers)
            else:
                body = (
                    "<!doctype html><html lang='zh-CN'><head>"
                    "<meta charset='utf-8'>"
                    "<meta http-equiv='refresh' content='0;url=/'>"
                    "<title>Redirecting</title>"
                    "</head><body>"
                    "<p>登录成功，正在进入首页。</p>"
                    "<p><a href='/'>如果没有自动跳转，请点这里。</a></p>"
                    "<script>window.location.replace('/');</script>"
                    "</body></html>"
                ).encode("utf-8")
                self.send_response(303)
                self.send_header("Location", "/")
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                for key, value in headers.items():
                    self.send_header(key, value)
                self.end_headers()
                self.wfile.write(body)
            return True

        if self.path == "/api/auth/logout":
            jar = cookies.SimpleCookie()
            jar.load(self.headers.get("Cookie", ""))
            token = jar.get(SESSION_COOKIE)
            if token:
                delete_session(token.value)
            self.send_json(
                {"ok": True},
                extra_headers={"Set-Cookie": f"{SESSION_COOKIE}=; Path=/; Max-Age=0; HttpOnly; SameSite=Lax"},
            )
            return True

        return False

    def _handle_citation_post(self) -> bool:
        if self.path == "/api/citations":
            data = self.parse_body()
            paper = data.get("paper") or {}
            if isinstance(paper, str):
                try:
                    paper = json.loads(paper)
                except Exception:
                    paper = {}
            search_slug = (data.get("search_slug") or "").strip()
            group_id_raw = data.get("group_id")
            if not paper.get("title"):
                self.send_json({"ok": False, "error": "missing_title"}, status=400)
                return True
            try:
                pdf_record = store_uploaded_pdf(data.get("pdf"), paper.get("title") or "")
            except ValueError as exc:
                self.send_json({"ok": False, "error": str(exc)}, status=400)
                return True
            pdf_path = pdf_record.get("pdf_path") or ""
            pdf_sha256 = pdf_record.get("pdf_sha256") or ""
            citation_id = upsert_citation(
                paper,
                search_slug,
                pdf_path=pdf_path or None,
                pdf_sha256=pdf_sha256 or None,
            )
            citation = get_citation_by_id(citation_id) if citation_id else None
            if citation and not pdf_path:
                existing_pdf_path = (citation.get("pdf_path") or "").strip()
                if existing_pdf_path:
                    pdf_path = existing_pdf_path
                    pdf_sha256 = (citation.get("pdf_sha256") or "").strip()
            reading_url = ""
            assigned_group = None
            if group_id_raw not in (None, ""):
                try:
                    group_id = int(group_id_raw)
                except Exception:
                    self.send_json({"ok": False, "error": "group_id 不合法"}, status=400)
                    return True
                if not reading_group_exists(group_id):
                    self.send_json({"ok": False, "error": "Reading Group 不存在"}, status=404)
                    return True
                if citation_id:
                    add_citation_to_group(citation_id, group_id)
                    assigned_group = next((item for item in list_reading_groups() if int(item.get("id") or 0) == group_id), None)
            elif citation_id and search_slug:
                group_name = _resolve_search_group_name(search_slug, paper)
                if group_name:
                    assigned_group = get_or_create_compatible_reading_group(
                        group_name,
                        f"自动为搜索「{group_name}」创建的深度阅读分组。",
                        source_kind="auto",
                    )
                    add_citation_to_group(citation_id, int(assigned_group["id"]))
            refresh_job = None
            reading_paper_id = ""
            if pdf_path and citation_id:
                reading = ensure_reading_workspace_for_citation(citation_id)
                reading_url = reading["reading_url"]
                reading_paper_id = reading["paper_id"]
                try:
                    refresh_job = start_openclaw_refresh_job_for_paper(
                        reading["paper_id"],
                        run_metadata=True,
                        run_analysis=True,
                    )
                except Exception:
                    refresh_job = None
            self.send_json(
                {
                    "ok": True,
                    "message": "已加入深度阅读，OpenClaw 正在自动解析 PDF。",
                    "citation_id": citation_id,
                    "pdf_path": pdf_path or "",
                    "pdf_reused": bool(pdf_record.get("reused")),
                    "reading_url": reading_url,
                    "paper_id": reading_paper_id,
                    "job_id": (refresh_job or {}).get("id") or "",
                    "job": refresh_job or {},
                    "group": assigned_group or {},
                }
            )
            return True

        if self.path == "/api/citations/export":
            data = self.parse_body()
            ids = data.get("ids") or []
            try:
                ids = [int(item) for item in ids]
            except Exception:
                self.send_json({"ok": False, "error": "ids 格式不正确"}, status=400)
                return True
            items = get_citations_by_ids(ids)
            payload = {
                "exported_at": datetime.utcnow().isoformat(timespec="seconds") + "Z",
                "count": len(items),
                "items": items,
            }
            raw = json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8")
            filename = f"citations-export-{datetime.now().strftime('%Y%m%d-%H%M%S')}.json"
            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(raw)))
            self.send_header("Content-Disposition", f'attachment; filename="{filename}"')
            self.end_headers()
            self.wfile.write(raw)
            return True

        if self.path == "/api/citations/tags":
            data = self.parse_body()
            try:
                citation_id = int(data.get("id"))
            except Exception:
                self.send_json({"ok": False, "error": "缺少合法的 citation id"}, status=400)
                return True
            tags = data.get("tags", "")
            update_citation_tags(citation_id, tags)
            self.send_json({"ok": True, "message": "tags 已更新。"})
            return True

        if self.path.startswith("/api/citations/") and self.path.endswith("/pdf"):
            data = self.parse_body()
            parts = [part for part in self.path.strip("/").split("/") if part]
            try:
                citation_id = int(parts[2])
            except Exception:
                self.send_json({"ok": False, "error": "citation id 不合法"}, status=400)
                return True
            citation = get_citation_by_id(citation_id)
            if not citation:
                self.send_json({"ok": False, "error": "Citation 不存在"}, status=404)
                return True
            try:
                pdf_record = store_uploaded_pdf(data.get("pdf"), citation.get("title") or "")
            except ValueError as exc:
                self.send_json({"ok": False, "error": str(exc)}, status=400)
                return True
            pdf_path = pdf_record.get("pdf_path") or ""
            pdf_sha256 = pdf_record.get("pdf_sha256") or ""
            if not pdf_path:
                self.send_json({"ok": False, "error": "缺少 PDF 文件"}, status=400)
                return True
            update_citation_pdf(citation_id, pdf_path, pdf_sha256)
            citation = get_citation_by_id(citation_id)
            reading_url = ""
            refresh_job = None
            if citation:
                try:
                    reading = ensure_reading_workspace_for_citation(citation_id)
                    reading_url = reading["reading_url"]
                    refresh_job = start_openclaw_refresh_job_for_paper(
                        reading["paper_id"],
                        run_metadata=True,
                        run_analysis=True,
                    )
                except Exception:
                    reading_url = ""
                    refresh_job = None
            self.send_json(
                {
                    "ok": True,
                    "message": "PDF 已绑定到该文献，OpenClaw 正在自动解析。",
                    "pdf_path": pdf_path,
                    "pdf_reused": bool(pdf_record.get("reused")),
                    "reading_url": reading_url,
                    "job_id": (refresh_job or {}).get("id") or "",
                    "job": refresh_job or {},
                }
            )
            return True

        if self.path.startswith("/api/citations/") and "/groups/" in self.path:
            parts = [part for part in self.path.strip("/").split("/") if part]
            try:
                citation_id = int(parts[2])
                group_id = int(parts[4])
            except Exception:
                self.send_json({"ok": False, "error": "路径参数不合法"}, status=400)
                return True
            if not citation_exists(citation_id):
                self.send_json({"ok": False, "error": "Citation 不存在"}, status=404)
                return True
            if not reading_group_exists(group_id):
                self.send_json({"ok": False, "error": "Reading Group 不存在"}, status=404)
                return True
            add_citation_to_group(citation_id, group_id)
            self.send_json({"ok": True, "message": "已加入 Group。"})
            return True

        if self.path.startswith("/api/citations/") and self.path.endswith("/reading"):
            parts = [part for part in self.path.strip("/").split("/") if part]
            try:
                citation_id = int(parts[2])
            except Exception:
                self.send_json({"ok": False, "error": "citation id 不合法"}, status=400)
                return True
            if not citation_exists(citation_id):
                self.send_json({"ok": False, "error": "Citation 不存在"}, status=404)
                return True
            try:
                reading = ensure_reading_workspace_for_citation(citation_id)
            except ValueError as exc:
                self.send_json({"ok": False, "error": str(exc)}, status=400)
                return True
            self.send_json({"ok": True, "reading_url": reading["reading_url"], "paper_id": reading["paper_id"]})
            return True

        return False

    def _handle_group_post(self) -> bool:
        if self.path != "/api/reading-groups":
            return False
        data = self.parse_body()
        name = (data.get("name") or "").strip()
        description = (data.get("description") or "").strip()
        if not name:
            self.send_json({"ok": False, "error": "Group 名称不能为空"}, status=400)
            return True
        try:
            group_id = create_reading_group(name, description)
        except sqlite3.IntegrityError:
            self.send_json({"ok": False, "error": "Group 名称已存在"}, status=409)
            return True
        self.send_json({"ok": True, "group_id": group_id, "message": "Group 已创建。"})
        return True

    def _collect_uploaded_files(self, data: dict) -> list[dict]:
        files = []
        for value in data.values():
            if getattr(value, "filename", None):
                files.append(value)
            elif isinstance(value, list):
                for item in value:
                    if getattr(item, "filename", None):
                        files.append(item)
        return files

    def _handle_openclaw_post(self) -> bool:
        if self.path == "/api/openclaw-image-intake/upload":
            data = self.parse_body()
            files = self._collect_uploaded_files(data)
            if not files:
                self.send_json({"ok": False, "error": "请至少上传一张图片文件"}, status=400)
                return True
            try:
                with user_context(openclaw_default_username()):
                    job = start_openclaw_picsearch_job(files)
            except (ValueError, OpenClawIngestError) as exc:
                self.send_json({"ok": False, "error": str(exc)}, status=400)
                return True
            except Exception as exc:
                self.send_json({"ok": False, "error": f"图片找论文任务启动失败: {exc}"}, status=500)
                return True
            self.send_json(
                {
                    "ok": True,
                    "job_id": job["id"],
                    "job": job,
                    "message": f"已提交 {len(job.get('items') or [])} 张图片到 picsearch 队列，系统会依次识别并追加到今日 Picsearch timeline。",
                }
            )
            return True

        if self.path != "/api/openclaw-intake/upload":
            return False
        data = self.parse_body()
        files = self._collect_uploaded_files(data)
        if not files:
            self.send_json({"ok": False, "error": "请至少上传一个 PDF 文件"}, status=400)
            return True
        try:
            with user_context(openclaw_default_username()):
                job = start_openclaw_intake_job(files, data.get("group_id"))
        except (ValueError, OpenClawIngestError) as exc:
            self.send_json({"ok": False, "error": str(exc)}, status=400)
            return True
        except Exception as exc:
            self.send_json({"ok": False, "error": f"OpenClaw PDF 导入启动失败: {exc}"}, status=500)
            return True
        self.send_json(
            {
                "ok": True,
                "job_id": job["id"],
                "job": job,
                "message": f"已提交 {len(job.get('items') or [])} 个 PDF 到 OpenClaw 导入队列，系统会自动复用或合并现有记录。",
            }
        )
        return True

    def _reading_paper_id_from_path(self) -> str:
        parts = [part for part in self.path.strip("/").split("/") if part]
        return parts[2] if len(parts) >= 4 else ""

    def _handle_reading_post(self) -> bool:
        if self.path.startswith("/api/reading/") and self.path.endswith("/analyze"):
            paper_id = self._reading_paper_id_from_path()
            if not paper_id:
                self.send_json({"ok": False, "error": "paper id 不合法"}, status=400)
                return True
            try:
                result = start_analysis_job(paper_id)
            except Exception as exc:
                self.send_json({"ok": False, "error": f"分析失败: {exc}"}, status=500)
                return True
            self.send_json(
                {
                    "ok": True,
                    "message": "分析任务已启动。" if result.get("started") else "分析任务已在运行。",
                    "started": bool(result.get("started")),
                    "status": result.get("status") or {},
                    "job": result.get("job"),
                }
            )
            return True

        if self.path.startswith("/api/reading/") and self.path.endswith("/metadata"):
            paper_id = self._reading_paper_id_from_path()
            if not paper_id:
                self.send_json({"ok": False, "error": "paper id 不合法"}, status=400)
                return True
            try:
                result = start_metadata_job_for_paper(paper_id)
            except Exception as exc:
                self.send_json({"ok": False, "error": f"元数据识别失败: {exc}"}, status=500)
                return True
            self.send_json(
                {
                    "ok": True,
                    "message": "元数据识别任务已启动。" if result.get("started") else "元数据识别任务已在运行。",
                    "started": bool(result.get("started")),
                    "status": result.get("status") or {},
                    "job": result.get("job"),
                }
            )
            return True

        if self.path.startswith("/api/reading/") and self.path.endswith("/questions"):
            paper_id = self._reading_paper_id_from_path()
            if not paper_id:
                self.send_json({"ok": False, "error": "paper id 不合法"}, status=400)
                return True
            data = self.parse_body()
            question = " ".join(str(data.get("question") or "").split())
            if not question:
                self.send_json({"ok": False, "error": "问题不能为空"}, status=400)
                return True
            try:
                item = answer_reading_question(paper_id, question)
            except Exception as exc:
                self.send_json({"ok": False, "error": f"提问失败: {exc}"}, status=500)
                return True
            self.send_json({"ok": True, "item": item})
            return True

        if self.path.startswith("/api/reading/") and self.path.endswith("/notes"):
            paper_id = self._reading_paper_id_from_path()
            if not paper_id:
                self.send_json({"ok": False, "error": "paper id 不合法"}, status=400)
                return True
            data = self.parse_body()
            module_name = (data.get("module") or "").strip()
            content = str(data.get("content") or "")
            try:
                item = save_manual_note(paper_id, module_name, content)
            except Exception as exc:
                self.send_json({"ok": False, "error": f"保存 Notes 失败: {exc}"}, status=400)
                return True
            self.send_json({"ok": True, "item": item})
            return True

        return False

    def _handle_reference_post(self) -> bool:
        if self.path != "/api/papers/expand-references":
            return False
        data = self.parse_body()
        search_slug = (data.get("search_slug") or "").strip()
        paper = data.get("paper") or {}
        try:
            job = start_reference_expansion_job(search_slug, paper)
        except ValueError as exc:
            self.send_json({"ok": False, "error": str(exc)}, status=400)
            return True
        except Exception as exc:
            self.send_json({"ok": False, "error": f"扩展引用失败: {exc}"}, status=500)
            return True
        self.send_json({"ok": True, "job": job})
        return True

    def _handle_search_entry_post(self) -> bool:
        if self.path != "/api/search-entries/title":
            return False
        data = self.parse_body()
        relative_dir = " ".join(str(data.get("relative_dir") or "").split()).strip()
        title = str(data.get("title") or "")
        if not relative_dir:
            self.send_json({"ok": False, "error": "搜索目录不合法"}, status=400)
            return True
        try:
            payload = update_search_entry_title(relative_dir, title)
        except FileNotFoundError:
            self.send_json({"ok": False, "error": "搜索结果不存在"}, status=404)
            return True
        except ValueError as exc:
            self.send_json({"ok": False, "error": str(exc)}, status=400)
            return True
        except Exception as exc:
            self.send_json({"ok": False, "error": f"更新标题失败: {exc}"}, status=500)
            return True
        self.send_json(
            {
                "ok": True,
                "message": "Timeline 标题已更新。" if payload.get("customized") else "已恢复默认标题。",
                **payload,
            }
        )
        return True

    def _handle_research_post(self) -> bool:
        if self.path == "/api/research/plan/compose":
            data = self.parse_body()
            latest_input = " ".join(str(data.get("input") or "").split())
            current_prompt = " ".join(str(data.get("current_prompt") or "").split())
            plan = data.get("plan")
            if isinstance(plan, str):
                try:
                    plan = json.loads(plan)
                except Exception:
                    plan = None
            if not latest_input:
                self.send_json({"ok": False, "error": "请输入 research 内容"}, status=400)
                return True
            try:
                payload = compose_research_plan_request(latest_input, current_prompt=current_prompt, current_plan=plan if isinstance(plan, dict) else None)
            except (ValueError, OpenClawIngestError) as exc:
                self.send_json({"ok": False, "error": str(exc)}, status=400)
                return True
            except Exception as exc:
                self.send_json({"ok": False, "error": f"research 方案生成失败: {exc}"}, status=500)
                return True
            self.send_json({"ok": True, **payload})
            return True

        if self.path == "/api/research/plan/validate":
            data = self.parse_body()
            prompt = " ".join(str(data.get("prompt") or "").split())
            plan = data.get("plan")
            if isinstance(plan, str):
                try:
                    plan = json.loads(plan)
                except Exception:
                    plan = None
            if not prompt:
                self.send_json({"ok": False, "error": "缺少原始 research 需求"}, status=400)
                return True
            if not isinstance(plan, dict):
                self.send_json({"ok": False, "error": "缺少当前 research 方案"}, status=400)
                return True
            try:
                validated = verify_research_plan(prompt, plan)
            except (ValueError, OpenClawIngestError) as exc:
                self.send_json({"ok": False, "error": str(exc)}, status=400)
                return True
            except Exception as exc:
                self.send_json({"ok": False, "error": f"research 方案验证失败: {exc}"}, status=500)
                return True
            self.send_json({"ok": True, "plan": validated, "prompt": prompt})
            return True

        if self.path == "/api/research/plan/revise":
            data = self.parse_body()
            prompt = " ".join(str(data.get("prompt") or "").split())
            modify_request = " ".join(str(data.get("modify_request") or "").split())
            plan = data.get("plan")
            if isinstance(plan, str):
                try:
                    plan = json.loads(plan)
                except Exception:
                    plan = None
            if not prompt:
                self.send_json({"ok": False, "error": "缺少原始 research 需求"}, status=400)
                return True
            if not isinstance(plan, dict):
                self.send_json({"ok": False, "error": "缺少当前 research 方案"}, status=400)
                return True
            if not modify_request:
                self.send_json({"ok": False, "error": "请输入修改要求"}, status=400)
                return True
            try:
                revised = revise_research_plan(prompt, plan, modify_request)
            except (ValueError, OpenClawIngestError) as exc:
                self.send_json({"ok": False, "error": str(exc)}, status=400)
                return True
            except Exception as exc:
                self.send_json({"ok": False, "error": f"research 方案修改失败: {exc}"}, status=500)
                return True
            self.send_json({"ok": True, "plan": revised, "prompt": prompt, "modify_request": modify_request})
            return True

        if self.path == "/api/research/plan":
            data = self.parse_body()
            prompt = " ".join(str(data.get("prompt") or "").split())
            if not prompt:
                self.send_json({"ok": False, "error": "请输入 research 需求"}, status=400)
                return True
            try:
                plan = preview_research_plan(prompt)
            except (ValueError, OpenClawIngestError) as exc:
                self.send_json({"ok": False, "error": str(exc)}, status=400)
                return True
            except Exception as exc:
                self.send_json({"ok": False, "error": f"research 方案生成失败: {exc}"}, status=500)
                return True
            self.send_json({"ok": True, "plan": plan, "prompt": prompt})
            return True

        if self.path != "/api/research/jobs":
            return False
        data = self.parse_body()
        prompt = " ".join(str(data.get("prompt") or "").split())
        if not prompt:
            self.send_json({"ok": False, "error": "请输入 research 需求"}, status=400)
            return True
        plan = data.get("plan")
        if isinstance(plan, str):
            try:
                plan = json.loads(plan)
            except Exception:
                plan = None
        try:
            job = start_research_job_with_plan(prompt, plan if isinstance(plan, dict) else None)
        except (ValueError, OpenClawIngestError) as exc:
            self.send_json({"ok": False, "error": str(exc)}, status=400)
            return True
        except Exception as exc:
            self.send_json({"ok": False, "error": f"research 任务启动失败: {exc}"}, status=500)
            return True
        self.send_json({"ok": True, "job": job, "job_id": job["id"]})
        return True

    def _handle_group_delete(self) -> bool:
        if not self.path.startswith("/api/reading-groups/"):
            return False
        group_id_raw = self.path.rsplit("/", 1)[-1]
        try:
            group_id = int(group_id_raw)
        except Exception:
            self.send_json({"ok": False, "error": "group id 不合法"}, status=400)
            return True
        if not reading_group_exists(group_id):
            self.send_json({"ok": False, "error": "Reading Group 不存在"}, status=404)
            return True
        delete_reading_group(group_id)
        self.send_json({"ok": True, "message": "Group 已删除，文章保留。"})
        return True

    def _handle_citation_delete(self) -> bool:
        if self.path.startswith("/api/citations/") and "/groups/" in self.path:
            parts = [part for part in self.path.strip("/").split("/") if part]
            try:
                citation_id = int(parts[2])
                group_id = int(parts[4])
            except Exception:
                self.send_json({"ok": False, "error": "路径参数不合法"}, status=400)
                return True
            remove_citation_from_group(citation_id, group_id)
            self.send_json({"ok": True, "message": "已移出 Group。"})
            return True

        if self.path.startswith("/api/citations/") and self.path.endswith("/reading"):
            parts = [part for part in self.path.strip("/").split("/") if part]
            try:
                citation_id = int(parts[2])
            except Exception:
                self.send_json({"ok": False, "error": "citation id 不合法"}, status=400)
                return True
            if not citation_exists(citation_id):
                self.send_json({"ok": False, "error": "Citation 不存在"}, status=404)
                return True
            removed = remove_reading_workspace_for_citation(citation_id)
            self.send_json({"ok": True, "message": "已删除该深度阅读文献及其相关数据。", "removed": removed})
            return True

        return False

    def _handle_reading_delete(self) -> bool:
        if not (self.path.startswith("/api/reading/") and "/questions/" in self.path):
            return False
        parts = [part for part in self.path.strip("/").split("/") if part]
        paper_id = parts[2] if len(parts) >= 5 else ""
        qa_id = parts[4] if len(parts) >= 5 else ""
        if not paper_id or not qa_id:
            self.send_json({"ok": False, "error": "路径参数不合法"}, status=400)
            return True
        removed = delete_reading_question_history_item(paper_id, qa_id)
        if not removed:
            self.send_json({"ok": False, "error": "提问记录不存在"}, status=404)
            return True
        self.send_json({"ok": True, "message": "提问记录已删除。"})
        return True

    def _handle_research_delete(self) -> bool:
        if not self.path.startswith("/api/research/jobs/"):
            return False
        job_id = unquote(self.path[len("/api/research/jobs/"):]).strip().strip("/")
        if not job_id:
            self.send_json({"ok": False, "error": "job id 不合法"}, status=400)
            return True
        if not delete_research_job(job_id):
            self.send_json({"ok": False, "error": "任务不存在"}, status=404)
            return True
        self.send_json({"ok": True, "message": "Research 记录已删除。"})
        return True

    def _handle_search_entry_delete(self) -> bool:
        if not self.path.startswith("/api/search-entries/"):
            return False
        relative_dir = unquote(self.path[len("/api/search-entries/"):]).strip().strip("/")
        if not relative_dir:
            self.send_json({"ok": False, "error": "搜索目录不合法"}, status=400)
            return True
        if not delete_search_entry(relative_dir):
            self.send_json({"ok": False, "error": "搜索结果不存在或无法删除"}, status=404)
            return True
        self.send_json({"ok": True, "message": "Timeline 搜索结果已删除。"})
        return True

    # HTTP verb handlers
    def do_GET(self):
        try:
            self._normalize_request_target_inplace()
            with user_context(self.current_username()):
                self._do_GET_impl()
        except Exception as exc:
            self.handle_server_error(exc)

    def _do_GET_impl(self):
        if self.path == "/api/auth/status":
            self.send_json(
                {
                    "ok": True,
                    "authenticated": self.is_authenticated(),
                    "require_password": require_password(),
                    "username": self.current_username(),
                    "has_users": has_users(),
                }
            )
            return

        if self.path == "/login":
            if self.is_authenticated():
                self.send_response(302)
                self.send_header("Location", "/")
                self.end_headers()
                return
            self.send_html(build_login_html(has_users=has_users() or bool(PASSWORD_SALT and PASSWORD_HASH)))
            return

        if not self.is_authenticated() and not self.is_openclaw_public_api():
            self.reject_unauthorized()
            return

        if self.path in ("/", "/index.html"):
            self.send_html(build_timeline_html())
            return

        if self.path == "/keywords":
            self.send_html(build_keywords_html())
            return

        if self.path.startswith("/keywords/intersection"):
            query = self.path.split("?", 1)[1] if "?" in self.path else ""
            params = parse_qs(query, keep_blank_values=False)
            raw_keywords = params.get("tags") or []
            selected_keywords = []
            for item in raw_keywords:
                selected_keywords.extend(part.strip() for part in str(item).split(",") if part.strip())
            self.send_html(build_keyword_intersection_html(selected_keywords))
            return

        if self.path.startswith("/keywords/"):
            keyword = unquote(self.path[len("/keywords/"):]).strip().strip("/")
            self.send_html(build_keyword_detail_html(keyword))
            return

        if self.path in ("/library", "/library/", "/reading", "/reading/"):
            self.send_html(build_library_html())
            return

        if self.path.startswith("/reading/"):
            paper_id = unquote(self.path[len("/reading/"):]).strip().strip("/")
            if paper_id and "/" not in paper_id:
                bundle = load_reading_bundle(paper_id)
                if bundle and ((bundle.get("paper") or {}).get("pdf") or {}).get("file_path"):
                    self.send_html(build_reading_detail_html(paper_id))
                    return
                self.send_html("<!doctype html><html lang='zh-CN'><body><h1>该阅读页缺少可访问的 PDF，暂时不能打开。</h1></body></html>", status=404)
                return

        if self.path == "/api/openclaw-intake/jobs":
            with user_context(openclaw_default_username()):
                self.send_json({"ok": True, "jobs": list_openclaw_jobs()})
            return

        if self.path == "/api/openclaw-image-intake/jobs":
            with user_context(openclaw_default_username()):
                jobs = [job for job in list_openclaw_jobs() if (job.get("kind") or "") == "openclaw_batch_picsearch"]
                self.send_json({"ok": True, "jobs": jobs})
            return

        if self.path == "/api/research/jobs":
            self.send_json({"ok": True, "jobs": list_research_jobs()})
            return

        if self.path.startswith("/api/research/jobs/"):
            job_id = unquote(self.path[len("/api/research/jobs/"):]).strip().strip("/")
            if not job_id:
                self.send_json({"ok": False, "error": "job id 不合法"}, status=400)
                return
            job = load_research_job(job_id)
            if not job:
                self.send_json({"ok": False, "error": "任务不存在"}, status=404)
                return
            self.send_json({"ok": True, "job": job})
            return

        if self.path.startswith("/api/openclaw-intake/jobs/"):
            job_id = unquote(self.path[len("/api/openclaw-intake/jobs/"):]).strip().strip("/")
            if not job_id:
                self.send_json({"ok": False, "error": "job id 不合法"}, status=400)
                return
            with user_context(openclaw_default_username()):
                job = load_openclaw_job(job_id)
            if not job:
                self.send_json({"ok": False, "error": "任务不存在"}, status=404)
                return
            self.send_json({"ok": True, "job": job})
            return

        if self.path.startswith("/api/openclaw-image-intake/jobs/"):
            job_id = unquote(self.path[len("/api/openclaw-image-intake/jobs/"):]).strip().strip("/")
            if not job_id:
                self.send_json({"ok": False, "error": "job id 不合法"}, status=400)
                return
            with user_context(openclaw_default_username()):
                job = load_openclaw_job(job_id)
            if not job or (job.get("kind") or "") != "openclaw_batch_picsearch":
                self.send_json({"ok": False, "error": "任务不存在"}, status=404)
                return
            self.send_json({"ok": True, "job": job})
            return

        if self.path.startswith("/api/reading/") and self.path.endswith("/status"):
            paper_id = unquote(self.path[len("/api/reading/"):]).strip().strip("/")
            paper_id = paper_id[:-len("/status")].rstrip("/") if paper_id.endswith("/status") else paper_id
            if not paper_id:
                self.send_json({"ok": False, "error": "paper id 不合法"}, status=400)
                return
            try:
                status_payload = get_reading_status_payload(paper_id)
            except ValueError as exc:
                self.send_json({"ok": False, "error": str(exc)}, status=404)
                return
            self.send_json({"ok": True, "status": status_payload})
            return

        if self.path == "/api/citations":
            self.send_json({"ok": True, "items": list_citations_with_groups()})
            return

        if self.path == "/api/reading-groups":
            self.send_json({"ok": True, "groups": list_reading_groups()})
            return

        if self.path == "/api/expansions":
            self.send_json({"ok": True, "items": list_expansion_sites()})
            return

        if self.path == "/api/papers/expand-references/jobs":
            self.send_json({"ok": True, "items": list_reference_jobs()})
            return

        if self.path.startswith("/api/papers/expand-references/jobs/"):
            job_id = unquote(self.path[len("/api/papers/expand-references/jobs/"):]).strip().strip("/")
            if not job_id:
                self.send_json({"ok": False, "error": "job id 不合法"}, status=400)
                return
            job = load_reference_job(job_id)
            if not job:
                self.send_json({"ok": False, "error": "任务不存在"}, status=404)
                return
            self.send_json({"ok": True, "job": job})
            return

        static_target = self._resolve_data_request_path(self.path)
        if static_target and static_target.exists():
            if not self._is_allowed_static_data_path(static_target):
                self.send_error(404, "File not found")
                return

        super().do_GET()

    def do_POST(self):
        try:
            self._normalize_request_target_inplace()
            with user_context(self.current_username()):
                self._do_POST_impl()
        except Exception as exc:
            self.handle_server_error(exc)

    def _do_POST_impl(self):
        if self._handle_auth_post():
            return

        if not self.is_authenticated() and not self.is_openclaw_public_api():
            self.reject_unauthorized()
            return

        for handler in (
            self._handle_citation_post,
            self._handle_group_post,
            self._handle_search_entry_post,
            self._handle_research_post,
            self._handle_openclaw_post,
            self._handle_reading_post,
            self._handle_reference_post,
        ):
            if handler():
                return

        self.send_json({"ok": False, "error": "not_found"}, status=404)

    def do_DELETE(self):
        try:
            self._normalize_request_target_inplace()
            with user_context(self.current_username()):
                self._do_DELETE_impl()
        except Exception as exc:
            self.handle_server_error(exc)

    def _do_DELETE_impl(self):
        if not self.is_authenticated():
            self.reject_unauthorized()
            return

        for handler in (
            self._handle_group_delete,
            self._handle_citation_delete,
            self._handle_reading_delete,
            self._handle_research_delete,
            self._handle_search_entry_delete,
        ):
            if handler():
                return

        self.send_json({"ok": False, "error": "not_found"}, status=404)


# Server entrypoint
def main():
    ROOT_DATA_DIR.mkdir(parents=True, exist_ok=True)
    USERS_DIR.mkdir(parents=True, exist_ok=True)
    if not has_users() and not require_password():
        SEARCHES_DIR.mkdir(parents=True, exist_ok=True)
        EXPANSIONS_DIR.mkdir(parents=True, exist_ok=True)
        ensure_db()
    server = ReusableThreadingHTTPServer((HOST, PORT), SearchSiteHandler)
    print(f"[site] serving {ROOT_DATA_DIR} at http://{HOST}:{PORT}")
    server.serve_forever()


if __name__ == "__main__":
    main()
