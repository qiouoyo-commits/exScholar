"""HTTP handlers and server entrypoint for the exScholar site."""

from ..core import *
from ..ui.pages import *


class SearchSiteHandler(SimpleHTTPRequestHandler):
    # Request parsing and response helpers
    def translate_path(self, path):
        path = unquote(path.split("?", 1)[0].split("#", 1)[0]).lstrip("/")
        return str(DATA_DIR / path)

    def parse_body(self):
        length = int(self.headers.get("Content-Length", "0") or "0")
        raw = self.rfile.read(length) if length else b""
        content_type = self.headers.get("Content-Type", "")
        if "multipart/form-data" in content_type:
            environ = {
                "REQUEST_METHOD": self.command,
                "CONTENT_TYPE": content_type,
                "CONTENT_LENGTH": str(length),
            }
            form = __import__("cgi").FieldStorage(
                fp=io.BytesIO(raw),
                headers=self.headers,
                environ=environ,
                keep_blank_values=True,
            )
            data = {}
            if getattr(form, "list", None):
                for field in form.list:
                    if field.filename:
                        data[field.name] = field
                    elif field.name in data:
                        current = data[field.name]
                        if isinstance(current, list):
                            current.append(field.value)
                        else:
                            data[field.name] = [current, field.value]
                    else:
                        data[field.name] = field.value
            return data
        if "application/json" in content_type:
            return json.loads(raw.decode("utf-8") or "{}")
        if "application/x-www-form-urlencoded" in content_type:
            text = raw.decode("utf-8")
            pairs = [part.split("=", 1) for part in text.split("&") if "=" in part]
            return {k: unquote(v.replace("+", " ")) for k, v in pairs}
        return {}

    def send_json(self, payload: dict, status: int = 200, extra_headers: dict | None = None):
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
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
        if extra_headers:
            for key, value in extra_headers.items():
                self.send_header(key, value)
        self.end_headers()
        self.wfile.write(data)

    def current_session(self):
        if not require_password():
            return {"id": "no-password", "created_at": time.time()}
        cookie_header = self.headers.get("Cookie", "")
        jar = cookies.SimpleCookie()
        jar.load(cookie_header)
        token = jar.get(SESSION_COOKIE)
        if not token:
            return None
        return SESSIONS.get(token.value)

    def is_authenticated(self) -> bool:
        return self.current_session() is not None

    def reject_unauthorized(self):
        if self.path.startswith("/api/"):
            self.send_json({"ok": False, "error": "unauthorized"}, status=401)
        else:
            self.send_response(302)
            self.send_header("Location", "/login")
            self.end_headers()

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
            password = data.get("password", "")
            if not verify_password(password):
                if "application/json" in self.headers.get("Content-Type", ""):
                    self.send_json({"ok": False, "error": "invalid_password"}, status=403)
                else:
                    self.send_html(build_login_html("密码错误，请重试。"), status=403)
                return True

            token = secrets.token_urlsafe(32)
            SESSIONS[token] = {"created_at": time.time()}
            headers = {
                "Set-Cookie": f"{SESSION_COOKIE}={token}; Path=/; HttpOnly; SameSite=Lax",
            }
            if "application/json" in self.headers.get("Content-Type", ""):
                self.send_json({"ok": True}, extra_headers=headers)
            else:
                self.send_response(302)
                self.send_header("Location", "/")
                for key, value in headers.items():
                    self.send_header(key, value)
                self.end_headers()
            return True

        if self.path == "/api/auth/logout":
            jar = cookies.SimpleCookie()
            jar.load(self.headers.get("Cookie", ""))
            token = jar.get(SESSION_COOKIE)
            if token:
                SESSIONS.pop(token.value, None)
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
            reading_url = ""
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
            if pdf_path and citation_id:
                reading = ensure_reading_workspace_for_citation(citation_id)
                reading_url = reading["reading_url"]
            self.send_json(
                {
                    "ok": True,
                    "message": "已加入深度阅读。",
                    "citation_id": citation_id,
                    "pdf_path": pdf_path or "",
                    "pdf_reused": bool(pdf_record.get("reused")),
                    "reading_url": reading_url,
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
            if citation:
                try:
                    reading_url = ensure_reading_workspace_for_citation(citation_id)["reading_url"]
                except Exception:
                    reading_url = ""
            self.send_json(
                {
                    "ok": True,
                    "message": "PDF 已绑定到该文献。",
                    "pdf_path": pdf_path,
                    "pdf_reused": bool(pdf_record.get("reused")),
                    "reading_url": reading_url,
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
        if self.path == "/api/openclaw-intake/upload":
            data = self.parse_body()
            files = self._collect_uploaded_files(data)
            if not files:
                self.send_json({"ok": False, "error": "请至少上传一个 PDF 文件"}, status=400)
                return True
            try:
                job = start_openclaw_intake_job(files, data.get("group_id"))
            except (ValueError, OpenClawIngestError) as exc:
                self.send_json({"ok": False, "error": str(exc)}, status=400)
                return True
            except Exception as exc:
                self.send_json({"ok": False, "error": f"OpenClaw 批量导入启动失败: {exc}"}, status=500)
                return True
            self.send_json(
                {
                    "ok": True,
                    "job_id": job["id"],
                    "job": job,
                    "message": f"已提交 {len(job.get('items') or [])} 个 PDF 到 OpenClaw 导入队列。",
                }
            )
            return True

        if self.path != "/api/reading/upload":
            return False
        data = self.parse_body()
        file_item = data.get("pdf")
        if not file_item or not getattr(file_item, "filename", None):
            self.send_json({"ok": False, "error": "请上传一个 PDF 文件"}, status=400)
            return True
        try:
            job = start_openclaw_intake_job([file_item], data.get("group_id"))
        except (ValueError, OpenClawIngestError) as exc:
            self.send_json({"ok": False, "error": str(exc)}, status=400)
            return True
        except Exception as exc:
            self.send_json({"ok": False, "error": f"OpenClaw 上传处理失败: {exc}"}, status=500)
            return True
        first_item = (job.get("items") or [{}])[0]
        self.send_json(
            {
                "ok": True,
                "citation_id": first_item.get("citation_id"),
                "matched_existing": bool(first_item.get("pdf_reused")),
                "metadata": first_item.get("metadata") or {},
                "pdf_path": first_item.get("pdf_path") or "",
                "pdf_reused": bool(first_item.get("pdf_reused")),
                "reading_url": first_item.get("reading_url") or "",
                "paper_id": first_item.get("paper_id") or "",
                "job_id": job["id"],
                "job": job,
                "message": "PDF 已提交到 OpenClaw Addon，元数据识别与论文分析正在后台处理。",
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

        if self.path == "/api/reading/batch-generate":
            try:
                result = start_batch_generation_job()
            except Exception as exc:
                self.send_json({"ok": False, "error": f"启动批处理失败: {exc}"}, status=500)
                return True
            self.send_json(
                {
                    "ok": True,
                    "message": "批处理任务已启动。" if result.get("started") else "批处理任务已在运行。",
                    "started": bool(result.get("started")),
                    "status": result.get("status") or {},
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
            site_url = create_reference_search(search_slug, paper)
        except ValueError as exc:
            self.send_json({"ok": False, "error": str(exc)}, status=400)
            return True
        except Exception as exc:
            self.send_json({"ok": False, "error": f"扩展引用失败: {exc}"}, status=500)
            return True
        self.send_json({"ok": True, "site_url": site_url})
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

    # HTTP verb handlers
    def do_GET(self):
        if self.path == "/api/auth/status":
            self.send_json({"ok": True, "authenticated": self.is_authenticated(), "require_password": require_password()})
            return

        if self.path == "/login":
            if self.is_authenticated():
                self.send_response(302)
                self.send_header("Location", "/")
                self.end_headers()
                return
            self.send_html(build_login_html())
            return

        if not self.is_authenticated():
            self.reject_unauthorized()
            return

        if self.path in ("/", "/index.html"):
            self.send_html(build_timeline_html())
            return

        if self.path == "/keywords":
            self.send_html(build_keywords_html())
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

        if self.path == "/api/reading/batch-status":
            self.send_json({"ok": True, "status": get_batch_reading_job_payload()})
            return

        if self.path == "/api/openclaw-intake/jobs":
            self.send_json({"ok": True, "jobs": list_openclaw_jobs()})
            return

        if self.path.startswith("/api/openclaw-intake/jobs/"):
            job_id = unquote(self.path[len("/api/openclaw-intake/jobs/"):]).strip().strip("/")
            if not job_id:
                self.send_json({"ok": False, "error": "job id 不合法"}, status=400)
                return
            job = load_openclaw_job(job_id)
            if not job:
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

        super().do_GET()

    def do_POST(self):
        try:
            self._do_POST_impl()
        except Exception as exc:
            self.handle_server_error(exc)

    def _do_POST_impl(self):
        if self._handle_auth_post():
            return

        if not self.is_authenticated():
            self.reject_unauthorized()
            return

        for handler in (
            self._handle_citation_post,
            self._handle_group_post,
            self._handle_openclaw_post,
            self._handle_reading_post,
            self._handle_reference_post,
        ):
            if handler():
                return

        self.send_json({"ok": False, "error": "not_found"}, status=404)

    def do_DELETE(self):
        try:
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
        ):
            if handler():
                return

        self.send_json({"ok": False, "error": "not_found"}, status=404)


# Server entrypoint
def main():
    SEARCHES_DIR.mkdir(parents=True, exist_ok=True)
    EXPANSIONS_DIR.mkdir(parents=True, exist_ok=True)
    ensure_db()
    server = ReusableThreadingHTTPServer((HOST, PORT), SearchSiteHandler)
    print(f"[site] serving {DATA_DIR} at http://{HOST}:{PORT}")
    server.serve_forever()


if __name__ == "__main__":
    main()
