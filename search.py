"""
search.py — 基于关键词的轻量级 DBLP 论文搜索 + 摘要获取 + CSV 导出

用法：
  python search.py \
    --keywords "physiological notification;biosignal alert" \
    --venues chi,uist,cscw \
    --slug physio-ui \
    [--top 100] \
    [--year-from 2020] \
    [--no-abstract]

输出目录结构：
  data/searches/YYYY-MM-DD_<slug>/
    ├── search.json   ← 搜索参数记录
    ├── papers.csv    ← 论文列表（含摘要）
    ├── papers.json   ← 面向静态站点的结构化 JSON
    └── site/index.html ← 可直接打开的静态网页

说明：
  - 默认爬取摘要（无代理，低并发，随机延迟 2-4s，避免反爬）
  - --no-abstract 跳过摘要爬取，只输出标题/年份/链接
  - 不传 --venues 则在全 DBLP 范围内搜索
"""

import os
import sys
import csv
import json
import time
import random
import asyncio
import argparse
import requests
import logging
from datetime import date
from pathlib import Path
from html import escape
from urllib.parse import quote

from dotenv import load_dotenv

ROOT_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.append(ROOT_DIR)
load_dotenv(os.path.join(ROOT_DIR, ".env.local"))

DBLP_SEARCH_URL = "https://dblp.org/search/publ/api"

# HTTP proxies that can reach dblp.org (bypasses the SOCKS5 wrapper at 17900)
_HTTP_PROXY_CANDIDATES = ["http://127.0.0.1:7890"]

def _detect_http_proxy() -> dict | None:
    """Return a working HTTP proxy dict for requests, or None."""
    for proxy_url in _HTTP_PROXY_CANDIDATES:
        try:
            r = requests.get("https://dblp.org/", proxies={"https": proxy_url, "http": proxy_url},
                             timeout=5, headers={"User-Agent": "ccf-research-skill/1.0"})
            if r.status_code < 500:
                return {"http": proxy_url, "https": proxy_url}
        except Exception:
            continue
    return None

_DBLP_PROXIES: dict | None | bool = False  # False = not yet detected

# OpenAlex venue source IDs (fallback when DBLP is unreachable)
OPENALEX_VENUE_IDS = {
    "chi":      "S4363607743",   # CHI Conference on Human Factors in Computing Systems
    "uist":     "S4306421131",   # User Interface Software and Technology
    "cscw":     "S177586587",    # Computer Supported Cooperative Work
    "ubicomp":  "S2764455111",   # UbiComp
    "imwut":    "S4210219751",   # IMWUT
}

NO_PROXY_SESSION = None

def _get_no_proxy_session():
    """Return a requests.Session that bypasses ALL_PROXY env var."""
    global NO_PROXY_SESSION
    if NO_PROXY_SESSION is None:
        import requests
        s = requests.Session()
        s.trust_env = False  # ignore HTTP_PROXY / ALL_PROXY env vars
        NO_PROXY_SESSION = s
    return NO_PROXY_SESSION


def _reconstruct_abstract(inverted_index: dict | None) -> str:
    """Reconstruct abstract text from OpenAlex abstract_inverted_index."""
    if not inverted_index:
        return ""
    words: dict[int, str] = {}
    for word, positions in inverted_index.items():
        for pos in positions:
            words[pos] = word
    return " ".join(words[i] for i in sorted(words))


