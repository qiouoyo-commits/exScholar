#!/home/ubuntu/miniconda3/envs/openclaw-analytics/bin/python
import argparse
import json
import mimetypes
import os
import sys
import time
from pathlib import Path

from app.site.core import ensure_db, load_openclaw_job, openclaw_default_username, start_openclaw_intake_job, user_context


class LocalPDFUpload:
    def __init__(self, path: Path):
        self.path = path
        self.filename = path.name
        self.type = mimetypes.guess_type(path.name)[0] or "application/pdf"
        self.file = path.open("rb")

    def close(self):
        try:
            self.file.close()
        except Exception:
            pass


def ingest_paths(paths: list[Path], *, group_id: int | None = None) -> dict:
    uploads: list[LocalPDFUpload] = []
    try:
        for path in paths:
            if not path.exists():
                raise FileNotFoundError(f"PDF 不存在: {path}")
            if path.suffix.lower() != ".pdf":
                raise ValueError(f"仅支持 PDF 文件: {path}")
            uploads.append(LocalPDFUpload(path))
        return start_openclaw_intake_job(uploads, str(group_id) if group_id is not None else None)
    finally:
        for item in uploads:
            item.close()


def wait_for_job(job_id: str, *, poll_interval: float = 2.0, timeout: float = 1800.0) -> dict:
    deadline = time.time() + timeout
    missing_streak = 0
    while time.time() < deadline:
        job = load_openclaw_job(job_id)
        if not job:
            missing_streak += 1
            if missing_streak >= 5:
                raise RuntimeError(f"任务不存在: {job_id}")
            time.sleep(min(poll_interval, 0.5))
            continue
        missing_streak = 0
        if not job.get("running"):
            return job
        time.sleep(poll_interval)
    raise TimeoutError(f"等待任务超时: {job_id}")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="把一个或多个本地 PDF 送进 exScholar 的 OpenClaw 附加 intake 流程。",
    )
    parser.add_argument("pdfs", nargs="+", help="一个或多个本地 PDF 绝对路径")
    parser.add_argument("--group-id", type=int, default=None, help="可选：导入后加入现有 reading group")
    parser.add_argument("--wait", action="store_true", help="等待后台任务完成后再返回")
    parser.add_argument("--timeout", type=float, default=1800.0, help="等待超时秒数，默认 1800")
    parser.add_argument("--poll-interval", type=float, default=2.0, help="轮询间隔秒数，默认 2")
    parser.add_argument("--json", action="store_true", help="输出 JSON")
    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    default_username = openclaw_default_username()
    with user_context(default_username):
        ensure_db()
        paths = [Path(os.path.expanduser(item)).resolve() for item in args.pdfs]
        job = ingest_paths(paths, group_id=args.group_id)
        if args.wait:
            job = wait_for_job(
                job["id"],
                poll_interval=max(args.poll_interval, 0.2),
                timeout=max(args.timeout, 1.0),
            )

    if args.json:
        print(json.dumps(job, ensure_ascii=False, indent=2))
    else:
        print(f"username={default_username}")
        print(f"job_id={job['id']}")
        print(f"status={job.get('status')}")
        print(f"running={job.get('running')}")
        for item in job.get("items") or []:
            title = item.get("title") or item.get("filename") or ""
            reading_url = item.get("reading_url") or ""
            status = item.get("status") or ""
            print(f"- {status}: {title}")
            if reading_url:
                print(f"  reading_url={reading_url}")
            if item.get("error"):
                print(f"  error={item['error']}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False), file=sys.stderr)
        raise SystemExit(1)
