import json
import os
import re
import time
from difflib import SequenceMatcher

from contextlib import contextmanager
from pathlib import Path

from prettytable import PrettyTable


@contextmanager
def suppress_all_output():
    """安全的输出抑制上下文管理器，兼容异步操作"""
    import logging
    
    # 保存原始的 logging 级别
    original_level = logging.getLogger().level
    
    try:
        # 只禁用 logging，不操作文件描述符
        logging.getLogger().setLevel(logging.CRITICAL)
        
        # 禁用所有 logging handlers
        original_handlers = logging.getLogger().handlers.copy()
        logging.getLogger().handlers.clear()
        logging.getLogger().addHandler(logging.NullHandler())
        
        yield
        
    finally:
        # 恢复 logging 配置
        logging.getLogger().handlers.clear()
        logging.getLogger().handlers.extend(original_handlers)
        logging.getLogger().setLevel(original_level)

def info_by_dir(dir_path: str):
    """
    从指定目录读取所有JSON文件，按年份输出统计表：
    - 每个会议在该年的：总论文数、含DOI数量、含URL数量(ee的第一个url)、含摘要数量
    - 最后输出一个汇总表（跨所有年份与会议）
    """


    if not os.path.exists(dir_path):
        print(f"❌ 目录不存在: {dir_path}")
        return

    json_files = list(Path(dir_path).glob("*.json"))
    if not json_files:
        print(f"❌ 目录中没有找到JSON文件: {dir_path}")
        return

    # 输出收集器
    output_lines = []
    def emit(s: str = ""):
        print(s)
        output_lines.append(s)

    # year -> venue -> metrics
    year_venue_stats = {}
    all_years = set()

    # 全局汇总
    grand_total = 0
    grand_with_doi = 0
    grand_with_url = 0
    grand_with_abs = 0
    grand_without_abs = 0

    def first_url(paper):
        ee = paper.get('ee')
        if isinstance(ee, list):
            return ee[0] if ee else ''
        if isinstance(ee, str):
            return ee
        return ''

    for json_file in sorted(json_files):
        try:
            with open(json_file, 'r', encoding='utf-8') as f:
                data = json.load(f)
            meta = data.get('metadata', {})
            venue = meta.get('venue_name', 'N/A')
            year = meta.get('year', 'N/A')
            papers = data.get('papers', [])
            papers = [paper for paper in papers if paper.get('type', '') != 'Editorship']

            if year == 'N/A':
                continue
            all_years.add(year)

            # 统计当前文件
            total = len(papers)
            with_doi = sum(1 for p in papers if bool(p.get('doi')))
            with_url = sum(1 for p in papers if bool(first_url(p)))
            with_abs = sum(1 for p in papers if bool(p.get('abstract')))

            # 写入分组统计
            if year not in year_venue_stats:
                year_venue_stats[year] = {}
            year_venue_stats[year][venue] = {
                'total': total,
                'with_doi': with_doi,
                'with_url': with_url,
                'with_abstract': with_abs,
                'without_abstract': total - with_abs,
            }

            # 汇总
            grand_total += total
            grand_with_doi += with_doi
            grand_with_url += with_url
            grand_with_abs += with_abs
            grand_without_abs += total - with_abs

        except Exception as e:
            msg = f"⚠️ 读取文件失败: {json_file.name} - {e}"
            print(msg)
            output_lines.append(msg)

    # 按年份输出表格
    sorted_years = sorted([y for y in all_years if isinstance(y, int)])
    # 兼容字符串年份（极少数情况）
    sorted_years += sorted([y for y in all_years if not isinstance(y, int)])

    for year in sorted_years:
        emit("=" * 80)
        emit(f"📊 年份 {year} 统计")
        table = PrettyTable()
        table.field_names = ['会议', '总数', '含DOI', '含URL', '含摘要', '无摘要']

        venues_stats = year_venue_stats.get(year, {})
        for venue in sorted(venues_stats.keys()):
            s = venues_stats[venue]
            table.add_row([
                venue,
                s['total'],
                s['with_doi'],
                s['with_url'],
                s['with_abstract'],
                s['without_abstract'],
            ])

        table.align = 'r'
        table.align['会议'] = 'l'
        emit(str(table))

    # 总结表
    emit("=" * 80)
    emit("📈 汇总（全部年份与会议）")
    sum_table = PrettyTable()
    sum_table.field_names = ['总论文', '含DOI', '含URL', '含摘要', '无摘要']
    sum_table.add_row([grand_total, grand_with_doi, grand_with_url, grand_with_abs, grand_without_abs])
    sum_table.align = 'r'
    emit(str(sum_table))

    # 写入文件
    out_path = os.path.join(dir_path, 'abstract_out.txt')
    try:
        with open(out_path, 'w', encoding='utf-8') as f:
            f.write("\n".join(output_lines) + "\n")
    except Exception as e:
        print(f"⚠️ 写入 abstract_out.txt 失败: {e}")

if __name__ == "__main__":
    pass


def normalize_title(value: str) -> str:
    text = " ".join(str(value or "").strip().lower().split())
    text = re.sub(r"[^a-z0-9]+", " ", text)
    return " ".join(text.split())


def title_similarity(left: str, right: str) -> float:
    a = normalize_title(left)
    b = normalize_title(right)
    if not a or not b:
        return 0.0
    return SequenceMatcher(None, a, b).ratio()


def wait_for_job(load_job, job_id: str, *, poll_interval: float = 2.0, timeout: float = 1800.0) -> dict:
    deadline = time.time() + timeout
    missing_streak = 0
    while time.time() < deadline:
        job = load_job(job_id)
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
