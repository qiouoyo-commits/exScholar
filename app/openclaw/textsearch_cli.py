#!/home/ubuntu/miniconda3/envs/openclaw-analytics/bin/python
import argparse
import json
import sys

from app.openclaw._cli_utils import wait_for_job
from app.openclaw import model_http_transport_mode, split_textsearch_inputs
from app.site.core import ensure_db, openclaw_default_username, start_openclaw_textsearch_job, user_context


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="把论文标题文本交给 OpenClaw 定位链接，并把结果加入当天 Textsearch timeline。",
    )
    parser.add_argument(
        "inputs",
        nargs="+",
        help="单个标题或多个标题。多标题推荐每行一个。",
    )
    parser.add_argument("--wait", action="store_true", help="等待后台任务完成后再返回")
    parser.add_argument("--timeout", type=float, default=1800.0, help="等待超时秒数，默认 1800")
    parser.add_argument("--poll-interval", type=float, default=2.0, help="轮询间隔秒数，默认 2")
    parser.add_argument("--json", action="store_true", help="输出 JSON")
    return parser
def main() -> int:
    args = build_parser().parse_args()
    titles = split_textsearch_inputs(args.inputs)
    if not titles:
        raise ValueError("请至少提供一个论文标题")
    username = openclaw_default_username()
    with user_context(username):
        ensure_db()
        job = start_openclaw_textsearch_job(titles)
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
        print(f"model_http={model_http_transport_mode()}")
        print(f"job_id={job['id']}")
        print(f"status={job.get('status')}")
        print(f"running={job.get('running')}")
        timeline = (job.get("links") or {}).get("timeline") or ""
        if timeline:
            print(f"timeline={timeline}")
        for item in job.get("items") or []:
            paper = item.get("paper") or {}
            url = paper.get("url") or ((paper.get("ee") or [""])[0] if isinstance(paper.get("ee"), list) else "")
            print(f"- {(item.get('status') or '')}: {paper.get('title') or item.get('title_query') or ''}")
            if item.get("source"):
                print(f"  source={item['source']}")
            if item.get("failure_reason"):
                print(f"  failure_reason={item['failure_reason']}")
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