def openalex_search(keywords: str, venue: str | None, top: int, year_from: int) -> list[dict]:
    """Search via OpenAlex API as fallback when DBLP is unreachable."""
    session = _get_no_proxy_session()
    base = "https://api.openalex.org/works"

    filters = []
    if venue:
        vid = OPENALEX_VENUE_IDS.get(venue.lower())
        if vid:
            filters.append(f"primary_location.source.id:{vid}")
    if year_from:
        filters.append(f"publication_year:{year_from}-2030")

    params = {
        "search": keywords,
        "per-page": min(top, 200),
        "select": "title,publication_year,doi,abstract_inverted_index,authorships,primary_location,ids",
        "mailto": "ccf-crawler@example.com",
    }
    if filters:
        params["filter"] = ",".join(filters)

    papers = []
    try:
        resp = session.get(base, params=params, timeout=30)
        resp.raise_for_status()
        data = resp.json()
    except Exception as e:
        print(f"[search] OpenAlex 请求失败: {e}")
        return []

    for item in data.get("results", []):
        authors_raw = item.get("authorships", [])
        authors = [a.get("author", {}).get("display_name", "") for a in authors_raw]
        doi = item.get("doi") or ""
        if doi.startswith("https://doi.org/"):
            doi = doi[len("https://doi.org/"):]
        venue_name = (item.get("primary_location") or {}).get("source") or {}
        venue_name = venue_name.get("display_name", "") if isinstance(venue_name, dict) else ""
        abstract = _reconstruct_abstract(item.get("abstract_inverted_index"))
        openalex_id = item.get("ids", {}).get("openalex", "")
        ee = [f"https://doi.org/{doi}"] if doi else []

        papers.append({
            "title": item.get("title") or item.get("display_name", ""),
            "venue": venue_name,
            "year": item.get("publication_year"),
            "authors": authors,
            "doi": doi,
            "ee": ee,
            "abstract": abstract,
            "key": openalex_id,
        })

    return papers


def dblp_search(keywords: str, venue: str | None, top: int, year_from: int) -> list[dict]:
    """调用 DBLP search API，返回论文列表（只匹配标题）。"""
    query = keywords
    if venue:
        query += f" venue:{venue}"

    params = {"q": query, "format": "json", "h": min(top, 1000), "f": 0, "c": 0}

    global _DBLP_PROXIES
    if _DBLP_PROXIES is False:
        _DBLP_PROXIES = _detect_http_proxy()
        if _DBLP_PROXIES:
            print(f"[search] 检测到可用 HTTP 代理: {list(_DBLP_PROXIES.values())[0]}")

    last_err = None
    for attempt in range(3):
        try:
            resp = requests.get(DBLP_SEARCH_URL, params=params, timeout=15,
                                headers={"User-Agent": "ccf-research-skill/1.0"},
                                proxies=_DBLP_PROXIES or {})
            resp.raise_for_status()
            data = resp.json()
            break
        except Exception as e:
            last_err = e
            if attempt < 2:
                time.sleep(2 ** attempt)
    else:
        print(f"[search] DBLP 请求失败: {last_err}，自动切换到 OpenAlex...")
        return None  # signal caller to fallback

    hits = data.get("result", {}).get("hits", {}).get("hit", []) or []
    papers = []
    for hit in hits:
        info = hit.get("info", {})
        year = info.get("year")
        try:
            year = int(year)
        except (TypeError, ValueError):
            year = None

        if year_from and (year is None or year < year_from):
            continue

        ee = info.get("ee")
        if isinstance(ee, str):
            ee = [ee]
        elif not isinstance(ee, list):
            ee = []

        authors_raw = info.get("authors", {}).get("author", [])
        if isinstance(authors_raw, dict):
            authors_raw = [authors_raw]
        authors = [a.get("text", "") if isinstance(a, dict) else str(a) for a in authors_raw]

        papers.append({
            "title": info.get("title", ""),
            "venue": info.get("venue", ""),
            "year": year,
            "authors": authors,
            "doi": info.get("doi", ""),
            "ee": ee,
            "abstract": "",
            "key": hit.get("@id", ""),
        })

    return papers


