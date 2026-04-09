#!/home/ubuntu/miniconda3/envs/openclaw-analytics/bin/python
import argparse
import json
import mimetypes
import os
import sys
from pathlib import Path

from app.openclaw._cli_utils import wait_for_job
from app.site.core import ensure_db, openclaw_default_username, start_openclaw_picsearch_job, user_context


class LocalImageUpload:
    def __init__(self, path: Path):
        self.path = path
        self.filename = path.name
        self.type = mimetypes.guess_type(path.name)[0] or "image/jpeg"
        self.file = path.open("rb")

    def close(self):
        try:
            self.file.close()
        except Exception:
            pass


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="把一张或多张论文截图交给 OpenClaw 识别，并把结果加入当天 Picsearch timeline。",
    )
    parser.add_argument("images", nargs="+", help="一个或多个本地图片绝对路径或可展开路径")
    parser.add_argument("--wait", action="store_true", help="等待后台任务完成后再返回")
    parser.add_argument("--timeout", type=float, default=1800.0, help="等待超时秒数，默认 1800")
    parser.add_argument("--poll-interval", type=float, default=2.0, help="轮询间隔秒数，默认 2")
    parser.add_argument("--json", action="store_true", help="输出 JSON")
    return parser


def ingest_paths(paths: list[Path]) -> dict:
    uploads: list[LocalImageUpload] = []
    try:
        for path in paths:
            if not path.exists():
                raise FileNotFoundError(f"图片不存在: {path}")
            uploads.append(LocalImageUpload(path))
        return start_openclaw_picsearch_job(uploads)
    finally:
        for item in uploads:
            item.close()
def main() -> int:
    args = build_parser().parse_args()
    username = openclaw_default_username()
    with user_context(username):
        ensure_db()
        paths = [Path(os.path.expanduser(item)).resolve() for item in args.images]
        job = ingest_paths(paths)
        if args.wait:
            job = wait_for_job(
                job["id"],
                poll_interval=max(args.poll_interval, 0.2),
                timeout=max(args.timeout, 1.0),
            )

    payload = {"ok": True, "username": username, "job": job}
    if args.json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        print(f"username={username}")
        print(f"job_id={job['id']}")
        print(f"status={job.get('status')}")
        print(f"running={job.get('running')}")
        timeline = (job.get("links") or {}).get("timeline") or ""
        if timeline:
            print(f"timeline={timeline}")
        for item in job.get("items") or []:
            papers = item.get("papers") or []
            if papers:
                mode = item.get("mode") or ""
                if mode == "scholar_list":
                    print(f"- {(item.get('status') or '')}: {item.get('filename') or ''} -> detected Scholar page, linked {len(papers)} paper(s)")
                else:
                    print(f"- {(item.get('status') or '')}: {item.get('filename') or ''} -> {len(papers)} result(s)")
                for paper in papers:
                    url = paper.get("url") or ((paper.get("ee") or [""])[0] if isinstance(paper.get("ee"), list) else "")
                    print(f"  title={paper.get('title') or ''}")
                    if url:
                        print(f"  url={url}")
            else:
                paper = item.get("paper") or {}
                url = paper.get("url") or ((paper.get("ee") or [""])[0] if isinstance(paper.get("ee"), list) else "")
                print(f"- {(item.get('status') or '')}: {paper.get('title') or item.get('filename') or ''}")
                if item.get("source"):
                    print(f"  source={item['source']}")
                if url:
                    print(f"  url={url}")
            if item.get("error"):
                print(f"  error={item['error']}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False), file=sys.stderr)
        raise SystemExit(1)