def fetch_abstracts_for_papers(papers: list[dict], tmp_dir: str, max_concurrent: int = 2) -> list[dict]:
    """
    调用 AsyncAbstractFetcher 补充摘要。
    无代理模式：并发数 2，每次请求间随机延迟 2-4 秒。
    100 篇预计耗时 10-20 分钟。
    """
    from crawler.fetch_abstract import AsyncAbstractFetcher

    os.makedirs(tmp_dir, exist_ok=True)
    tmp_file = os.path.join(tmp_dir, "batch.json")

    with open(tmp_file, "w", encoding="utf-8") as f:
        json.dump({"metadata": {"venue_name": "search", "year": 0}, "papers": papers},
                  f, ensure_ascii=False)

    async def run():
        async with AsyncAbstractFetcher(max_concurrent=max_concurrent, proxy_pool_size=0) as fetcher:
            await fetcher.driver.start()
            try:
                await fetcher.process_dir_async(tmp_dir)
            finally:
                await fetcher.driver.close()

    logging.disable(logging.WARNING)
    asyncio.run(run())
    logging.disable(logging.NOTSET)

    with open(tmp_file, encoding="utf-8") as f:
        result = json.load(f)

    return result.get("papers", papers)


def write_csv(papers: list[dict], output_path: str):
    with open(output_path, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=[
            "matched_kw", "title", "venue", "year", "authors", "doi", "url", "abstract"
        ])
        writer.writeheader()
        for p in papers:
            ee = p.get("ee") or []
            authors = p.get("authors", [])
            writer.writerow({
                "matched_kw": p.get("_matched_kw", ""),
                "title": p.get("title", ""),
                "venue": p.get("venue", ""),
                "year": p.get("year", ""),
                "authors": ", ".join(authors) if isinstance(authors, list) else str(authors),
                "doi": p.get("doi", ""),
                "url": ee[0] if ee else "",
                "abstract": (p.get("abstract") or "").replace("\n", " ").strip(),
            })


def build_json_records(papers: list[dict]) -> list[dict]:
    records = []
    for idx, p in enumerate(papers, start=1):
        ee = p.get("ee") or []
        authors = p.get("authors", [])
        content = (p.get("abstract") or "").replace("\n", " ").strip()
        records.append({
            "csv_index": idx,
            "title": p.get("title", ""),
            "content": content,
            "matched_kw": p.get("_matched_kw", ""),
            "venue": p.get("venue", ""),
            "year": p.get("year", ""),
            "authors": ", ".join(authors) if isinstance(authors, list) else str(authors),
            "doi": p.get("doi", ""),
            "url": ee[0] if ee else "",
            "paper_id": p.get("paper_id", "") or p.get("paperId", ""),
        })
    return records


def write_json(records: list[dict], output_path: str, meta: dict):
    payload = {
        "meta": meta,
        "papers": records,
    }
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)


def write_site(records: list[dict], output_path: str, meta: dict):
    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    payload = {
        "meta": meta,
        "papers": records,
    }
    payload_json = json.dumps(payload, ensure_ascii=False)
    title = escape(f"{meta.get('slug', 'papers')} Papers")
    keyword_text = escape(" / ".join(meta.get("keywords", [])))
    venue_text = escape(", ".join(meta.get("venues", [])) or "全局")
    html = f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{title}</title>
  <style>
    :root {{
      color-scheme: light;
      --bg: #f4f0e8;
      --panel: #fffaf2;
      --ink: #1f1f1f;
      --muted: #6f6455;
      --line: #d7ccb9;
      --accent: #b35c2e;
      --accent-soft: #f0dccf;
    }}
    * {{ box-sizing: border-box; }}
    body {{
      margin: 0;
      font-family: Georgia, "Noto Serif SC", serif;
      color: var(--ink);
      background:
        radial-gradient(circle at top left, #efe4d4 0, transparent 28rem),
        linear-gradient(180deg, #f7f1e7 0%, var(--bg) 100%);
    }}
    .wrap {{
      max-width: 1080px;
      margin: 0 auto;
      padding: 32px 20px 80px;
    }}
    .hero {{
      padding: 28px;
      border: 1px solid var(--line);
      background: rgba(255, 250, 242, 0.92);
      border-radius: 24px;
      box-shadow: 0 18px 40px rgba(90, 64, 32, 0.08);
    }}
    h1 {{
      margin: 0 0 10px;
      font-size: clamp(30px, 5vw, 54px);
      line-height: 1.02;
    }}
    .meta, .hint {{
      color: var(--muted);
      font-size: 14px;
      line-height: 1.6;
    }}
    .toolbar {{
      display: grid;
      grid-template-columns: 1fr auto;
      gap: 12px;
      margin: 24px 0 20px;
    }}
    input {{
      width: 100%;
      border: 1px solid var(--line);
      border-radius: 999px;
      padding: 14px 18px;
      font: inherit;
      background: rgba(255,255,255,0.85);
    }}
    .count {{
      display: inline-flex;
      align-items: center;
      justify-content: center;
      min-width: 124px;
      border-radius: 999px;
      padding: 0 16px;
      background: var(--accent);
      color: white;
      font-weight: 700;
    }}
    .filters {{
      display: flex;
      gap: 10px;
      flex-wrap: wrap;
      margin: 0 0 20px;
    }}
    .tag {{
      border: none;
      border-radius: 999px;
      background: var(--accent-soft);
      color: var(--muted);
      padding: 8px 12px;
      cursor: pointer;
      font: inherit;
    }}
    .tag.active {{
      background: var(--accent);
      color: white;
    }}
    .list {{
      display: grid;
      gap: 14px;
    }}
    .card {{
      border: 1px solid var(--line);
      border-radius: 20px;
      padding: 20px;
      background: rgba(255, 250, 242, 0.96);
      box-shadow: 0 10px 24px rgba(90, 64, 32, 0.06);
    }}
    .topbar {{
      margin-top: 18px;
      display: flex;
      gap: 12px;
      flex-wrap: wrap;
    }}
    .card-head {{
      display: flex;
      gap: 12px;
      align-items: baseline;
      flex-wrap: wrap;
      margin-bottom: 10px;
    }}
    .idx {{
      color: var(--accent);
      font-weight: 700;
      letter-spacing: 0.04em;
    }}
    .paper-title {{
      margin: 0;
      font-size: 22px;
      line-height: 1.25;
    }}
    .paper-meta {{
      color: var(--muted);
      font-size: 14px;
      margin-bottom: 12px;
      line-height: 1.7;
    }}
    .content {{
      white-space: pre-wrap;
      line-height: 1.8;
      font-size: 16px;
    }}
    .actions {{
      margin-top: 16px;
      display: flex;
      gap: 10px;
      flex-wrap: wrap;
    }}
    button.action, a.pill {{
      border: none;
      border-radius: 999px;
      padding: 10px 14px;
      font: inherit;
      cursor: pointer;
      text-decoration: none;
      background: var(--accent);
      color: white;
    }}
    button.action.secondary {{
      background: #6f6455;
    }}
    button.action.related {{
      background: #1f7a5c;
    }}
    .card.expanded {{
      border-color: #99c3b1;
      box-shadow: 0 12px 28px rgba(31, 122, 92, 0.12);
      background: linear-gradient(180deg, rgba(241, 251, 246, 0.98), rgba(255, 250, 242, 0.96));
    }}
    .toast {{
      position: fixed;
      right: 18px;
      bottom: 18px;
      max-width: min(88vw, 360px);
      padding: 14px 16px;
      border-radius: 16px;
      background: rgba(31,31,31,0.94);
      color: white;
      line-height: 1.6;
      box-shadow: 0 14px 28px rgba(0,0,0,0.2);
      opacity: 0;
      transform: translateY(16px);
      transition: opacity .22s ease, transform .22s ease;
      pointer-events: none;
    }}
    .toast.show {{
      opacity: 1;
      transform: translateY(0);
    }}
    .empty {{
      padding: 20px;
      color: var(--muted);
      border: 1px dashed var(--line);
      border-radius: 18px;
      text-align: center;
      background: rgba(255,255,255,0.5);
    }}
    a {{
      color: var(--accent);
      text-decoration: none;
    }}
    @media (max-width: 720px) {{
      .wrap {{ padding: 22px 14px 56px; }}
      .hero {{ padding: 22px 18px; }}
      .toolbar {{ grid-template-columns: 1fr; }}
      .count {{ min-height: 48px; }}
      .topbar a.pill,
      .actions button.action,
      .actions a.pill {{
        width: 100%;
        text-align: center;
      }}
      .filters {{ gap: 8px; }}
      .tag {{ width: 100%; text-align: center; }}
      .paper-title {{ font-size: 20px; }}
      .card {{ padding: 16px; }}
      .content {{ font-size: 15px; }}
    }}
  </style>
</head>
<body>
  <main class="wrap">
    <section class="hero">
      <h1>{title}</h1>
      <div class="meta">关键词：{keyword_text}</div>
      <div class="meta">范围：{venue_text}</div>
      <div class="meta">共 <span id="total-count">{len(records)}</span> 篇，支持按标题、关键词、摘要内容检索。</div>
      <div class="topbar">
        <a class="pill" href="/">返回时间线</a>
        <a class="pill" href="/library">打开 Citation 库</a>
      </div>
    </section>

    <section class="toolbar">
      <input id="q" type="search" placeholder="搜索标题、关键词、摘要内容">
      <div class="count"><span id="count">{len(records)}</span> 篇可见</div>
    </section>

    <section class="filters" id="kw-filters"></section>

    <section id="list" class="list"></section>
    <template id="empty">
      <div class="empty">没有匹配结果，试试更短的关键词。</div>
    </template>
  </main>

  <script id="papers-data" type="application/json">{payload_json}</script>
  <script>
    const payload = JSON.parse(document.getElementById('papers-data').textContent);
    const papers = payload.papers || [];
    const meta = payload.meta || {{}};
    const list = document.getElementById('list');
    const input = document.getElementById('q');
    const count = document.getElementById('count');
    const kwFilters = document.getElementById('kw-filters');
    const emptyTemplate = document.getElementById('empty');
    let expansionIndex = {{}};
    let activeMatchedKw = 'all';
    const toast = document.createElement('div');
    toast.className = 'toast';
    document.body.appendChild(toast);

    function text(v) {{
      return (v || '').toString();
    }}

    function esc(v) {{
      return text(v)
        .replaceAll('&', '&amp;')
        .replaceAll('<', '&lt;')
        .replaceAll('>', '&gt;')
        .replaceAll('"', '&quot;');
    }}

    function showToast(message) {{
      toast.textContent = message;
      toast.classList.add('show');
      clearTimeout(showToast.timer);
      showToast.timer = setTimeout(() => toast.classList.remove('show'), 2600);
    }}

    function normalizeDoi(value) {{
      let text = (value || '').toString().trim().toLowerCase();
      for (const prefix of ['https://doi.org/', 'http://doi.org/', 'doi:']) {{
        if (text.startsWith(prefix)) {{
          text = text.slice(prefix.length);
        }}
      }}
      return text.trim();
    }}

    function uniqueMatchedKeywords() {{
      const values = [];
      const seen = new Set();
      for (const paper of papers) {{
        const kw = text(paper.matched_kw).trim();
        if (!kw) continue;
        const key = kw.toLowerCase();
        if (seen.has(key)) continue;
        seen.add(key);
        values.push(kw);
      }}
      return values;
    }}

    async function apiPost(path, body) {{
      const resp = await fetch(path, {{
        method: 'POST',
        credentials: 'same-origin',
        headers: {{ 'Content-Type': 'application/json' }},
        body: JSON.stringify(body)
      }});
      const data = await resp.json().catch(() => ({{ ok: false, error: '请求失败' }}));
      if (!resp.ok || data.ok === false) {{
        throw new Error(data.error || '请求失败');
      }}
      return data;
    }}

    async function addCitation(index) {{
      const paper = papers.find((item) => item.csv_index === index);
      if (!paper) return;
      try {{
        const data = await apiPost('/api/citations', {{
          search_slug: meta.slug || '',
          paper
        }});
        showToast(data.message || '已加入 Citation 库。');
      }} catch (error) {{
        showToast(error.message);
      }}
    }}

    async function expandReferences(index) {{
      const paper = papers.find((item) => item.csv_index === index);
      if (!paper) return;
      const doi = normalizeDoi(paper.doi);
      if (doi && expansionIndex[doi] && expansionIndex[doi].site_url) {{
        showToast('已找到现有相关论文页，正在打开...');
        window.open(expansionIndex[doi].site_url, '_blank', 'noopener');
        return;
      }}
      showToast('正在执行延展搜索，请稍等...');
      try {{
        const data = await apiPost('/api/papers/expand-references', {{
          search_slug: meta.slug || '',
          paper
        }});
        if (doi && data.site_url) {{
          expansionIndex[doi] = {{ site_url: data.site_url }};
        }}
        showToast('延展搜索页面已生成，正在打开...');
        render(input.value);
        window.open(data.site_url, '_blank', 'noopener');
      }} catch (error) {{
        showToast(error.message);
      }}
    }}

    window.addCitation = addCitation;
    window.expandReferences = expandReferences;

    function cardHtml(paper) {{
      const lineMeta = [
        paper.matched_kw ? `CSV #${{paper.csv_index}} · 命中词：${{paper.matched_kw}}` : `CSV #${{paper.csv_index}}`,
        paper.venue || '',
        paper.year || '',
        paper.authors || ''
      ].filter(Boolean).join(' · ');
      const link = paper.url ? `<a href="${{esc(paper.url)}}" target="_blank" rel="noreferrer">原文链接</a>` : '';
      const doi = paper.doi ? `DOI: ${{esc(paper.doi)}}` : '';
      const normalizedDoi = normalizeDoi(paper.doi);
      const expansion = normalizedDoi ? expansionIndex[normalizedDoi] : null;
      const expanded = Boolean(expansion && expansion.site_url);
      const expandLabel = expanded ? '查看相关论文' : '延展搜索';
      const expandClass = expanded ? 'action secondary related' : 'action secondary';
      return `
        <article class="card${{expanded ? ' expanded' : ''}}">
          <div class="card-head">
            <div class="idx">#${{paper.csv_index}}</div>
            <h2 class="paper-title">${{esc(paper.title)}}</h2>
          </div>
          <div class="paper-meta">${{esc(lineMeta)}}</div>
          <div class="paper-meta">${{[doi, link].filter(Boolean).join(' · ')}}</div>
          <div class="content">${{esc(paper.content || '暂无内容')}}</div>
          <div class="actions">
            <button class="action" type="button" onclick="addCitation(${{paper.csv_index}})">加入 Citation 库</button>
            <button class="${{expandClass}}" type="button" onclick="expandReferences(${{paper.csv_index}})">${{expandLabel}}</button>
          </div>
        </article>
      `;
    }}

    function renderKeywordFilters() {{
      const values = uniqueMatchedKeywords();
      if (!kwFilters) return;
      if (!values.length) {{
        kwFilters.innerHTML = '';
        return;
      }}
      kwFilters.innerHTML = [
        `<button class="tag${{activeMatchedKw === 'all' ? ' active' : ''}}" type="button" data-matched-filter="all">全部命中词</button>`,
        ...values.map((kw) => `<button class="tag${{activeMatchedKw === kw.toLowerCase() ? ' active' : ''}}" type="button" data-matched-filter="${{esc(kw.toLowerCase())}}">${{esc(kw)}}</button>`)
      ].join('');
      kwFilters.querySelectorAll('[data-matched-filter]').forEach((button) => {{
        button.addEventListener('click', () => {{
          activeMatchedKw = button.dataset.matchedFilter || 'all';
          renderKeywordFilters();
          render(input.value);
        }});
      }});
    }}

    function render(query = '') {{
      const q = query.trim().toLowerCase();
      const byText = !q ? papers : papers.filter((paper) => {{
        const haystack = [
          paper.title,
          paper.content,
          paper.matched_kw,
          paper.venue,
          paper.authors
        ].join('\\n').toLowerCase();
        return haystack.includes(q);
      }});
      const filtered = byText.filter((paper) => activeMatchedKw === 'all' || text(paper.matched_kw).trim().toLowerCase() === activeMatchedKw);

      count.textContent = filtered.length;
      if (!filtered.length) {{
        list.innerHTML = '';
        list.appendChild(emptyTemplate.content.cloneNode(true));
        return;
      }}
      list.innerHTML = filtered.map(cardHtml).join('');
    }}

    async function loadExpansions() {{
      try {{
        const resp = await fetch('/api/expansions', {{ credentials: 'same-origin' }});
        const data = await resp.json().catch(() => ({{ ok: false }}));
        if (!resp.ok || data.ok === false) {{
          return;
        }}
        expansionIndex = data.items || {{}};
      }} catch (error) {{
        expansionIndex = {{}};
      }}
    }}

    input.addEventListener('input', (event) => render(event.target.value));
    renderKeywordFilters();
    loadExpansions().finally(() => render());
  </script>
</body>
</html>
"""
    with open(output_path, "w", encoding="utf-8") as f:
        f.write(html)


def build_site_url(out_dir: str, site_path: str) -> str:
    base_url = (os.getenv("PUBLIC_SITE_BASE_URL") or "").strip().rstrip("/")
    if not base_url:
        host = (os.getenv("PUBLIC_SITE_HOST") or "").strip()
        port = (os.getenv("PUBLIC_SITE_PORT") or "").strip()
        if host and port:
            base_url = f"http://{host}:{port}"
        elif host:
            base_url = f"http://{host}"

    if base_url:
        relative_dir = os.path.relpath(out_dir, os.path.join(ROOT_DIR, "data"))
        relative_dir = quote(relative_dir.replace(os.sep, "/"))
        return f"{base_url}/{relative_dir}/site/"

    return Path(site_path).resolve().as_uri()


def main():
    parser = argparse.ArgumentParser(description="DBLP 关键词搜索 → CSV")
    parser.add_argument("--keywords", required=True,
                        help="关键词，多组用分号分隔。示例: 'physiological notification;biosignal alert'")
    parser.add_argument("--venues", default="",
                        help="逗号分隔的会议缩写，空则全 DBLP 范围搜索")
    parser.add_argument("--slug", required=True,
                        help="话题简称，用于命名输出目录，仅限英文/数字/连字符。示例: physio-ui")
    parser.add_argument("--top", type=int, default=100,
                        help="每组关键词 × 每个 venue 各取最多 N 篇，合并去重（默认 100）")
    parser.add_argument("--year-from", type=int, default=0,
                        help="最早年份，0 表示不限")
    parser.add_argument("--no-abstract", action="store_true",
                        help="跳过摘要爬取（默认会爬取摘要）")
    args = parser.parse_args()

    # 输出目录：data/searches/YYYY-MM-DD_<slug>/
    out_dir = os.path.join(ROOT_DIR, "data", "searches",
                           f"{date.today().isoformat()}_{args.slug}")
    os.makedirs(out_dir, exist_ok=True)
    csv_path = os.path.join(out_dir, "papers.csv")
    json_path = os.path.join(out_dir, "papers.json")
    meta_path = os.path.join(out_dir, "search.json")
    site_path = os.path.join(out_dir, "site", "index.html")
    tmp_dir = os.path.join(ROOT_DIR, "data", "tmp_search")

    venues = [v.strip() for v in args.venues.split(",") if v.strip()] if args.venues else [None]
    keyword_groups = [k.strip() for k in args.keywords.split(";") if k.strip()]

    # 保存搜索参数
    with open(meta_path, "w", encoding="utf-8") as f:
        json.dump({
            "slug": args.slug,
            "keywords": keyword_groups,
            "venues": [v for v in venues if v],
            "top_per_group": args.top,
            "year_from": args.year_from,
            "fetch_abstract": not args.no_abstract,
            "date": date.today().isoformat(),
        }, f, ensure_ascii=False, indent=2)

    # 搜索
    all_papers: list[dict] = []
    seen_keys: set[str] = set()
    hit_counts: dict[str, int] = {}

    dblp_ok = True  # track whether DBLP is reachable
    for kw in keyword_groups:
        kw_hits = 0
        for venue in venues:
            label = venue or "全局"
            print(f"[search] '{kw}'  venue={label} ...")
            if dblp_ok:
                papers = dblp_search(kw, venue, args.top, args.year_from)
                if papers is None:
                    dblp_ok = False
                    print("[search] DBLP 不可用，切换到 OpenAlex 搜索引擎")
            if not dblp_ok:
                papers = openalex_search(kw, venue, args.top, args.year_from)
            new = 0
            for p in papers:
                key = p.get("key") or p.get("title", "")
                if key not in seen_keys:
                    seen_keys.add(key)
                    p["_matched_kw"] = kw
                    all_papers.append(p)
                    new += 1
            kw_hits += new
            print(f"[search]   命中 {len(papers)} 篇，新增 {new} 篇（去重后）")
            if len(venues) > 1 or len(keyword_groups) > 1:
                time.sleep(random.uniform(1.0, 2.0))
        hit_counts[kw] = kw_hits

    all_papers.sort(key=lambda p: -(p.get("year") or 0))
    total = len(all_papers)
    print(f"\n[search] 合并去重后共 {total} 篇")
    for kw, n in hit_counts.items():
        print(f"  '{kw}': {n} 篇")

    # 摘要
    if not args.no_abstract:
        has_abstract = sum(1 for p in all_papers if p.get("abstract"))
        need = total - has_abstract
        if need > 0:
            est_min = round(need * 3 / 60)
            print(f"\n[search] 开始爬取摘要（{need} 篇，预计约 {est_min} 分钟）...")
            all_papers = fetch_abstracts_for_papers(all_papers, tmp_dir)
            fetched = sum(1 for p in all_papers if p.get("abstract"))
            print(f"[search] 摘要爬取完成：{fetched}/{total} 篇获得摘要")
        else:
            print("[search] 所有论文已有摘要，跳过爬取")
    else:
        print("[search] 已跳过摘要爬取（--no-abstract）")

    output_meta = {
        "slug": args.slug,
        "keywords": keyword_groups,
        "venues": [v for v in venues if v],
        "top_per_group": args.top,
        "year_from": args.year_from,
        "fetch_abstract": not args.no_abstract,
        "date": date.today().isoformat(),
        "total_papers": total,
    }

    write_csv(all_papers, csv_path)
    json_records = build_json_records(all_papers)
    write_json(json_records, json_path, output_meta)
    write_site(json_records, site_path, output_meta)
    site_url = build_site_url(out_dir, site_path)
    print(f"\n[search] 完成。输出目录: {out_dir}")
    print(f"  papers.csv : {csv_path}")
    print(f"  papers.json: {json_path}")
    print(f"  search.json: {meta_path}")
    print(f"  site.html  : {site_path}")
    print(f"  site_url   : {site_url}")


if __name__ == "__main__":
    main()
