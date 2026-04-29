"""HTML page builders for the exScholar site."""

from html import unescape as html_unescape

from ..core import *


# HTML page builders
def build_timeline_html():
    entries = list_search_entries()
    original_items_html = []
    expansion_items_html = []
    expansion_tags = []
    seen_expansion_tags = set()

    def render_entry(entry: dict) -> str:
        keywords = " / ".join(entry["keywords"]) if entry["keywords"] else "未记录关键词"
        venues = ", ".join(entry["venues"]) if entry["venues"] else "全局"
        papers_text = f'{entry["total_papers"]} 篇' if entry["total_papers"] is not None else "篇数未知"
        abstract_text = "含摘要" if entry["fetch_abstract"] else "未抓摘要"
        source_paper = entry.get("source_paper") or {}
        is_expansion = bool(entry.get("is_expansion"))
        title = html_unescape(entry.get("title") or source_paper.get("title") or keywords)
        default_title = html_unescape(entry.get("default_title") or source_paper.get("title") or keywords)
        detail_parts = [papers_text, abstract_text]
        if is_expansion:
            source_slug = source_paper.get("source_slug") or "未知来源"
            expansion_source = entry.get("expansion_source") or "related-search"
            source_kw = entry.get("source_matched_kw") or "未记录命中词"
            detail_parts.extend([f"来源：{source_slug}", f"方式：{expansion_source}"])
            detail_parts.append(f"命中词：{source_kw}")
        else:
            detail_parts.append(f"范围：{venues}")
        primary_href = entry["site_url"] or entry["search_url"] or "#"
        links = [
            f'<a href="{entry["site_url"]}">结果网页</a>' if entry["site_url"] else "",
            f'<a href="{entry["csv_url"]}">CSV</a>' if entry["csv_url"] else "",
        ]
        edit_button = f'<button class="edit-search-entry" type="button" data-relative-dir="{entry["relative_dir"]}">编辑标题</button>'
        delete_button = f'<button class="delete-search-entry danger" type="button" data-relative-dir="{entry["relative_dir"]}">删除这次搜索</button>'
        subtitle = f'<div class="meta">{keywords}</div>' if is_expansion and keywords else ""
        safe_title = escape(title)
        title_html = (
            f'<a class="title-link entry-title-text" href="{primary_href}">{safe_title}</a>'
            if primary_href != "#"
            else f'<span class="title-link entry-title-text">{safe_title}</span>'
        )
        return f"""
        <article class="entry" data-matched-kw="{entry.get("source_matched_kw", "").lower()}" data-relative-dir="{entry["relative_dir"]}" data-entry-title="{escape(title)}" data-default-title="{escape(default_title)}">
          <div class="dot"></div>
          <div class="card">
            <div class="row">
              <div class="date">{entry["date"]}</div>
              <div class="slug">{entry["slug"]}</div>
            </div>
            <div class="meta">{' · '.join(detail_parts)}</div>
            <h2>{title_html}</h2>
            {subtitle}
            <div class="links">{' '.join(link for link in links if link)} {edit_button} {delete_button}</div>
          </div>
        </article>
        """

    for entry in entries:
        if entry.get("is_expansion"):
            source_kw = (entry.get("source_matched_kw") or "").strip()
            if source_kw and source_kw.lower() not in seen_expansion_tags:
                seen_expansion_tags.add(source_kw.lower())
                expansion_tags.append(source_kw)
            expansion_items_html.append(render_entry(entry))
        else:
            original_items_html.append(render_entry(entry))

    original_body = "\n".join(original_items_html) if original_items_html else '<div class="empty">还没有原始搜索结果。</div>'
    expansion_body = "\n".join(expansion_items_html) if expansion_items_html else '<div class="empty">还没有延展搜索结果。</div>'
    active_user = current_username()
    auth_text = f"当前用户：{active_user}" if active_user else ("已启用密码保护" if require_password() else "未启用密码保护")
    return f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Search Timeline</title>
  <style>
    :root {{
      --bg: #f2efe8;
      --panel: rgba(255, 251, 244, 0.94);
      --ink: #1e1d1a;
      --muted: #6f685c;
      --line: #d5cbba;
      --accent: #9c4f2f;
      --accent-soft: #ead8ca;
    }}
    * {{ box-sizing: border-box; }}
    body {{
      margin: 0;
      color: var(--ink);
      font-family: Georgia, "Noto Serif SC", serif;
      background:
        linear-gradient(180deg, rgba(255,255,255,0.5), transparent 30%),
        radial-gradient(circle at top left, #ece1d0 0, transparent 28rem),
        var(--bg);
    }}
    .wrap {{ max-width: 1100px; margin: 0 auto; padding: 28px 18px 72px; }}
    .hero {{
      border: 1px solid var(--line);
      background: var(--panel);
      border-radius: 28px;
      padding: 28px;
      box-shadow: 0 18px 40px rgba(76, 50, 28, 0.08);
    }}
    .hero-bar {{ display:flex; justify-content:space-between; gap:12px; flex-wrap:wrap; align-items:flex-start; }}
    h1 {{ margin: 0 0 8px; font-size: clamp(30px, 5vw, 56px); line-height: 1; }}
    .sub {{ color: var(--muted); line-height: 1.7; font-size: 15px; }}
    .hero-links {{ display:flex; gap:10px; flex-wrap:wrap; }}
    .hero-links a, .hero-links button {{
      color:white; background:var(--accent); text-decoration:none; padding:10px 14px;
      border:none; border-radius:999px; font:inherit; cursor:pointer;
    }}
    .section {{ margin-top: 26px; }}
    .section-title {{ margin: 0 0 12px; font-size: 28px; }}
    .research-panel {{
      margin-top: 22px; border: 1px solid var(--line); background: var(--panel);
      border-radius: 24px; padding: 20px; box-shadow: 0 12px 28px rgba(76, 50, 28, 0.06);
    }}
    .research-panel textarea {{
      width: 100%; min-height: 120px; resize: vertical; border: 1px solid var(--line);
      border-radius: 18px; padding: 14px 16px; font: inherit; background: rgba(255,255,255,0.85);
      color: var(--ink);
    }}
    .research-editor {{
      margin-top: 14px; border: 1px solid var(--line); border-radius: 18px; padding: 14px;
      background: rgba(255,255,255,0.48);
    }}
    .research-editor-grid {{
      display:grid; grid-template-columns:repeat(auto-fit, minmax(180px, 1fr)); gap:10px; margin-top:10px;
    }}
    .research-editor input, .research-editor textarea {{
      width:100%; border:1px solid var(--line); border-radius:14px; padding:10px 12px; font:inherit; background:white; color:var(--ink);
    }}
    .research-editor label {{ display:block; color:var(--muted); font-size:14px; }}
    .research-editor .checkbox {{ display:flex; align-items:center; gap:8px; margin-top:8px; color:var(--muted); }}
    .research-actions {{ display:flex; gap:10px; flex-wrap:wrap; margin-top:12px; }}
    .research-actions button {{
      color:white; background:var(--accent); text-decoration:none; padding:10px 14px;
      border:none; border-radius:999px; font:inherit; cursor:pointer;
    }}
    .research-actions button:disabled {{
      background: #cfc7bb;
      color: #8c8375;
      cursor: not-allowed;
      box-shadow: none;
      opacity: 1;
    }}
    .research-actions button.busy {{
      background: #c8733f;
      box-shadow: 0 0 0 4px rgba(200, 115, 63, 0.14);
    }}
    .research-actions button.success {{
      background: #2f7d57;
      box-shadow: 0 0 0 4px rgba(47, 125, 87, 0.14);
    }}
    .research-status, .research-job {{
      margin-top: 14px; border: 1px solid var(--line); border-radius: 18px; padding: 14px;
      background: rgba(255,255,255,0.55);
    }}
    .research-progress {{
      margin-top: 10px;
    }}
    .research-progress-row {{
      display:flex;
      justify-content:space-between;
      gap:12px;
      align-items:center;
      color: var(--muted);
      font-size: 14px;
    }}
    .research-progress-track {{
      margin-top: 8px;
      width: 100%;
      height: 10px;
      border-radius: 999px;
      background: #ead8ca;
      overflow: hidden;
    }}
    .research-progress-bar {{
      height: 100%;
      width: 0%;
      background: linear-gradient(90deg, #c8733f, #9c4f2f);
      transition: width .25s ease;
    }}
    .guide {{
      margin-top: 12px;
      padding: 14px 16px;
      border: 1px solid var(--line);
      border-radius: 18px;
      background: rgba(255,255,255,0.52);
      color: var(--muted);
      line-height: 1.7;
      font-size: 14px;
    }}
    .guide strong {{ color: var(--ink); }}
    .research-job-links {{ display:flex; gap:10px; flex-wrap:wrap; margin-top:10px; }}
    .research-job-links a {{
      color:white; background:var(--accent); text-decoration:none; padding:8px 12px; border-radius:999px;
    }}
    .danger-btn {{
      color: white;
      background: #9c3d33;
      border: none;
      border-radius: 999px;
      padding: 8px 12px;
      font: inherit;
      cursor: pointer;
    }}
    .filters {{ display:flex; gap:10px; flex-wrap:wrap; margin: 10px 0 0; }}
    .filters .tag {{
      border:none; border-radius:999px; background:var(--accent-soft); color:var(--muted);
      padding:8px 12px; cursor:pointer; font:inherit;
    }}
    .filters .tag.active {{ background: var(--accent); color: white; }}
    .timeline {{ position: relative; margin-top: 26px; padding-left: 26px; }}
    .timeline::before {{
      content: ""; position: absolute; left: 9px; top: 8px; bottom: 8px; width: 2px;
      background: linear-gradient(180deg, var(--accent), #c8b09c);
    }}
    .entry {{ position: relative; margin: 0 0 18px; }}
    .dot {{
      position: absolute; left: -1px; top: 20px; width: 20px; height: 20px; border-radius: 999px;
      border: 2px solid var(--accent); background: var(--accent-soft);
      box-shadow: 0 0 0 5px rgba(156, 79, 47, 0.08);
    }}
    .card {{
      margin-left: 28px; border: 1px solid var(--line); background: var(--panel); border-radius: 22px;
      padding: 18px 18px 16px; box-shadow: 0 10px 24px rgba(76, 50, 28, 0.06);
    }}
    .row {{ display: flex; gap: 12px; flex-wrap: wrap; align-items: baseline; }}
    .date {{ color: var(--accent); font-weight: 700; letter-spacing: 0.04em; }}
    .slug {{ color: var(--muted); font-size: 14px; }}
    h2 {{ margin: 10px 0 8px; font-size: 23px; line-height: 1.28; }}
    .title-link {{
      color: var(--ink);
      background: none;
      padding: 0;
      border-radius: 0;
      text-decoration: none;
    }}
    .title-link:hover {{ color: var(--accent); }}
    .meta {{ color: var(--muted); font-size: 14px; line-height: 1.7; }}
    .links {{ margin-top: 14px; display: flex; gap: 12px; flex-wrap: wrap; }}
    a {{ color: white; background: var(--accent); text-decoration: none; padding: 10px 14px; border-radius: 999px; }}
    .links button {{
      color: white;
      background: var(--accent);
      padding: 10px 14px;
      border-radius: 999px;
      border: none;
      font: inherit;
      cursor: pointer;
    }}
    .links button.danger {{ background: #9c3d33; }}
    .empty {{
      border: 1px dashed var(--line); border-radius: 18px; background: rgba(255,255,255,0.55);
      text-align: center; padding: 24px; color: var(--muted);
    }}
    @media (max-width: 720px) {{
      .wrap {{ padding: 22px 14px 56px; }}
      .hero {{ padding: 22px 18px; }}
      .hero-links {{ width: 100%; }}
      .hero-links a, .hero-links button {{ width: 100%; text-align: center; }}
      .section-title {{ font-size: 24px; }}
      .filters .tag {{ width: 100%; text-align: center; }}
      .timeline {{ padding-left: 18px; }}
      .card {{ margin-left: 20px; }}
      h2 {{ font-size: 20px; }}
    }}
  </style>
</head>
<body>
  <main class="wrap">
    <section class="hero">
      <div class="hero-bar">
        <div>
          <h1>Search Timeline</h1>
          <div class="sub">这里汇总了每一次论文搜索结果。按时间倒序排列，优先进入对应搜索的网站页，也可以直接打开 CSV、JSON 和原始参数。</div>
          <div class="sub">站点状态：{auth_text}</div>
        </div>
        <div class="hero-links">
          <a href="/keywords">Keywords</a>
          <a href="/reading">深度阅读</a>
          <button id="logout-btn" type="button">退出登录</button>
        </div>
      </div>
    </section>
    <section class="research-panel">
      <h2 class="section-title">用自然语言找论文</h2>
      <div class="sub">像和助手聊天一样描述你要找的论文方向即可。系统会先帮你整理成搜索方案，你确认后再正式开始搜索。</div>
      <div class="guide">
        <strong>新手用法：</strong><br>
        1. 先在下面输入一句需求，例如“帮我找 2022 年以来 HCI 里关于 pet robot 的论文”。<br>
        2. 点“生成方案”，系统会自动给出关键词、会议和年份。<br>
        3. 如果方案看起来没问题，再点“开始搜索”；如果想改，继续在同一个输入框里写“只保留 CHI/UbiComp”这类修改即可。
      </div>
      <textarea id="research-prompt" placeholder="例如：帮我找 2022 年以来 HCI 里关于 companion robot / pet robot 的论文"></textarea>
      <div class="research-actions">
        <button id="research-compose" type="button">1. 生成方案</button>
        <button id="research-submit" type="button">2. 开始搜索</button>
      </div>
      <div id="research-editor" class="research-editor" style="display:none;">
        <div class="sub">如果你会改参数，也可以直接手动编辑；系统会在真正开始搜索前再次帮你检查这份方案。</div>
        <div class="research-editor-grid">
          <label>Slug
            <input id="research-edit-slug" type="text" placeholder="research-slug">
          </label>
          <label>Year From
            <input id="research-edit-year-from" type="number" placeholder="0">
          </label>
          <label>Top
            <input id="research-edit-top" type="number" placeholder="100">
          </label>
          <label>Venues
            <input id="research-edit-venues" type="text" placeholder="chi,uist,cscw">
          </label>
        </div>
        <label style="display:block; margin-top:10px;">Keywords
          <textarea id="research-edit-keywords" placeholder="每行一组英文关键词"></textarea>
        </label>
        <label style="display:block; margin-top:10px;">Notes
          <textarea id="research-edit-notes" placeholder="可选备注"></textarea>
        </label>
        <label class="checkbox">
          <input id="research-edit-fetch-abstract" type="checkbox" checked>
          <span>抓取摘要</span>
        </label>
      </div>
      <div id="research-status" class="research-status" style="display:none;"></div>
      <div id="research-jobs" class="research-jobs"></div>
    </section>
    <section class="section">
      <h2 class="section-title">原始搜索</h2>
      <div class="timeline">{original_body}</div>
    </section>
    <section class="section">
      <h2 class="section-title">延展搜索</h2>
      <div class="filters" id="expansion-filters">
        <button class="tag active" type="button" data-expansion-filter="all">全部</button>
        {"".join(f'<button class="tag" type="button" data-expansion-filter="{tag.lower()}">{tag}</button>' for tag in expansion_tags)}
      </div>
      <div class="timeline" id="expansion-timeline">{expansion_body}</div>
    </section>
  </main>
  <script>
    const btn = document.getElementById('logout-btn');
    if (btn) {{
      btn.addEventListener('click', async () => {{
        await fetch('/api/auth/logout', {{ method: 'POST', credentials: 'same-origin' }});
        window.location.href = '/login';
      }});
    }}
    const expansionFilterButtons = Array.from(document.querySelectorAll('[data-expansion-filter]'));
    const expansionEntries = Array.from(document.querySelectorAll('#expansion-timeline .entry[data-matched-kw]'));
    const researchPrompt = document.getElementById('research-prompt');
    const researchCompose = document.getElementById('research-compose');
    const researchEditor = document.getElementById('research-editor');
    const researchEditSlug = document.getElementById('research-edit-slug');
    const researchEditYearFrom = document.getElementById('research-edit-year-from');
    const researchEditTop = document.getElementById('research-edit-top');
    const researchEditVenues = document.getElementById('research-edit-venues');
    const researchEditKeywords = document.getElementById('research-edit-keywords');
    const researchEditNotes = document.getElementById('research-edit-notes');
    const researchEditFetchAbstract = document.getElementById('research-edit-fetch-abstract');
    const researchSubmit = document.getElementById('research-submit');
    const researchStatus = document.getElementById('research-status');
    const researchJobs = document.getElementById('research-jobs');
    let activeResearchJobId = '';
    let previewResearchPlan = null;
    let previewResearchPrompt = '';
    let previewResearchPlanDirty = false;

    function syncResearchSubmitState() {{
      if (!researchSubmit) return;
      researchSubmit.disabled = !previewResearchPlan;
    }}

    function esc(value) {{
      return (value || '').toString()
        .replaceAll('&', '&amp;')
        .replaceAll('<', '&lt;')
        .replaceAll('>', '&gt;')
        .replaceAll('"', '&quot;');
    }}

    function text(value) {{
      if (value === null || value === undefined) return '';
      return String(value);
    }}

    async function updateTimelineTitle(relativeDir, title) {{
      const resp = await fetch('/api/search-entries/title', {{
        method: 'POST',
        credentials: 'same-origin',
        headers: {{ 'Content-Type': 'application/json' }},
        body: JSON.stringify({{ relative_dir: relativeDir, title }})
      }});
      return parseApiJson(resp, '更新标题失败');
    }}

    async function parseApiJson(resp, fallbackError) {{
      const data = await resp.json().catch(() => ({{ ok: false, error: fallbackError || '请求失败' }}));
      if (resp.status === 401) {{
        const message = '登录状态已失效，请刷新页面后重新登录。';
        alert(message);
        window.location.href = '/login';
        throw new Error(message);
      }}
      if (!resp.ok || data.ok === false) {{
        throw new Error(data.error || fallbackError || '请求失败');
      }}
      return data;
    }}

    function setActionButtonState(button, phase) {{
      if (!button) return;
      button.classList.remove('busy', 'success');
      if (phase === 'busy') button.classList.add('busy');
      if (phase === 'success') button.classList.add('success');
    }}

    function researchProgressState(job) {{
      const status = (job && job.status || '').toString();
      const step = (job && job.current_step || '').toString();
      const stepMessage = (job && job.step_message || '').toString();
      const progressPayload = (job && job.progress) || {{}};
      const reviewSummary = (job && job.review_summary) || {{}};
      if (status === 'completed') {{
        return {{
          percent: 100,
          label: '已完成',
          detail: `high ${{Number(reviewSummary.high || 0)}} / medium ${{Number(reviewSummary.medium || 0)}} / low ${{Number(reviewSummary.low || 0)}}`
        }};
      }}
      if (status === 'failed') {{
        return {{ percent: 100, label: '失败', detail: '任务已停止' }};
      }}
      if (status === 'preview') {{
        return {{ percent: 20, label: '方案预览', detail: '等待确认并启动搜索' }};
      }}
      const mapping = {{
        queued: [5, '排队中', '等待 research 并发槽位'],
        planning: [15, '规划中', '正在生成 research 搜索方案'],
        planned: [25, '方案完成', '准备启动搜索'],
        searching: [45, '搜索中', '正在召回与去重'],
        search_completed: [60, '搜索完成', '准备摘要或导出'],
        fetching_abstracts: [75, '抓取摘要', '正在补摘要内容'],
        abstracts_completed: [85, '摘要完成', '准备整理输出'],
        abstracts_skipped: [82, '跳过摘要', '准备整理输出'],
        writing_outputs: [92, '整理输出', '正在写入 CSV / JSON / site'],
        reviewing_results: [97, '复核结果', '正在做相关性判断和 autotag'],
      }};
      if (step === 'reviewing_results') {{
        const currentBatch = Number(progressPayload.review_current_batch || 0);
        const totalBatches = Number(progressPayload.review_total_batches || 0);
        const reviewedPapers = Number(progressPayload.review_reviewed_papers || 0);
        const totalReviewPapers = Number(progressPayload.review_total_papers || job.total_papers || 0);
        const modelReviewedTotal = Number(progressPayload.review_model_reviewed_total || 0);
        const heuristicReviewedTotal = Number(progressPayload.review_heuristic_reviewed_total || 0);
        if (currentBatch > 0 && totalBatches > 0) {{
          const ratio = Math.min(1, currentBatch / totalBatches);
          const percent = Math.max(93, Math.min(99, Math.round(92 + ratio * 7)));
          const paperProgress = totalReviewPapers > 0
            ? `已复核 ${{reviewedPapers}} / ${{totalReviewPapers}} 篇`
            : `已完成第 ${{currentBatch}} / ${{totalBatches}} 批`;
          const reviewModes = `模型复核 ${{modelReviewedTotal}} 篇，启发式兜底 ${{heuristicReviewedTotal}} 篇`;
          return {{
            percent,
            label: '复核结果',
            detail: `${{paperProgress}} · ${{reviewModes}} · 第 ${{currentBatch}}/${{totalBatches}} 批`
          }};
        }}
        return {{
          percent: 93,
          label: '复核结果',
          detail: '正在启动逐批复核，并写回相关性与 autotag'
        }};
      }}
      if (step === 'searching') {{
        const discovered = Number(progressPayload.discovered_papers || 0);
        const currentHit = Number(progressPayload.current_hit_count || 0);
        const currentAdded = Number(progressPayload.current_added_count || 0);
        const currentKeyword = text(progressPayload.current_keyword).trim();
        const currentVenue = text(progressPayload.current_venue).trim();
        const scope = [currentKeyword, currentVenue].filter(Boolean).join(' / ');
        const detailBase = discovered > 0
          ? `当前已找到 ${{discovered}} 篇（最近一轮命中 ${{currentHit}} 篇，新增 ${{currentAdded}} 篇）`
          : '正在召回与去重';
        const detail = scope ? `${{detailBase}} · 当前检索：${{scope}}` : detailBase;
        return {{ percent: 45, label: '搜索中', detail }};
      }}
      const matched = mapping[step] || mapping[status] || [12, '处理中', '任务正在运行'];
      return {{ percent: matched[0], label: matched[1], detail: matched[2] }};
    }}

    function renderResearchStatus(job) {{
      if (!researchStatus) return;
      if (!job) {{
        researchStatus.style.display = 'none';
        researchStatus.innerHTML = '';
        return;
      }}
      const plan = job.plan || {{}};
      const suggestion = plan.query_suggestion || {{}};
      const diagnostics = job.diagnostics || plan.diagnostics || {{}};
      const progress = researchProgressState(job);
      const hasKeywords = Array.isArray(plan.keywords) && plan.keywords.length > 0;
      const hasSlug = Boolean((plan.slug || '').toString().trim());
      const hasPlan = hasKeywords || hasSlug;
      const links = [];
      if (job.site_relative_url) links.push(`<a href="${{job.site_relative_url}}" target="_blank" rel="noreferrer">结果网页</a>`);
      if (job.site_url && job.site_url !== job.site_relative_url) links.push(`<a href="${{job.site_url}}" target="_blank" rel="noreferrer">绝对链接</a>`);
      if (job.csv_url) links.push(`<a href="${{job.csv_url}}" target="_blank" rel="noreferrer">CSV</a>`);
      if (job.json_url) links.push(`<a href="${{job.json_url}}" target="_blank" rel="noreferrer">JSON</a>`);
      researchStatus.style.display = 'block';
      researchStatus.innerHTML = `
        <div><strong>状态：</strong>${{esc(job.status || '')}}</div>
        <div style="margin-top:6px;"><strong>当前步骤：</strong>${{esc(job.step_message || '')}}</div>
        <div class="research-progress">
          <div class="research-progress-row">
            <span>${{esc(progress.label)}}</span>
            <span>${{progress.percent}}%</span>
          </div>
          <div class="research-progress-track">
            <div class="research-progress-bar" style="width:${{progress.percent}}%;"></div>
          </div>
          <div class="meta" style="margin-top:8px;">${{esc(progress.detail)}}</div>
        </div>
        ${{suggestion.candidate_keywords && suggestion.candidate_keywords.length ? `<div style="margin-top:6px;"><strong>智能建议检索词：</strong>${{esc((suggestion.candidate_keywords || []).join(' ; '))}}</div>` : ''}}
        ${{suggestion.avoid_keywords && suggestion.avoid_keywords.length ? `<div style="margin-top:6px;"><strong>建议避免：</strong>${{esc((suggestion.avoid_keywords || []).join(' ; '))}}</div>` : ''}}
        ${{diagnostics.plan_generation_mode ? `<div style="margin-top:6px;"><strong>规划诊断：</strong>plan=${{esc(diagnostics.plan_generation_mode)}}${{diagnostics.query_suggestion_mode ? ` · suggestion=${{esc(diagnostics.query_suggestion_mode)}}` : ''}}${{diagnostics.fallback_reason ? ` · reason=${{esc(diagnostics.fallback_reason)}}` : ''}}</div>` : ''}}
        ${{hasPlan ? `<div style="margin-top:6px;"><strong>方案：</strong>${{esc((plan.keywords || []).join(' ; '))}}${{plan.venues && plan.venues.length ? ` | venues: ${{esc(plan.venues.join(', '))}}` : ''}}</div>` : ''}}
        ${{hasPlan ? `<div style="margin-top:6px;"><strong>slug：</strong>${{esc(plan.slug || '')}}${{plan.year_from ? ` | year_from: ${{esc(plan.year_from)}}` : ''}}</div>` : ''}}
        ${{links.length ? `<div class="research-job-links">${{links.join('')}}</div>` : ''}}
      `;
    }}

    function renderResearchPreview(plan, prompt, message) {{
      previewResearchPlan = plan || null;
      previewResearchPrompt = prompt || '';
      previewResearchPlanDirty = false;
      if (researchEditor) researchEditor.style.display = previewResearchPlan ? 'block' : 'none';
      syncResearchSubmitState();
      if (previewResearchPlan) {{
        if (researchEditSlug) researchEditSlug.value = previewResearchPlan.slug || '';
        if (researchEditYearFrom) researchEditYearFrom.value = previewResearchPlan.year_from || 0;
        if (researchEditTop) researchEditTop.value = previewResearchPlan.top || 100;
        if (researchEditVenues) researchEditVenues.value = (previewResearchPlan.venues || []).join(',');
        if (researchEditKeywords) researchEditKeywords.value = (previewResearchPlan.keywords || []).join('\\n');
        if (researchEditNotes) researchEditNotes.value = previewResearchPlan.notes || '';
        if (researchEditFetchAbstract) researchEditFetchAbstract.checked = Boolean(previewResearchPlan.fetch_abstract);
      }}
      renderResearchStatus({{
        status: 'preview',
        step_message: message || '已生成 research 方案预览，确认后可直接开始执行。',
        plan: plan || {{}},
        prompt: prompt || '',
      }});
    }}

    function clearResearchPreview() {{
      previewResearchPlan = null;
      previewResearchPrompt = '';
      previewResearchPlanDirty = false;
      if (researchEditor) researchEditor.style.display = 'none';
      syncResearchSubmitState();
      if (researchEditSlug) researchEditSlug.value = '';
      if (researchEditYearFrom) researchEditYearFrom.value = '';
      if (researchEditTop) researchEditTop.value = '';
      if (researchEditVenues) researchEditVenues.value = '';
      if (researchEditKeywords) researchEditKeywords.value = '';
      if (researchEditNotes) researchEditNotes.value = '';
      if (researchEditFetchAbstract) researchEditFetchAbstract.checked = true;
    }}

    function collectManualResearchPlan() {{
      return {{
        slug: researchEditSlug ? researchEditSlug.value.trim() : '',
        year_from: researchEditYearFrom ? Number(researchEditYearFrom.value || 0) : 0,
        top: researchEditTop ? Number(researchEditTop.value || 100) : 100,
        venues: researchEditVenues ? researchEditVenues.value.split(',').map((item) => item.trim()).filter(Boolean) : [],
        keywords: researchEditKeywords ? researchEditKeywords.value.split('\\n').map((item) => item.trim()).filter(Boolean) : [],
        notes: researchEditNotes ? researchEditNotes.value.trim() : '',
        fetch_abstract: researchEditFetchAbstract ? Boolean(researchEditFetchAbstract.checked) : true,
        summary: previewResearchPlan && previewResearchPlan.summary ? previewResearchPlan.summary : '',
      }};
    }}

    function markResearchPlanDirty() {{
      if (!previewResearchPlan) return;
      previewResearchPlanDirty = true;
    }}

    function renderResearchJobs(items) {{
      if (!researchJobs) return;
      const allJobs = Array.isArray(items) ? items : [];
      const pendingStatuses = new Set(['queued', 'running', 'planning', 'planned', 'searching', 'search_completed', 'fetching_abstracts', 'abstracts_completed', 'abstracts_skipped', 'writing_outputs', 'reviewing_results']);
      const activeJobs = allJobs.filter((job) => pendingStatuses.has((job.status || '').toString()));
      const completedJobs = allJobs
        .filter((job) => (job.status || '') === 'completed')
        .sort((a, b) => {{
          const aTime = Date.parse(a.updated_at || a.created_at || '') || 0;
          const bTime = Date.parse(b.updated_at || b.created_at || '') || 0;
          return bTime - aTime;
        }});
      const visibleJobs = activeJobs.concat(completedJobs.slice(0, 1));
      if (!visibleJobs.length) {{
        researchJobs.innerHTML = '';
        return;
      }}
      researchJobs.innerHTML = visibleJobs.map((job) => {{
        const plan = job.plan || {{}};
        const links = [];
        if (job.site_relative_url) links.push(`<a href="${{job.site_relative_url}}" target="_blank" rel="noreferrer">结果网页</a>`);
        if (job.csv_url) links.push(`<a href="${{job.csv_url}}" target="_blank" rel="noreferrer">CSV</a>`);
        if (job.status === 'failed') links.push(`<button class="danger-btn delete-research-job" type="button" data-job-id="${{esc(job.id || '')}}">删除失败记录</button>`);
        return `
          <div class="research-job">
            <div><strong>${{esc(job.prompt || '')}}</strong></div>
            <div class="meta">状态：${{esc(job.status || '')}} · ${{esc(job.step_message || '')}}</div>
            <div class="meta">slug：${{esc(plan.slug || '')}} · 关键词：${{esc((plan.keywords || []).join(' ; '))}}</div>
            ${{links.length ? `<div class="research-job-links">${{links.join('')}}</div>` : ''}}
          </div>
        `;
      }}).join('');
    }}

    function pickPrimaryResearchJob(items) {{
      const allJobs = Array.isArray(items) ? items : [];
      if (!allJobs.length) return null;
      const pendingSteps = new Set(['queued', 'planning', 'planned', 'searching', 'search_completed', 'fetching_abstracts', 'abstracts_completed', 'abstracts_skipped', 'writing_outputs', 'reviewing_results']);
      const pendingJobs = allJobs
        .filter((job) => {{
          const status = (job.status || '').toString();
          const step = (job.current_step || '').toString();
          return status === 'queued' || status === 'running' || pendingSteps.has(step);
        }})
        .sort((a, b) => {{
          const aTime = Date.parse(a.updated_at || a.created_at || '') || 0;
          const bTime = Date.parse(b.updated_at || b.created_at || '') || 0;
          return bTime - aTime;
        }});
      if (pendingJobs.length) return pendingJobs[0];
      const completedJobs = allJobs
        .filter((job) => (job.status || '') === 'completed')
        .sort((a, b) => {{
          const aTime = Date.parse(a.updated_at || a.created_at || '') || 0;
          const bTime = Date.parse(b.updated_at || b.created_at || '') || 0;
          return bTime - aTime;
        }});
      return completedJobs[0] || allJobs[0] || null;
    }}

    async function fetchResearchJobs() {{
      const resp = await fetch('/api/research/jobs', {{ credentials: 'same-origin' }});
      const data = await resp.json().catch(() => ({{ ok: false, jobs: [] }}));
      if (!resp.ok || data.ok === false) return [];
      const jobs = data.jobs || [];
      renderResearchJobs(jobs);
      if (!activeResearchJobId) {{
        renderResearchStatus(pickPrimaryResearchJob(jobs));
        const runningJob = jobs.find((item) => item && !['completed', 'failed'].includes((item.status || '').toString()));
        if (runningJob && runningJob.id) {{
          pollResearchJob(runningJob.id);
        }}
      }}
      return jobs;
    }}

    async function pollResearchJob(jobId) {{
      activeResearchJobId = jobId;
      while (activeResearchJobId === jobId) {{
        const resp = await fetch('/api/research/jobs/' + encodeURIComponent(jobId), {{ credentials: 'same-origin' }});
        const data = await resp.json().catch(() => ({{ ok: false }}));
        if (!resp.ok || data.ok === false) break;
        const job = data.job || null;
        renderResearchStatus(job);
        await fetchResearchJobs();
        if (!job || ['completed', 'failed'].includes(job.status)) {{
          break;
        }}
        await new Promise((resolve) => setTimeout(resolve, 2000));
      }}
    }}

    if (researchCompose) {{
      researchCompose.addEventListener('click', async () => {{
        const latestInput = (researchPrompt && researchPrompt.value || '').trim();
        if (!latestInput) {{
          renderResearchStatus({{ status: 'invalid', step_message: '请先输入 research 内容。', plan: {{}} }});
          return;
        }}
        researchCompose.disabled = true;
        if (researchSubmit) researchSubmit.disabled = true;
        setActionButtonState(researchCompose, 'busy');
        renderResearchStatus({{
          status: 'planning',
          step_message: previewResearchPlan ? '正在判断你是想修改当前方案，还是重新生成一个新方案。' : '正在根据你的需求生成一份可执行的搜索方案。',
          plan: previewResearchPlan || {{}}
        }});
        try {{
          const resp = await fetch('/api/research/plan/compose', {{
            method: 'POST',
            credentials: 'same-origin',
            headers: {{ 'Content-Type': 'application/json' }},
            body: JSON.stringify({{
              input: latestInput,
              current_prompt: previewResearchPrompt,
              plan: previewResearchPlan
            }})
          }});
          const data = await parseApiJson(resp, '生成方案失败');
          renderResearchPreview(data.plan || {{}}, data.prompt || latestInput, data.message || '方案已更新。');
          setActionButtonState(researchCompose, 'success');
          if (researchPrompt) researchPrompt.value = '';
        }} catch (error) {{
          clearResearchPreview();
          renderResearchStatus({{ status: 'failed', step_message: error.message, plan: {{}} }});
        }} finally {{
          researchCompose.disabled = false;
          window.setTimeout(() => setActionButtonState(researchCompose, ''), 1800);
          syncResearchSubmitState();
        }}
      }});
    }}

    if (researchSubmit) {{
      researchSubmit.addEventListener('click', async () => {{
        const prompt = (researchPrompt && researchPrompt.value || '').trim();
        const effectivePrompt = previewResearchPrompt || prompt;
        if (!effectivePrompt) {{
          renderResearchStatus({{ status: 'invalid', step_message: '请先输入 research 需求。', plan: {{}} }});
          return;
        }}
        researchSubmit.disabled = true;
        if (researchCompose) researchCompose.disabled = true;
        setActionButtonState(researchSubmit, 'busy');
        renderResearchStatus({{
          status: 'queued',
          step_message: previewResearchPlan
            ? (previewResearchPlanDirty ? '正在检查你手工修改后的方案，并把搜索任务提交到后台队列。' : '正在直接提交刚生成的方案到后台队列。')
            : '还没有现成方案，正在自动生成并提交搜索任务。',
          plan: previewResearchPlan || {{}}
        }});
        try {{
          let validatedPlan = previewResearchPlan;
          if (previewResearchPlan && previewResearchPlanDirty) {{
            const manualPlan = collectManualResearchPlan();
            const validateResp = await fetch('/api/research/plan/validate', {{
              method: 'POST',
              credentials: 'same-origin',
              headers: {{ 'Content-Type': 'application/json' }},
              body: JSON.stringify({{
                prompt: effectivePrompt,
                plan: manualPlan
              }})
            }});
            const validateData = await parseApiJson(validateResp, '方案验证失败');
            validatedPlan = validateData.plan || manualPlan;
            renderResearchPreview(validatedPlan, effectivePrompt, '当前手工编辑方案已通过模型复核，开始执行 Research。');
          }} else if (previewResearchPlan) {{
            validatedPlan = collectManualResearchPlan();
          }}
          const resp = await fetch('/api/research/jobs', {{
            method: 'POST',
            credentials: 'same-origin',
            headers: {{ 'Content-Type': 'application/json' }},
            body: JSON.stringify({{ prompt: effectivePrompt, plan: validatedPlan }})
          }});
          const data = await parseApiJson(resp, '启动搜索失败');
          setActionButtonState(researchSubmit, 'success');
          alert('搜索任务已经开始。你现在可以留在当前页面看进度，也可以稍后回来，任务会继续在后台运行。');
          renderResearchStatus(data.job || null);
          await fetchResearchJobs();
          clearResearchPreview();
          if (data.job_id) {{
            pollResearchJob(data.job_id);
          }}
        }} catch (error) {{
          renderResearchStatus({{ status: 'failed', step_message: error.message, plan: previewResearchPlan || {{}} }});
        }} finally {{
          syncResearchSubmitState();
          window.setTimeout(() => setActionButtonState(researchSubmit, ''), 2400);
          if (researchCompose) researchCompose.disabled = false;
        }}
      }});
      syncResearchSubmitState();
      fetchResearchJobs().catch(() => {{}});
    }}

    [
      researchEditSlug,
      researchEditYearFrom,
      researchEditTop,
      researchEditVenues,
      researchEditKeywords,
      researchEditNotes,
      researchEditFetchAbstract,
    ].filter(Boolean).forEach((element) => {{
      element.addEventListener('input', markResearchPlanDirty);
      element.addEventListener('change', markResearchPlanDirty);
    }});

    expansionFilterButtons.forEach((button) => {{
      button.addEventListener('click', () => {{
        const filter = button.dataset.expansionFilter || 'all';
        expansionFilterButtons.forEach((item) => item.classList.toggle('active', item === button));
        expansionEntries.forEach((entry) => {{
          const kw = (entry.dataset.matchedKw || '').trim();
          const visible = filter === 'all' || kw === filter;
          entry.style.display = visible ? '' : 'none';
        }});
      }});
    }});

    document.querySelectorAll('.delete-search-entry').forEach((button) => {{
      button.addEventListener('click', async () => {{
        const relativeDir = (button.dataset.relativeDir || '').trim();
        if (!relativeDir) return;
        if (!confirm('确定删除这次 timeline 搜索结果吗？相关目录和站点文件会一起删除。')) return;
        const resp = await fetch('/api/search-entries/' + encodeURIComponent(relativeDir), {{
          method: 'DELETE',
          credentials: 'same-origin'
        }});
        const data = await resp.json().catch(() => ({{ ok: false, error: '删除失败' }}));
        if (!resp.ok || data.ok === false) {{
          alert(data.error || '删除失败');
          return;
        }}
        window.location.reload();
      }});
    }});

    document.querySelectorAll('.edit-search-entry').forEach((button) => {{
      button.addEventListener('click', async () => {{
        const entry = button.closest('.entry');
        const relativeDir = text(button.dataset.relativeDir || entry?.dataset.relativeDir).trim();
        if (!entry || !relativeDir) return;
        const currentTitle = text(entry.dataset.entryTitle).trim();
        const defaultTitle = text(entry.dataset.defaultTitle).trim();
        const nextTitle = window.prompt('输入新的 timeline 标题。留空后保存可恢复默认标题。', currentTitle);
        if (nextTitle === null) return;
        button.disabled = true;
        const previousText = button.textContent;
        button.textContent = '保存中...';
        try {{
          const data = await updateTimelineTitle(relativeDir, nextTitle);
          const titleText = text(data.title).trim() || defaultTitle;
          entry.dataset.entryTitle = titleText;
          if (data.default_title) {{
            entry.dataset.defaultTitle = text(data.default_title).trim();
          }}
          const titleNode = entry.querySelector('.entry-title-text');
          if (titleNode) {{
            titleNode.textContent = titleText;
          }}
          alert(data.message || '标题已更新');
        }} catch (error) {{
          alert(error.message || '更新标题失败');
        }} finally {{
          button.disabled = false;
          button.textContent = previousText;
        }}
      }});
    }});

    researchJobs?.addEventListener('click', async (event) => {{
      const button = event.target.closest('.delete-research-job');
      if (!button) return;
      const jobId = (button.dataset.jobId || '').trim();
      if (!jobId) return;
      if (!confirm('确定删除这条失败的 research 记录吗？')) return;
      const resp = await fetch('/api/research/jobs/' + encodeURIComponent(jobId), {{
        method: 'DELETE',
        credentials: 'same-origin'
      }});
      const data = await resp.json().catch(() => ({{ ok: false, error: '删除失败' }}));
      if (!resp.ok || data.ok === false) {{
        alert(data.error || '删除失败');
        return;
      }}
      if (activeResearchJobId === jobId) {{
        activeResearchJobId = '';
        renderResearchStatus(null);
      }}
      await fetchResearchJobs();
    }});
  </script>
</body>
</html>"""


def build_keywords_html():
    entries = list_keyword_entries()
    graph = load_keyword_graph_cache()
    cards = []
    for entry in entries:
        cards.append(
            f"""
            <a class="kw-card" data-keyword-id="{escape(entry['keyword'].lower())}" href="/keywords/{entry['slug']}">
              <div class="kw-name">{escape(entry['keyword'])}</div>
              <div class="kw-count">{entry['count']} 篇论文</div>
              <div class="muted">最近新增：{escape(entry.get('latest_date') or '未知')}</div>
            </a>
            """
        )
    body = "\n".join(cards) if cards else '<div class="empty">还没有可用的命中词数据。</div>'
    graph_json = json.dumps(graph, ensure_ascii=False).replace("</script>", "<\\/script>")
    return f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Keywords</title>
  <style>
    body {{ margin:0; font-family: Georgia, "Noto Serif SC", serif; background:#f2efe8; color:#1e1d1a; }}
    .wrap {{ max-width:1040px; margin:0 auto; padding:28px 18px 72px; }}
    .hero, .kw-card, .empty {{
      border:1px solid #d5cbba; border-radius:24px; background:rgba(255,251,244,0.96);
      box-shadow:0 18px 40px rgba(76,50,28,0.08);
    }}
    .hero {{ padding:28px; margin-bottom:20px; }}
    .row {{ display:flex; justify-content:space-between; gap:12px; flex-wrap:wrap; }}
    .actions {{ display:flex; gap:10px; flex-wrap:wrap; margin-top:14px; }}
    .actions a {{
      display:inline-block; background:#9c4f2f; color:white; text-decoration:none;
      padding:10px 14px; border-radius:999px;
    }}
    h1 {{ margin:0 0 8px; font-size:40px; }}
    .muted {{ color:#6f685c; line-height:1.7; font-size:14px; }}
    .graph-shell {{
      margin-bottom:20px; padding:20px;
      border:1px solid #d5cbba; border-radius:24px; background:rgba(255,251,244,0.96);
      box-shadow:0 18px 40px rgba(76,50,28,0.08);
    }}
    .graph-layout {{
      display:flex;
      flex-direction:column;
      gap:16px;
      align-items:stretch;
    }}
    .graph-head {{ display:flex; justify-content:space-between; gap:12px; flex-wrap:wrap; align-items:flex-end; margin-bottom:12px; }}
    .graph-title {{ font-size:24px; margin:0; }}
    .graph-meta {{ color:#6f685c; font-size:14px; }}
    .graph-controls {{
      display:flex;
      gap:8px;
      flex-wrap:wrap;
      margin:12px 0 14px;
      align-items:center;
    }}
    .graph-group-select {{
      padding:8px 12px;
      border-radius:999px;
      border:1px solid #d5cbba;
      background:white;
      color:#4f4338;
      font:inherit;
      min-width:220px;
    }}
    .graph-toggle {{
      border:none;
      border-radius:999px;
      padding:8px 12px;
      background:#ead8ca;
      color:#6f685c;
      font:inherit;
      cursor:pointer;
    }}
    .graph-toggle.active {{
      background:#9c4f2f;
      color:white;
    }}
    .graph-board {{
      position:relative;
      min-height:560px;
      border-radius:20px;
      border:1px dashed #d5cbba;
      background:
        radial-gradient(circle at top, rgba(156,79,47,0.08), transparent 38%),
        linear-gradient(180deg, rgba(255,255,255,0.68), rgba(244,236,225,0.88));
      overflow:hidden;
    }}
    .graph-board svg {{ width:100%; height:560px; display:block; }}
    .graph-empty {{
      padding:28px;
      color:#6f685c;
      text-align:center;
      line-height:1.7;
    }}
    .graph-link {{
      stroke:#d9c7b6;
      stroke-linecap:round;
      transition:opacity .2s ease, stroke .2s ease, stroke-width .2s ease;
    }}
    .graph-link.active {{
      stroke:#b34e2f;
      opacity:0.95 !important;
    }}
    .graph-link.context {{
      stroke:#c47b52;
    }}
    .graph-node {{ cursor:pointer; }}
    .graph-node circle {{
      stroke:#7b4734;
      stroke-width:1.6;
      transition:transform .16s ease, fill .16s ease, stroke-width .16s ease, opacity .16s ease;
      transform-box:fill-box;
      transform-origin:center;
    }}
    .graph-node text {{
      fill:#47352d;
      font-weight:700;
      text-anchor:middle;
      dominant-baseline:middle;
      pointer-events:none;
      transition:fill .16s ease, opacity .16s ease;
    }}
    .graph-node:hover circle, .graph-node.active circle {{
      stroke-width:2.8;
      transform:scale(1.04);
    }}
    .graph-node.active text {{ fill:#8e3f28; }}
    .graph-node.context circle {{ stroke:#c16a3f; }}
    .graph-node.context text {{ fill:#9a5b39; }}
    .graph-node.faded circle, .graph-node.faded text {{
      opacity:0.22;
    }}
    .graph-hint {{
      margin-top:10px;
      color:#6f685c;
      font-size:14px;
      line-height:1.7;
    }}
    .graph-side {{
      border:1px solid #d5cbba;
      border-radius:20px;
      background:rgba(255,248,240,0.94);
      padding:16px;
      min-height:240px;
      box-shadow:inset 0 1px 0 rgba(255,255,255,0.5);
    }}
    .graph-side h3 {{
      margin:0 0 8px;
      font-size:24px;
      line-height:1.2;
    }}
    .graph-side .meta-line {{
      color:#6f685c;
      font-size:14px;
      line-height:1.7;
      margin-bottom:12px;
    }}
    .graph-side .summary {{
      color:#4f4338;
      line-height:1.75;
      font-size:15px;
      margin-bottom:14px;
    }}
    .graph-side .open-link {{
      display:inline-block;
      background:#9c4f2f;
      color:white;
      text-decoration:none;
      padding:10px 14px;
      border-radius:999px;
      margin-bottom:16px;
    }}
    .graph-related-title {{
      margin:18px 0 8px;
      font-size:16px;
      color:#4f4338;
    }}
    .graph-related-list {{
      display:grid;
      grid-template-columns:repeat(auto-fit, minmax(220px, 1fr));
      gap:8px;
      align-items:start;
    }}
    .graph-related-list.collapsed .graph-related-item.hidden-related {{
      display:none;
    }}
    .graph-related-item {{
      display:grid;
      grid-template-columns:minmax(0, 1fr) auto;
      gap:8px 10px;
      align-items:start;
      padding:10px 12px;
      border:1px solid #e2d4c4;
      border-radius:16px;
      background:rgba(255,255,255,0.72);
      min-width:0;
      max-width:100%;
    }}
    .graph-related-actions {{
      display:flex;
      gap:6px;
      flex-wrap:wrap;
      align-items:center;
      margin-top:4px;
    }}
    .graph-related-main {{
      min-width:0;
    }}
    .graph-related-head {{
      display:flex;
      gap:8px;
      align-items:baseline;
      flex-wrap:wrap;
    }}
    .graph-related-item a {{
      color:#9c4f2f;
      text-decoration:none;
      background:none;
      padding:0;
      border-radius:0;
      font-weight:700;
      line-height:1.35;
      font-size:15px;
    }}
    .graph-related-weight {{
      color:#6f685c;
      font-size:12px;
      white-space:nowrap;
      background:rgba(240,223,207,0.9);
      border-radius:999px;
      padding:4px 8px;
    }}
    .graph-related-toggle, .graph-filter-clear, .graph-related-more {{
      border:none;
      border-radius:999px;
      padding:5px 10px;
      font:inherit;
      font-size:13px;
      cursor:pointer;
      background:#ead8ca;
      color:#6f685c;
    }}
    .graph-related-toggle {{
      width:28px;
      height:28px;
      padding:0;
      display:inline-flex;
      align-items:center;
      justify-content:center;
      font-size:16px;
      font-weight:700;
      line-height:1;
    }}
    .graph-related-toggle.active {{
      background:#9c4f2f;
      color:white;
    }}
    .graph-related-more {{
      margin-top:8px;
      background:#f0dfcf;
      color:#7a4a2a;
    }}
    .graph-filter-summary {{
      margin:14px 0 10px;
      display:flex;
      gap:8px;
      flex-wrap:wrap;
      align-items:center;
    }}
    .graph-filter-querybar {{
      margin:12px 0 10px;
      padding:10px 12px;
      border-radius:14px;
      background:rgba(255,255,255,0.78);
      border:1px solid #e6d7c8;
      color:#5b473f;
      line-height:1.7;
    }}
    .graph-filter-pill {{
      background:#f0dfcf;
      color:#734532;
      border-radius:999px;
      padding:6px 10px;
      font-size:13px;
    }}
    .graph-filter-open {{
      display:inline-block;
      background:#9c4f2f;
      color:white;
      text-decoration:none;
      padding:9px 12px;
      border-radius:999px;
    }}
    .graph-side-empty {{
      color:#6f685c;
      line-height:1.8;
      font-size:15px;
      padding:8px 2px;
    }}
    .grid {{ display:grid; grid-template-columns:repeat(auto-fill, minmax(220px, 1fr)); gap:16px; }}
    .kw-card {{ padding:18px; text-decoration:none; color:inherit; }}
    .kw-card:hover {{ transform:translateY(-2px); transition:transform .18s ease; }}
    .kw-name {{ font-size:24px; line-height:1.25; margin-bottom:10px; }}
    .kw-count {{ color:#9c4f2f; font-weight:700; }}
    .empty {{ padding:24px; text-align:center; }}
    @media (max-width: 720px) {{
      .wrap {{ padding:22px 14px 56px; }}
      .hero {{ padding:22px 18px; }}
      .actions {{ width:100%; }}
      .actions a {{ width:100%; text-align:center; }}
      .graph-board {{ min-height:420px; }}
      .graph-board svg {{ height:420px; }}
      .graph-shell {{ padding:16px; }}
      .graph-side {{ min-height:initial; }}
      .grid {{ grid-template-columns:1fr; gap:14px; }}
      .kw-card {{ padding:16px; }}
      .kw-name {{ font-size:20px; }}
    }}
  </style>
</head>
<body>
  <main class="wrap">
    <section class="hero">
      <div class="row">
        <div>
          <h1>Keywords</h1>
          <div class="muted">这里汇总了所有关键词，并按最近新增论文的时间倒序排列。除了原始搜索命中词，也会展示深度阅读分析后回写到文献上的关键词。</div>
        </div>
        <div class="actions">
          <a href="/">返回时间线</a>
          <a href="/reading">深度阅读</a>
        </div>
      </div>
    </section>
    <section class="graph-shell">
      <div class="graph-head">
        <div>
          <h2 class="graph-title">Keyword Constellation</h2>
          <div class="graph-meta">基于同一篇论文里的标签共现关系生成。字越大，出现次数越高；距离越近、连线越深，表示关联越强。</div>
        </div>
        <div class="graph-meta">当前展示 {graph['graph_keywords']} / {graph['total_keywords']} 个关键词，覆盖 {graph['total_papers']} 篇论文</div>
      </div>
      <div class="graph-controls">
        <button class="graph-toggle active" type="button" data-graph-mode="strong">只看当前最强关联</button>
        <button class="graph-toggle" type="button" data-graph-mode="popular">只看最常见 tags</button>
        <select class="graph-group-select" id="keyword-graph-group-filter">
          <option value="all">全部 Groups / 全局标签</option>
        </select>
      </div>
      <div class="graph-layout">
        <div class="graph-board" id="keyword-graph-board">
          <div class="graph-empty">正在绘制关键词关联图…</div>
        </div>
        <aside class="graph-side" id="keyword-graph-side">
          <div class="graph-side-empty">点击上方任意球体，我们会在这里展示这个 tag 最相关的几个 tag，方便你先判断主题结构，再决定是否进入详情页。</div>
        </aside>
      </div>
      <div class="graph-hint">页面每次打开时都会基于最新论文和 tags 实时重算，所以有新论文加入后，这里会自动反映。单击球体可在下方预览，再进入详情页。</div>
    </section>
    <section class="grid">{body}</section>
  </main>
  <script id="keyword-graph-data" type="application/json">{graph_json}</script>
  <script>
    const keywordGraph = JSON.parse(document.getElementById('keyword-graph-data').textContent || '{{}}');
    let keywordGraphMode = 'strong';
    let keywordGraphSelectedNodeId = '';
    let keywordGraphGroupFilter = 'all';
    let keywordGraphActiveFilterIds = new Set();
    let keywordGraphVisibleNodes = [];
    let keywordGraphVisibleEdges = [];

    function esc(value) {{
      return (value || '').toString()
        .replaceAll('&', '&amp;')
        .replaceAll('<', '&lt;')
        .replaceAll('>', '&gt;')
        .replaceAll('"', '&quot;');
    }}

    function clamp(value, min, max) {{
      return Math.max(min, Math.min(max, value));
    }}

    function scoreNodeDegrees(nodes, edges) {{
      const degreeMap = new Map(nodes.map((node) => [node.id, 0]));
      for (const edge of edges) {{
        const weight = Number(edge.weight) || 0;
        degreeMap.set(edge.source, (degreeMap.get(edge.source) || 0) + weight);
        degreeMap.set(edge.target, (degreeMap.get(edge.target) || 0) + weight);
      }}
      return degreeMap;
    }}

    function getVisibleGraphPayload() {{
      const sourceGraph = keywordGraphGroupFilter === 'all'
        ? keywordGraph
        : ((keywordGraph.graphs_by_group || {{}})[keywordGraphGroupFilter] || {{ nodes: [], edges: [] }});
      const allNodes = Array.isArray(sourceGraph.nodes) ? sourceGraph.nodes.slice() : [];
      const allEdges = Array.isArray(sourceGraph.edges) ? sourceGraph.edges.slice() : [];
      if (!allNodes.length) return {{ nodes: [], edges: [] }};

      const degreeMap = scoreNodeDegrees(allNodes, allEdges);
      let nodes;
      if (keywordGraphMode === 'popular') {{
        nodes = allNodes
          .slice()
          .sort((a, b) => (Number(b.count) || 0) - (Number(a.count) || 0) || (b.latest_date || '').localeCompare(a.latest_date || '') || (a.label || '').localeCompare(b.label || ''))
          .slice(0, 28);
      }} else {{
        nodes = allNodes
          .slice()
          .sort((a, b) => (degreeMap.get(b.id) || 0) - (degreeMap.get(a.id) || 0) || (Number(b.count) || 0) - (Number(a.count) || 0) || (a.label || '').localeCompare(b.label || ''))
          .slice(0, 24);
      }}
      const allowed = new Set(nodes.map((node) => node.id));
      const edges = allEdges
        .filter((edge) => allowed.has(edge.source) && allowed.has(edge.target))
        .sort((a, b) => (Number(b.weight) || 0) - (Number(a.weight) || 0) || a.source.localeCompare(b.source) || a.target.localeCompare(b.target))
        .slice(0, keywordGraphMode === 'popular' ? 72 : 56);
      return {{ nodes, edges }};
    }}

    function updateGraphModeButtons() {{
      document.querySelectorAll('[data-graph-mode]').forEach((button) => {{
        button.classList.toggle('active', button.dataset.graphMode === keywordGraphMode);
      }});
    }}

    function renderGroupFilterOptions() {{
      const select = document.getElementById('keyword-graph-group-filter');
      if (!select) return;
      const groups = Array.isArray(keywordGraph.groups) ? keywordGraph.groups.slice() : [];
      select.innerHTML = '<option value="all">全部 Groups / 全局标签</option>' + groups.map((group) => (
        `<option value="${{esc(group.id)}}">${{esc(group.name)}} (${{group.paper_count}} 篇)</option>`
      )).join('');
      if (!groups.some((group) => group.id === keywordGraphGroupFilter)) {{
        keywordGraphGroupFilter = 'all';
      }}
      select.value = keywordGraphGroupFilter;
    }}

    function renderKeywordPreview(node, visibleEdges) {{
      const side = document.getElementById('keyword-graph-side');
      if (!side) return;
      if (!node) {{
        side.innerHTML = '<div class="graph-side-empty">点击上方任意球体，我们会在这里展示这个 tag 最相关的几个 tag，方便你先判断主题结构，再决定是否进入详情页。</div>';
        return;
      }}
      const related = visibleEdges
        .filter((edge) => edge.source === node.id || edge.target === node.id)
        .map((edge) => {{
          const otherId = edge.source === node.id ? edge.target : edge.source;
          const other = (keywordGraph.nodes || []).find((item) => item.id === otherId);
          return other ? {{ node: other, weight: Number(edge.weight) || 0 }} : null;
        }})
        .filter(Boolean)
        .sort((a, b) => b.weight - a.weight || (Number(b.node.count) || 0) - (Number(a.node.count) || 0))
        .slice(0, 8);
      const relatedVisibleLimit = 5;

      const relatedHtml = related.length
        ? related.map((item, index) => `
            <div class="graph-related-item${{index >= relatedVisibleLimit ? ' hidden-related' : ''}}">
              <div class="graph-related-main">
                <div class="graph-related-head">
                  <a href="/keywords/${{item.node.slug}}">${{esc(item.node.label)}}</a>
                  <div class="muted" style="font-size:13px;">${{item.node.count}} 篇</div>
                </div>
                <div class="graph-related-actions">
                  <button class="graph-related-toggle ${{keywordGraphActiveFilterIds.has(item.node.id) ? 'active' : ''}}" type="button" title="${{keywordGraphActiveFilterIds.has(item.node.id) ? '移出筛选' : '加入筛选'}}" aria-label="${{keywordGraphActiveFilterIds.has(item.node.id) ? '移出筛选' : '加入筛选'}}" data-related-node-id="${{esc(item.node.id)}}">
                    ${{keywordGraphActiveFilterIds.has(item.node.id) ? '✓' : '+'}}
                  </button>
                </div>
              </div>
              <div class="graph-related-weight">强度 ${{item.weight}}</div>
            </div>
          `).join('')
        : '<div class="graph-side-empty">这个 tag 目前还没有足够强的共现关系，可能更适合作为独立入口查看。</div>';

      const filterPills = Array.from(keywordGraphActiveFilterIds)
        .map((id) => keywordGraphVisibleNodes.find((item) => item.id === id) || (keywordGraph.nodes || []).find((item) => item.id === id))
        .filter(Boolean)
        .map((item) => `<span class="graph-filter-pill">${{esc(item.label)}}</span>`)
        .join('');
      const filterQueryText = Array.from(keywordGraphActiveFilterIds)
        .map((id) => keywordGraphVisibleNodes.find((item) => item.id === id) || (keywordGraph.nodes || []).find((item) => item.id === id))
        .filter(Boolean)
        .map((item) => item.label)
        .join(' + ');
      const intersectionHref = `/keywords/intersection?tags=${{encodeURIComponent(Array.from(keywordGraphActiveFilterIds).map((id) => {{
        const hit = keywordGraphVisibleNodes.find((item) => item.id === id) || (keywordGraph.nodes || []).find((item) => item.id === id);
        return hit ? hit.label : '';
      }}).filter(Boolean).join(','))}}`;
      const filterSummaryHtml = keywordGraphActiveFilterIds.size
        ? `
            <div class="graph-filter-querybar">
              当前查询：${{esc(filterQueryText)}}<br>
              <span class="muted">这会筛出这些 tags 的交叉区域，并可直接打开共同出现的论文列表。</span>
            </div>
            <div class="graph-filter-summary">
              ${{filterPills}}
              <a class="graph-filter-open" href="${{intersectionHref}}">只看这些 tag 共同出现的论文</a>
              <button class="graph-filter-clear" type="button" id="keyword-graph-clear-filter">清空多标签筛选</button>
            </div>
          `
        : '';

      side.innerHTML = `
        <h3>${{esc(node.label)}}</h3>
        <div class="meta-line">出现 ${{node.count}} 次 · 最近出现：${{esc(node.latest_date || '未知')}}</div>
        <div class="summary">${{keywordGraphMode === 'strong' ? '当前视图强调它与其他 tags 的共现强度，适合快速判断主题簇。' : '当前视图强调这个 tag 本身的出现频率，适合先看全局热点。'}}${{keywordGraphGroupFilter !== 'all' ? ' 现在这张图只基于当前选中的 Group。' : ''}}</div>
        <a class="open-link" href="/keywords/${{node.slug}}">打开这个关键词详情</a>
        ${{filterSummaryHtml}}
        <div class="graph-related-title">最相关的几个 tag</div>
        <div class="graph-related-list${{related.length > relatedVisibleLimit ? ' collapsed' : ''}}" id="keyword-graph-related-list">${{relatedHtml}}</div>
        ${{related.length > relatedVisibleLimit ? `<button class="graph-related-more" type="button" id="keyword-graph-related-more">展开更多（+${{related.length - relatedVisibleLimit}}）</button>` : ''}}
      `;

      side.querySelectorAll('[data-related-node-id]').forEach((button) => {{
        button.addEventListener('click', () => {{
          const targetId = button.getAttribute('data-related-node-id');
          if (!targetId || targetId === keywordGraphSelectedNodeId) return;
          if (keywordGraphActiveFilterIds.has(targetId)) {{
            keywordGraphActiveFilterIds.delete(targetId);
          }} else {{
            keywordGraphActiveFilterIds.add(targetId);
          }}
          renderKeywordPreview(node, visibleEdges);
          applyKeywordGraphSelectionState();
          applyKeywordCardFilter();
        }});
      }});

      const clearButton = document.getElementById('keyword-graph-clear-filter');
      if (clearButton) {{
        clearButton.addEventListener('click', () => {{
          keywordGraphActiveFilterIds = new Set(keywordGraphSelectedNodeId ? [keywordGraphSelectedNodeId] : []);
          renderKeywordPreview(node, visibleEdges);
          applyKeywordGraphSelectionState();
          applyKeywordCardFilter();
        }});
      }}

      const moreButton = document.getElementById('keyword-graph-related-more');
      const relatedList = document.getElementById('keyword-graph-related-list');
      if (moreButton && relatedList) {{
        moreButton.addEventListener('click', () => {{
          const collapsed = relatedList.classList.toggle('collapsed');
          moreButton.textContent = collapsed
            ? `展开更多（+${{Math.max(0, related.length - relatedVisibleLimit)}}）`
            : '收起';
        }});
      }}
    }}

    function colorForNode(node, maxCount) {{
      const ratio = maxCount <= 1 ? 0.55 : (Number(node.count) || 1) / maxCount;
      const hue = 22 + ratio * 18;
      const saturation = 52 + ratio * 18;
      const lightness = 86 - ratio * 28;
      return `hsl(${{hue}}, ${{saturation}}%, ${{lightness}}%)`;
    }}

    function applyKeywordCardFilter() {{
      const cards = Array.from(document.querySelectorAll('.kw-card[data-keyword-id]'));
      if (!cards.length) return;
      const activeIds = Array.from(keywordGraphActiveFilterIds);
      if (!activeIds.length) {{
        cards.forEach((card) => {{
          card.style.display = '';
          card.style.opacity = '1';
        }});
        return;
      }}
      cards.forEach((card) => {{
        const cardId = (card.dataset.keywordId || '').trim().toLowerCase();
        const visible = keywordGraphActiveFilterIds.has(cardId);
        card.style.display = visible ? '' : 'none';
        card.style.opacity = visible ? '1' : '0.25';
      }});
    }}

    function applyKeywordGraphSelectionState() {{
      const board = document.getElementById('keyword-graph-board');
      if (!board) return;
      const activeIds = keywordGraphActiveFilterIds.size ? new Set(keywordGraphActiveFilterIds) : new Set(keywordGraphSelectedNodeId ? [keywordGraphSelectedNodeId] : []);
      const contextIds = new Set(keywordGraphSelectedNodeId ? [keywordGraphSelectedNodeId] : []);
      keywordGraphVisibleEdges.forEach((edge) => {{
        if (edge.source === keywordGraphSelectedNodeId) contextIds.add(edge.target);
        if (edge.target === keywordGraphSelectedNodeId) contextIds.add(edge.source);
      }});

      board.querySelectorAll('.graph-node').forEach((element) => {{
        const nodeId = element.getAttribute('data-node-id') || '';
        const isSelected = activeIds.has(nodeId);
        const isContext = contextIds.has(nodeId);
        element.classList.toggle('active', isSelected);
        element.classList.toggle('context', !isSelected && isContext);
        element.classList.toggle('faded', !isSelected && !isContext && activeIds.size > 0);
      }});

      board.querySelectorAll('.graph-link').forEach((element) => {{
        const source = element.dataset.source || '';
        const target = element.dataset.target || '';
        const bothSelected = activeIds.has(source) && activeIds.has(target);
        const touchesSelected = activeIds.has(source) || activeIds.has(target);
        const touchesPrimary = source === keywordGraphSelectedNodeId || target === keywordGraphSelectedNodeId;
        element.classList.toggle('active', bothSelected);
        element.classList.toggle('context', !bothSelected && touchesPrimary);
        if (!activeIds.size) {{
          element.style.opacity = element.getAttribute('opacity') || '0.2';
        }} else if (bothSelected) {{
          element.style.opacity = '0.98';
        }} else if (touchesSelected || touchesPrimary) {{
          element.style.opacity = '0.42';
        }} else {{
          element.style.opacity = '0.05';
        }}
      }});
    }}

    function renderKeywordGraph() {{
      const board = document.getElementById('keyword-graph-board');
      if (!board) return;
      updateGraphModeButtons();
      const visible = getVisibleGraphPayload();
      const nodes = visible.nodes.slice();
      const edges = visible.edges.slice();
      if (!nodes.length) {{
        board.innerHTML = '<div class="graph-empty">还没有足够的标签共现数据来绘制关联图。</div>';
        renderKeywordPreview(null, []);
        return;
      }}

      const width = board.clientWidth || 900;
      const height = width < 720 ? 420 : 560;
      const maxCount = Math.max(...nodes.map((node) => Number(node.count) || 1), 1);
      const minCount = Math.min(...nodes.map((node) => Number(node.count) || 1), maxCount);
      const maxWeight = Math.max(...edges.map((edge) => Number(edge.weight) || 1), 1);
      const centerX = width / 2;
      const centerY = height / 2;
      const baseRadius = Math.max(Math.min(width, height) * 0.24, 120);
      const outerRadius = Math.max(Math.min(width, height) * 0.43, 180);

      nodes.sort((a, b) => (Number(b.count) || 0) - (Number(a.count) || 0) || (a.label || '').localeCompare(b.label || ''));

      nodes.forEach((node, index) => {{
        const count = Number(node.count) || 1;
        const ratio = maxCount === minCount ? 1 : (count - minCount) / (maxCount - minCount);
        node.radius = Math.round(22 + ratio * 32);
        node.fontSize = Math.round(12 + ratio * 12);
        node.fill = colorForNode(node, maxCount);
        node.mass = 1 + ratio * 2.2;
        const angle = index * 2.399963229728653;
        const spreadRatio = nodes.length <= 1 ? 0 : index / Math.max(nodes.length - 1, 1);
        const largeNodeBoost = (1 - spreadRatio) * 0.18;
        const radial = baseRadius + (outerRadius - baseRadius) * Math.sqrt(spreadRatio + largeNodeBoost);
        node.x = centerX + Math.cos(angle) * radial;
        node.y = centerY + Math.sin(angle) * radial;
        node.vx = 0;
        node.vy = 0;
      }});

      const nodeMap = new Map(nodes.map((node) => [node.id, node]));
      const layoutEdges = edges
        .map((edge) => {{
          const source = nodeMap.get(edge.source);
          const target = nodeMap.get(edge.target);
          if (!source || !target) return null;
          return {{
            ...edge,
            sourceNode: source,
            targetNode: target,
            strength: 0.03 + ((Number(edge.weight) || 1) / maxWeight) * 0.12,
            targetDistance: source.radius + target.radius + (150 - ((Number(edge.weight) || 1) / maxWeight) * 70),
          }};
        }})
        .filter(Boolean);

      for (let iteration = 0; iteration < 320; iteration += 1) {{
        for (const edge of layoutEdges) {{
          const a = edge.sourceNode;
          const b = edge.targetNode;
          const dx = b.x - a.x;
          const dy = b.y - a.y;
          const distance = Math.max(Math.hypot(dx, dy), 1);
          const pull = (distance - edge.targetDistance) * edge.strength;
          const offsetX = (dx / distance) * pull;
          const offsetY = (dy / distance) * pull;
          a.vx += offsetX;
          a.vy += offsetY;
          b.vx -= offsetX;
          b.vy -= offsetY;
        }}

        for (let i = 0; i < nodes.length; i += 1) {{
          for (let j = i + 1; j < nodes.length; j += 1) {{
            const a = nodes[i];
            const b = nodes[j];
            const dx = b.x - a.x;
            const dy = b.y - a.y;
            const distance = Math.max(Math.hypot(dx, dy), 1);
            const minDistance = a.radius + b.radius + 26 + Math.max(a.radius, b.radius) * 0.28;
            const overlapPush = distance < minDistance ? (minDistance - distance) * 0.11 : 0;
            const repulsion = ((a.mass || 1) * (b.mass || 1) * 115) / (distance * distance);
            const push = overlapPush + repulsion;
            const offsetX = (dx / distance) * push;
            const offsetY = (dy / distance) * push;
            a.vx -= offsetX;
            a.vy -= offsetY;
            b.vx += offsetX;
            b.vy += offsetY;
          }}
        }}

        for (const node of nodes) {{
          const towardCenterX = (centerX - node.x) * 0.0022;
          const towardCenterY = (centerY - node.y) * 0.0022;
          node.vx += towardCenterX;
          node.vy += towardCenterY;
          node.x += node.vx;
          node.y += node.vy;
          node.vx *= 0.82;
          node.vy *= 0.82;
          node.x = clamp(node.x, node.radius + 18, width - node.radius - 18);
          node.y = clamp(node.y, node.radius + 18, height - node.radius - 18);
        }}
      }}

      const edgeMarkup = layoutEdges.map((edge) => {{
        const opacity = (0.14 + ((Number(edge.weight) || 1) / maxWeight) * 0.62).toFixed(3);
        const strokeWidth = (0.8 + ((Number(edge.weight) || 1) / maxWeight) * 2.2).toFixed(2);
        return `
          <line
            class="graph-link"
            data-source="${{esc(edge.source)}}"
            data-target="${{esc(edge.target)}}"
            x1="${{edge.sourceNode.x.toFixed(1)}}"
            y1="${{edge.sourceNode.y.toFixed(1)}}"
            x2="${{edge.targetNode.x.toFixed(1)}}"
            y2="${{edge.targetNode.y.toFixed(1)}}"
            opacity="${{opacity}}"
            stroke-width="${{strokeWidth}}"
          />
        `;
      }}).join('');

      const nodeMarkup = nodes.map((node) => {{
        const label = esc(node.label || '');
        const shortLabel = label.length > 26 ? `${{label.slice(0, 24)}}…` : label;
        return `
          <g class="graph-node" data-node-id="${{esc(node.id)}}" data-node-slug="${{esc(node.slug)}}" tabindex="0" role="button" aria-label="预览关键词 ${{label}}">
            <title>${{label}} (${{node.count}})</title>
            <circle cx="${{node.x.toFixed(1)}}" cy="${{node.y.toFixed(1)}}" r="${{node.radius}}" fill="${{node.fill}}"></circle>
            <text x="${{node.x.toFixed(1)}}" y="${{node.y.toFixed(1)}}" font-size="${{node.fontSize}}">${{shortLabel}}</text>
          </g>
        `;
      }}).join('');

      board.innerHTML = `
        <svg viewBox="0 0 ${{width}} ${{height}}" preserveAspectRatio="xMidYMid meet" role="img" aria-label="Keyword relation graph">
          <g>${{edgeMarkup}}</g>
          <g>${{nodeMarkup}}</g>
        </svg>
      `;

      const svg = board.querySelector('svg');
      const links = Array.from(board.querySelectorAll('.graph-link'));
      const nodeElements = Array.from(board.querySelectorAll('.graph-node'));
      const selectedNode = nodes.find((node) => node.id === keywordGraphSelectedNodeId) || nodes[0];
      keywordGraphSelectedNodeId = selectedNode ? selectedNode.id : '';
      keywordGraphVisibleNodes = nodes.slice();
      keywordGraphVisibleEdges = edges.slice();
      if (!keywordGraphActiveFilterIds.size || !keywordGraphActiveFilterIds.has(keywordGraphSelectedNodeId)) {{
        keywordGraphActiveFilterIds = new Set(keywordGraphSelectedNodeId ? [keywordGraphSelectedNodeId] : []);
      }}
      renderKeywordPreview(selectedNode, edges);
      applyKeywordGraphSelectionState();
      applyKeywordCardFilter();

      function activateNode(nodeId) {{
        const node = nodes.find((item) => item.id === nodeId);
        if (!node) return;
        keywordGraphSelectedNodeId = node.id;
        keywordGraphActiveFilterIds = new Set([node.id]);
        renderKeywordPreview(node, edges);
        applyKeywordGraphSelectionState();
        applyKeywordCardFilter();
      }}

      for (const node of board.querySelectorAll('.graph-node')) {{
        node.addEventListener('mouseenter', () => {{
          const nodeId = node.getAttribute('data-node-id');
          const hoveredIds = new Set(keywordGraphActiveFilterIds.size ? keywordGraphActiveFilterIds : [keywordGraphSelectedNodeId]);
          if (nodeId) hoveredIds.add(nodeId);
          board.querySelectorAll('.graph-node').forEach((element) => {{
            const elementId = element.getAttribute('data-node-id') || '';
            element.classList.toggle('active', hoveredIds.has(elementId));
          }});
          board.querySelectorAll('.graph-link').forEach((element) => {{
            const source = element.dataset.source || '';
            const target = element.dataset.target || '';
            const related = source === nodeId || target === nodeId || (hoveredIds.has(source) && hoveredIds.has(target));
            element.style.opacity = related ? '0.92' : '0.05';
          }});
        }});
        node.addEventListener('mouseleave', () => {{
          applyKeywordGraphSelectionState();
        }});
        node.addEventListener('click', () => {{
          activateNode(node.getAttribute('data-node-id'));
        }});
        node.addEventListener('keydown', (event) => {{
          if (event.key === 'Enter' || event.key === ' ') {{
            event.preventDefault();
            activateNode(node.getAttribute('data-node-id'));
          }}
        }});
      }}

      if (svg) {{
        svg.addEventListener('mouseleave', () => {{
          applyKeywordGraphSelectionState();
        }});
      }}
    }}

    document.querySelectorAll('[data-graph-mode]').forEach((button) => {{
      button.addEventListener('click', () => {{
        const nextMode = button.dataset.graphMode || 'strong';
        if (nextMode === keywordGraphMode) return;
        keywordGraphMode = nextMode;
        renderKeywordGraph();
      }});
    }});

    const keywordGraphGroupSelect = document.getElementById('keyword-graph-group-filter');
    if (keywordGraphGroupSelect) {{
      keywordGraphGroupSelect.addEventListener('change', () => {{
        keywordGraphGroupFilter = keywordGraphGroupSelect.value || 'all';
        keywordGraphSelectedNodeId = '';
        renderKeywordGraph();
      }});
    }}

    window.addEventListener('load', () => {{
      renderGroupFilterOptions();
      renderKeywordGraph();
    }});
    window.addEventListener('resize', () => {{
      clearTimeout(window.__keywordGraphResizeTimer);
      window.__keywordGraphResizeTimer = setTimeout(renderKeywordGraph, 120);
    }});
  </script>
</body>
</html>"""


def build_keyword_detail_html(keyword: str):
    entry = get_keyword_entry(keyword)
    if not entry:
        return build_keywords_html()
    cards = []
    all_groups = list_reading_groups()
    group_options = "".join(f'<option value="{g["id"]}">{escape(g["name"])}</option>' for g in all_groups)
    payload_json = json.dumps(entry["papers"], ensure_ascii=False).replace("</script>", "<\\/script>")
    for idx, paper in enumerate(entry["papers"], start=1):
        source_kind = paper.get("source_kind") or "search"
        meta_parts = [
            f"CSV #{paper['csv_index']}" if paper.get("csv_index") else "",
            paper.get("venue") or "",
            str(paper.get("year") or ""),
            paper.get("authors") or "",
            f"来源：{'深度阅读' if source_kind == 'deep_reading' else ('搜索 ' + str(paper.get('source_slug') or ''))}",
        ]
        meta_text = " · ".join(part for part in meta_parts if part)
        doi_text = f"DOI: {escape(paper['doi'])}" if paper.get("doi") else ""
        link_html = f'<a href="{paper["url"]}" target="_blank" rel="noreferrer">原文链接</a>' if paper.get("url") else ""
        reading_link_html = (
            f'<a href="{paper["source_site_url"]}" target="_blank" rel="noreferrer">打开阅读页</a>'
            if source_kind == "deep_reading" and paper.get("source_site_url")
            else ""
        )
        title_text = html_unescape(paper['title'])
        content_text = html_unescape(paper.get('content') or '暂无内容')
        cards.append(
            f"""
            <article class="card">
              <h2>{escape(title_text)}</h2>
              <div class="meta">{escape(meta_text)}</div>
              <div class="meta">{doi_text}</div>
              <p>{escape(content_text)}</p>
              <div class="group-select-row" style="margin:12px 0;">
                <label>选择 Reading Group（可选）:</label>
                <select id="group-select-{idx}" style="margin-left:8px; padding:6px; border-radius:8px; border:1px solid #d5cbba;">
                  <option value="">-- 不选择 --</option>
                  {group_options}
                </select>
              </div>
              <div class="pdf-upload-row" style="margin:12px 0;">
                <label>上传 PDF（可选）:</label>
                <input type="file" id="pdf-input-{idx}" accept=".pdf" style="margin-left:8px;">
              </div>
              <div class="links">
                {link_html}
                {reading_link_html}
                <button class="action" type="button" onclick="addKeywordCitation({idx})">加入深度阅读</button>
                <button class="action secondary" type="button" onclick="expandKeywordPaper({idx})">扩展搜索</button>
              </div>
            </article>
            """
        )
    body = "\n".join(cards) if cards else '<div class="empty">这个关键词下暂时没有论文。</div>'
    return f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{escape(entry['keyword'])}</title>
  <style>
    body {{ margin:0; font-family: Georgia, "Noto Serif SC", serif; background:#f2efe8; color:#1e1d1a; }}
    .wrap {{ max-width:1040px; margin:0 auto; padding:28px 18px 72px; }}
    .hero, .card, .empty {{
      border:1px solid #d5cbba; border-radius:24px; background:rgba(255,251,244,0.96);
      box-shadow:0 18px 40px rgba(76,50,28,0.08);
    }}
    .hero {{ padding:28px; margin-bottom:20px; }}
    .row {{ display:flex; justify-content:space-between; gap:12px; flex-wrap:wrap; }}
    .actions {{ display:flex; gap:10px; flex-wrap:wrap; margin-top:14px; }}
    .actions a, .links a {{
      display:inline-block; background:#9c4f2f; color:white; text-decoration:none;
      padding:10px 14px; border-radius:999px;
    }}
    .links button {{
      display:inline-block; background:#9c4f2f; color:white; text-decoration:none;
      padding:10px 14px; border-radius:999px; border:none; cursor:pointer; font:inherit;
    }}
    .links button.secondary {{ background:#6f6455; }}
    h1 {{ margin:0 0 8px; font-size:40px; }}
    h2 {{ margin:0 0 8px; font-size:24px; line-height:1.3; }}
    .muted, .meta {{ color:#6f685c; line-height:1.7; font-size:14px; }}
    .list {{ display:grid; gap:16px; }}
    .card {{ padding:18px; }}
    p {{ line-height:1.8; }}
    .links {{ display:flex; gap:10px; flex-wrap:wrap; margin-top:14px; }}
    .empty {{ padding:24px; text-align:center; }}
    @media (max-width: 720px) {{
      .wrap {{ padding:22px 14px 56px; }}
      .hero {{ padding:22px 18px; }}
      .actions {{ width:100%; }}
      .actions a {{ width:100%; text-align:center; }}
      .card {{ padding:16px; }}
      .links a, .links button {{ width:100%; text-align:center; }}
      h1 {{ font-size:32px; }}
      h2 {{ font-size:20px; }}
    }}
  </style>
</head>
<body>
  <main class="wrap">
    <section class="hero">
      <div class="row">
        <div>
          <h1>{escape(entry['keyword'])}</h1>
          <div class="muted">共 {entry['count']} 篇论文。这里同时汇总原始搜索命中词，以及深度阅读分析后回写到文献上的关键词。</div>
        </div>
        <div class="actions">
          <a href="/keywords">返回 Keywords</a>
          <a href="/">返回时间线</a>
        </div>
      </div>
    </section>
    <section class="list">{body}</section>
  </main>
  <script id="keyword-papers" type="application/json">{payload_json}</script>
  <script>
    const keywordPapers = JSON.parse(document.getElementById('keyword-papers').textContent);
    let readingGroups = [];

    function escapeHtml(value) {{
      return (value || '').toString()
        .replaceAll('&', '&amp;')
        .replaceAll('<', '&lt;')
        .replaceAll('>', '&gt;')
        .replaceAll('"', '&quot;');
    }}

    function ensureCitationDialog() {{
      let dialog = document.getElementById('citation-dialog');
      if (dialog) return dialog;
      dialog = document.createElement('dialog');
      dialog.id = 'citation-dialog';
      dialog.style.maxWidth = '560px';
      dialog.style.width = 'calc(100vw - 24px)';
      dialog.style.border = '1px solid #d5cbba';
      dialog.style.borderRadius = '18px';
      dialog.style.padding = '0';
      dialog.innerHTML = `
        <form method="dialog" id="citation-form" style="padding:20px;">
          <h3 style="margin:0 0 12px; font-size:24px;">加入深度阅读</h3>
          <div id="citation-dialog-title" style="color:#6f685c; line-height:1.6; margin-bottom:14px;"></div>
          <label style="display:block; margin-bottom:10px;">
            <div style="margin-bottom:6px; color:#6f685c;">Reading Group</div>
            <select id="citation-group-select" style="width:100%; padding:10px 12px; border-radius:12px; border:1px solid #d5cbba;">
              <option value="">暂不加入 Group</option>
            </select>
          </label>
          <div id="citation-source-link-box" style="display:none; margin-bottom:14px; padding:12px; border:1px dashed #d5cbba; border-radius:14px; background:#fffaf4;">
            <div style="margin-bottom:8px; color:#6f685c;">请先打开原文链接手动下载 PDF，再上传到深度阅读。</div>
            <a id="citation-source-link" href="#" target="_blank" rel="noreferrer" style="display:none; border:none; background:#9c4f2f; color:white; padding:10px 14px; border-radius:999px; text-decoration:none;">打开原文链接</a>
          </div>
          <label style="display:block; margin-bottom:14px;">
            <div style="margin-bottom:6px; color:#6f685c;">上传 PDF（必填）</div>
            <input id="citation-pdf-input" type="file" accept="application/pdf,.pdf" style="width:100%;">
          </label>
          <div id="citation-progress-box" style="display:none; margin:-4px 0 14px;">
            <div style="height:10px; border-radius:999px; background:#eadfce; overflow:hidden;">
              <div id="citation-progress-bar" style="width:0%; height:100%; background:#9c4f2f; transition:width .2s ease;"></div>
            </div>
            <div id="citation-progress-text" style="margin-top:8px; color:#6f685c; font-size:14px;">等待开始...</div>
          </div>
          <div id="citation-status" style="display:none; margin:-4px 0 14px; padding:10px 12px; border-radius:12px; background:#f5ede4; color:#6b4b39; line-height:1.6;"></div>
          <div style="display:flex; gap:10px; flex-wrap:wrap;">
            <button id="citation-submit" type="submit" value="submit" style="border:none; background:#9c4f2f; color:white; padding:10px 14px; border-radius:999px; cursor:pointer;">保存并解析</button>
            <button id="citation-cancel" type="submit" value="cancel" style="border:none; background:#6f6455; color:white; padding:10px 14px; border-radius:999px; cursor:pointer;">取消</button>
          </div>
        </form>
      `;
      document.body.appendChild(dialog);
      return dialog;
    }}

    async function loadReadingGroups() {{
      const resp = await fetch('/api/reading-groups', {{ credentials: 'same-origin' }});
      const data = await resp.json().catch(() => ({{ ok: false, groups: [] }}));
      readingGroups = data.ok ? (data.groups || []) : [];
    }}

    async function submitCitation(searchSlug, paper, preferredGroupName = '') {{
      const dialog = ensureCitationDialog();
      document.getElementById('citation-dialog-title').textContent = paper.title || '';
      const select = document.getElementById('citation-group-select');
      const fileInput = document.getElementById('citation-pdf-input');
      const progressBox = document.getElementById('citation-progress-box');
      const progressBar = document.getElementById('citation-progress-bar');
      const progressText = document.getElementById('citation-progress-text');
      const statusBox = document.getElementById('citation-status');
      const sourceLinkBox = document.getElementById('citation-source-link-box');
      const sourceLink = document.getElementById('citation-source-link');
      const submitButton = document.getElementById('citation-submit');
      const cancelButton = document.getElementById('citation-cancel');
      select.innerHTML = '<option value="">暂不加入 Group</option>' + readingGroups.map(
        (group) => `<option value="${{group.id}}">${{escapeHtml(group.name)}}</option>`
      ).join('');
      const preferred = (preferredGroupName || '').toString().trim().toLowerCase();
      if (preferred) {{
        const matchedGroup = readingGroups.find((group) => (group.name || '').toString().trim().toLowerCase() === preferred);
        if (matchedGroup) select.value = String(matchedGroup.id);
      }}
      fileInput.value = '';
      progressBox.style.display = 'none';
      progressBar.style.width = '0%';
      progressText.textContent = '等待开始...';
      statusBox.style.display = 'none';
      statusBox.textContent = '';
      const sourceUrl = (paper.url || '').toString().trim();
      sourceLinkBox.style.display = 'block';
      sourceLink.href = sourceUrl || '#';
      sourceLink.style.display = sourceUrl ? 'inline-flex' : 'none';
      submitButton.disabled = false;
      cancelButton.disabled = false;
      const result = await new Promise((resolve) => {{
        const form = document.getElementById('citation-form');

        const setBusy = (busy) => {{
          submitButton.disabled = busy;
          cancelButton.disabled = busy;
        }};

        const cleanup = () => {{
          form.removeEventListener('submit', handler);
        }};

        const handler = async (event) => {{
          event.preventDefault();
          const submitterValue = event.submitter && event.submitter.value;
          if (submitterValue !== 'submit') {{
            cleanup();
            dialog.close();
            resolve(null);
            return;
          }}
          if (!fileInput.files[0]) {{
            statusBox.textContent = '请先从原文链接下载 PDF，然后上传 PDF 后再加入深度阅读。';
            statusBox.style.display = 'block';
            return;
          }}
          setBusy(true);
          progressBox.style.display = 'block';
          progressBar.style.width = '0%';
          progressText.textContent = '准备上传 PDF...';
          const formData = new FormData();
          formData.append('search_slug', searchSlug || '');
          formData.append('paper', JSON.stringify(paper));
          if (select.value) formData.append('group_id', select.value);
          formData.append('pdf', fileInput.files[0]);
          try {{
            const data = await uploadWithProgress('/api/citations', formData, (percent, text) => {{
              progressBar.style.width = percent + '%';
              progressText.textContent = text;
            }});
            progressBar.style.width = '100%';
            progressText.textContent = 'PDF 已上传，正在启动深度解析...';
            cleanup();
            dialog.close();
            resolve(data);
          }} catch (error) {{
            cleanup();
            dialog.close();
            resolve({{ error: error.message || '请求失败' }});
          }}
        }};

        form.addEventListener('submit', handler);
        dialog.showModal();
      }});
      return result;
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

    async function apiGet(path) {{
      const controller = new AbortController();
      const timer = setTimeout(() => controller.abort(), 15000);
      let resp;
      try {{
        resp = await fetch(path, {{
          credentials: 'same-origin',
          signal: controller.signal,
        }});
      }} catch (error) {{
        if (error && error.name === 'AbortError') {{
          throw new Error('扩展搜索请求超时，请稍后重试');
        }}
        throw error;
      }} finally {{
        clearTimeout(timer);
      }}
      const data = await resp.json().catch(() => ({{ ok: false, error: '请求失败' }}));
      if (!resp.ok || data.ok === false) {{
        throw new Error(data.error || '请求失败');
      }}
      return data;
    }}

    function sleep(ms) {{
      return new Promise((resolve) => setTimeout(resolve, ms));
    }}

    const keywordExpansionStatus = (() => {{
      const el = document.createElement('div');
      el.style.position = 'fixed';
      el.style.right = '20px';
      el.style.bottom = '20px';
      el.style.maxWidth = '360px';
      el.style.padding = '10px 14px';
      el.style.borderRadius = '12px';
      el.style.background = 'rgba(61, 50, 39, 0.92)';
      el.style.color = '#fffaf2';
      el.style.fontSize = '14px';
      el.style.lineHeight = '1.5';
      el.style.boxShadow = '0 10px 30px rgba(43, 35, 25, 0.18)';
      el.style.zIndex = '9999';
      el.style.display = 'none';
      document.body.appendChild(el);
      return el;
    }})();

    function showKeywordExpansionStatus(message) {{
      const textValue = text(message).trim();
      if (!textValue) return;
      keywordExpansionStatus.textContent = textValue;
      keywordExpansionStatus.style.display = 'block';
    }}

    function hideKeywordExpansionStatus() {{
      keywordExpansionStatus.style.display = 'none';
      keywordExpansionStatus.textContent = '';
    }}

    async function waitReferenceExpansionJob(jobId) {{
      const deadline = Date.now() + 20 * 60 * 1000;
      while (Date.now() < deadline) {{
        const data = await apiGet(`/api/papers/expand-references/jobs/${{encodeURIComponent(jobId)}}`);
        const job = data.job || {{}};
        const status = text(job.status).trim().toLowerCase();
        const message = text(job.step_message).trim();
        if (message) {{
          showKeywordExpansionStatus(message);
        }}
        if (status === 'completed') return job;
        if (status === 'failed') {{
          throw new Error(job.error || job.step_message || '扩展搜索失败');
        }}
        await sleep(1200);
      }}
      throw new Error('扩展搜索等待超时，请稍后到时间线中查看结果');
    }}

    async function addKeywordCitation(index) {{
      const paper = keywordPapers[index - 1];
      if (!paper) return;
      try {{
        if (!readingGroups.length) {{
          await loadReadingGroups();
        }}
        const data = await submitCitation(paper.source_output_slug || paper.source_slug || '', paper, paper.source_group_name || '');
        if (!data) return;
        if (data.error) throw new Error(data.error);
        if (data.reading_url) {{
          window.open(data.reading_url, '_blank', 'noopener');
        }}
        alert(data.message || '已加入深度阅读。');
      }} catch (error) {{
        alert(error.message);
      }}
    }}

    async function expandKeywordPaper(index) {{
      const paper = keywordPapers[index - 1];
      if (!paper) return;
      try {{
        showKeywordExpansionStatus('已进入扩展搜索队列，正在准备结果...');
        const started = await apiPost('/api/papers/expand-references', {{
          search_slug: paper.source_slug || '',
          paper
        }});
        const job = started.job || {{}};
        if (!job.id) {{
          throw new Error('扩展搜索任务创建失败');
        }}
        const finished = job.status === 'completed' ? job : await waitReferenceExpansionJob(job.id);
        if (!finished.site_url) {{
          throw new Error('扩展搜索结果未生成链接');
        }}
        hideKeywordExpansionStatus();
        window.open(finished.site_url, '_blank', 'noopener');
      }} catch (error) {{
        hideKeywordExpansionStatus();
        alert(error.message);
      }}
    }}

    window.addKeywordCitation = addKeywordCitation;
    window.expandKeywordPaper = expandKeywordPaper;
    loadReadingGroups().catch(() => {{}});
  </script>
</body>
</html>"""


def build_keyword_intersection_html(keywords: list[str]):
    entry = build_keyword_intersection_entry(keywords)
    selected = entry.get("selected_keywords") or []
    if not selected:
        return build_keywords_html()
    cards = []
    for paper in entry["papers"]:
        source_kind = paper.get("source_kind") or "search"
        meta_parts = [
            paper.get("venue") or "",
            str(paper.get("year") or ""),
            paper.get("authors") or "",
            f"来源：{'深度阅读' if source_kind == 'deep_reading' else ('搜索 ' + str(paper.get('source_slug') or ''))}",
        ]
        meta_text = " · ".join(part for part in meta_parts if part)
        doi_text = f"DOI: {escape(paper['doi'])}" if paper.get("doi") else ""
        link_html = f'<a href="{paper["url"]}" target="_blank" rel="noreferrer">原文链接</a>' if paper.get("url") else ""
        reading_link_html = (
            f'<a href="{paper["source_site_url"]}" target="_blank" rel="noreferrer">打开结果页</a>'
            if paper.get("source_site_url")
            else ""
        )
        title_text = html_unescape(paper["title"])
        content_text = html_unescape((paper.get("content") or "").strip() or "暂无内容")
        tags_text = " · ".join(paper.get("keywords") or [])
        cards.append(
            f"""
            <article class="card">
              <h2>{escape(title_text)}</h2>
              <div class="meta">{escape(meta_text)}</div>
              <div class="meta">{doi_text}</div>
              <div class="meta">关键词：{escape(tags_text)}</div>
              <p>{escape(content_text)}</p>
              <div class="links">
                {link_html}
                {reading_link_html}
              </div>
            </article>
            """
        )
    body = "\n".join(cards) if cards else '<div class="empty">当前没有论文同时命中这组 tags。</div>'
    query_text = " + ".join(selected)
    return f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{escape(query_text)}</title>
  <style>
    body {{ margin:0; font-family: Georgia, "Noto Serif SC", serif; background:#f2efe8; color:#1e1d1a; }}
    .wrap {{ max-width:1040px; margin:0 auto; padding:28px 18px 72px; }}
    .hero, .card, .empty {{
      border:1px solid #d5cbba; border-radius:24px; background:rgba(255,251,244,0.96);
      box-shadow:0 18px 40px rgba(76,50,28,0.08);
    }}
    .hero {{ padding:28px; margin-bottom:20px; }}
    .row {{ display:flex; justify-content:space-between; gap:12px; flex-wrap:wrap; }}
    .actions {{ display:flex; gap:10px; flex-wrap:wrap; margin-top:14px; }}
    .actions a, .links a {{
      display:inline-block; background:#9c4f2f; color:white; text-decoration:none;
      padding:10px 14px; border-radius:999px;
    }}
    h1 {{ margin:0 0 8px; font-size:36px; line-height:1.18; }}
    h2 {{ margin:0 0 8px; font-size:24px; line-height:1.3; }}
    .muted, .meta {{ color:#6f685c; line-height:1.7; font-size:14px; }}
    .list {{ display:grid; gap:16px; }}
    .card {{ padding:18px; }}
    p {{ line-height:1.8; }}
    .links {{ display:flex; gap:10px; flex-wrap:wrap; margin-top:14px; }}
    .empty {{ padding:24px; text-align:center; }}
  </style>
</head>
<body>
  <main class="wrap">
    <section class="hero">
      <div class="row">
        <div>
          <h1>{escape(query_text)}</h1>
          <div class="muted">共 {entry['count']} 篇论文同时命中这组 tags。这里适合快速查看多标签交叉区域里的论文集合。</div>
        </div>
        <div class="actions">
          <a href="/keywords">返回 Keywords</a>
          <a href="/">返回时间线</a>
        </div>
      </div>
    </section>
    <section class="list">{body}</section>
  </main>
</body>
</html>"""


def build_login_html(error: str = "", has_users: bool = True):
    error_html = f'<div class="error">{error}</div>' if error else ""
    hint_html = (
        '<p>这个站点需要用户名和密码才能访问搜索结果、深度阅读模块和扩展引用功能。</p>'
        if has_users
        else '<p>当前还没有任何账号。请先运行 <code>python set_site_password.py --username admin --password &lt;your-password&gt;</code> 创建首个用户。</p>'
    )
    return f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Login</title>
  <style>
    body {{
      margin:0; min-height:100vh; display:grid; place-items:center;
      font-family: Georgia, "Noto Serif SC", serif;
      background: radial-gradient(circle at top left, #ead8ca 0, transparent 24rem), #f2efe8;
      color:#221f1b;
    }}
    .card {{
      width:min(92vw, 420px); padding:28px; border-radius:24px; background:rgba(255,251,244,0.96);
      border:1px solid #d5cbba; box-shadow:0 18px 40px rgba(76,50,28,0.08);
    }}
    h1 {{ margin:0 0 10px; font-size:34px; }}
    p {{ color:#6f685c; line-height:1.7; }}
    input, button {{
      width:100%; font:inherit; padding:14px 16px; border-radius:16px; box-sizing:border-box;
    }}
    input {{ border:1px solid #d5cbba; margin-top:8px; background:white; }}
    button {{
      margin-top:14px; border:none; background:#9c4f2f; color:white; cursor:pointer;
    }}
    .error {{ margin-top:10px; color:#a12d2d; font-size:14px; }}
    code {{ font-family: "SFMono-Regular", Consolas, monospace; font-size: 13px; }}
  </style>
</head>
<body>
  <form class="card" method="post" action="/api/auth/login">
    <h1>Private Site</h1>
    {hint_html}
    <input name="username" type="text" placeholder="输入用户名" autocomplete="username" required>
    <input name="password" type="password" placeholder="输入密码" autocomplete="current-password" required>
    <button type="submit">登录</button>
    {error_html}
  </form>
</body>
</html>"""


def build_reading_detail_html(paper_id: str):
    bundle = load_reading_bundle(paper_id)
    if not bundle:
        return "<!doctype html><html lang='zh-CN'><body><h1>未找到阅读页</h1></body></html>"
    paper = bundle["paper"]
    analysis = bundle["analysis"]
    modules = analysis.get("modules") or {}
    overview = (modules.get("overview") or {}).get("data") or {}
    problem = (modules.get("problem") or {}).get("data") or {}
    method = (modules.get("method") or {}).get("data") or {}
    results = (modules.get("results") or {}).get("data") or {}
    critique = (modules.get("critique") or {}).get("data") or {}
    qa_history = bundle.get("qa_history") or []
    notes = bundle.get("notes") or {}

    def render_list(items, empty="等待分析生成"):
        values = items or []
        return "".join(f"<li>{escape(str(item))}</li>" for item in values) or f"<li>{empty}</li>"

    logic = "".join(
        f"<li><strong>{escape(str(item.get('step', '')))}. {escape(item.get('label') or '')}</strong><div>{escape(item.get('content') or '')}</div></li>"
        for item in (problem.get("paper_logic") or [])
    ) or "<li>等待分析生成</li>"
    findings = "".join(
        f"<li><strong>{escape(item.get('id') or '')}</strong> {escape(item.get('claim') or '')}<div>{escape(item.get('evidence') or '')}</div></li>"
        for item in (results.get("findings") or [])
    ) or "<li>等待分析生成</li>"
    qa_history_html = "".join(
        f"""
        <article class="qa-item" data-qa-id="{escape(item.get("id") or "")}">
          <div class="qa-meta">{escape(item.get("created_at") or "")}</div>
          <div class="qa-q"><strong>问：</strong>{escape(item.get("question") or "")}</div>
          <div class="qa-a"><strong>答：</strong>{escape(item.get("answer") or "").replace(chr(10), "<br>")}</div>
          <div class="qa-toolbar"><button class="delete-qa" type="button" data-qa-id="{escape(item.get("id") or "")}">删除这条提问</button></div>
        </article>
        """
        for item in reversed(qa_history)
    ) or '<div class="meta" id="qa-empty">还没有提问记录，先问一个你关心的问题吧。</div>'
    pdf_path = ((paper.get("pdf") or {}).get("file_path") or "").strip()
    pdf_link = f'<a href="{escape(pdf_path)}" target="_blank" rel="noreferrer">打开 PDF</a>' if pdf_path else ""
    metadata_status = ((paper.get("status") or {}).get("metadata") or "pending").strip()
    metadata_message = (paper.get("status") or {}).get("metadata_message") or ""
    metadata_label = "重新识别元数据" if metadata_status in {"completed", "failed"} else "识别元数据"
    analysis_status = ((paper.get("status") or {}).get("analysis") or "pending").strip()
    analysis_progress = int(((paper.get("status") or {}).get("analysis_progress") or 0) or 0)
    analysis_message = (paper.get("status") or {}).get("analysis_message") or ""
    has_analysis_content = any(
        bool(((modules.get(name) or {}).get("data") or {}))
        for name in ("overview", "problem", "method", "results", "critique")
    )
    analyze_label = "重新分析" if analysis_status == "completed" or has_analysis_content else "开始分析"

    return f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{escape(paper.get("title") or "Deep Reading")}</title>
  <style>
    body {{ margin:0; font-family: Georgia, "Noto Serif SC", serif; background:#f3efe7; color:#1f1c18; }}
    .wrap {{ max-width:1080px; margin:0 auto; padding:28px 18px 72px; }}
    .top-nav {{ display:flex; gap:10px; flex-wrap:wrap; margin-bottom:12px; }}
    .hero, .section {{ border:1px solid #d5cbba; border-radius:24px; background:rgba(255,251,244,0.96); box-shadow:0 18px 40px rgba(76,50,28,0.08); }}
    .hero {{ padding:28px; margin-bottom:18px; }}
    .section {{ padding:22px; margin-top:16px; }}
    .actions, .meta, .grid {{ display:flex; gap:10px; flex-wrap:wrap; }}
    .top-nav a, .actions a {{ display:inline-block; background:#9c4f2f; color:white; text-decoration:none; padding:10px 14px; border-radius:999px; }}
    .meta {{ color:#6f685c; line-height:1.8; font-size:14px; margin-top:8px; }}
    .progress-shell {{ margin-top:14px; border:1px solid #e3d8c8; border-radius:16px; padding:12px 14px; background:#fffdfa; }}
    .progress-row {{ display:flex; justify-content:space-between; gap:12px; align-items:center; flex-wrap:wrap; }}
    .progress-track {{ width:100%; height:10px; border-radius:999px; background:#ead8ca; overflow:hidden; margin-top:10px; }}
    .progress-bar {{ height:100%; width:0%; background:linear-gradient(90deg, #c8733f, #9c4f2f); }}
    .grid.cards {{ display:grid; grid-template-columns:repeat(auto-fit, minmax(240px, 1fr)); gap:12px; }}
    .card {{ border:1px solid #e3d8c8; border-radius:18px; padding:14px; background:#fffdfa; }}
    .qa-form {{ display:grid; gap:10px; }}
    .qa-form textarea {{ width:100%; min-height:110px; resize:vertical; border:1px solid #d5cbba; border-radius:16px; padding:12px 14px; font:inherit; background:#fffdfa; }}
    .qa-actions {{ display:flex; gap:10px; align-items:center; flex-wrap:wrap; }}
    .qa-actions button {{ display:inline-block; background:#9c4f2f; color:white; text-decoration:none; padding:10px 14px; border-radius:999px; border:none; font:inherit; cursor:pointer; }}
    .qa-list {{ display:grid; gap:12px; margin-top:16px; }}
    .qa-item {{ border:1px solid #e3d8c8; border-radius:18px; padding:14px; background:#fffdfa; }}
    .qa-meta {{ color:#8a7d6a; font-size:13px; margin-bottom:8px; }}
    .qa-q, .qa-a {{ line-height:1.8; }}
    .qa-a {{ margin-top:8px; }}
    .qa-toolbar {{ margin-top:10px; }}
    .qa-toolbar button, .note-box button {{ border:none; background:#6f6455; color:white; padding:8px 12px; border-radius:999px; cursor:pointer; font:inherit; }}
    .note-box {{ margin-top:14px; border:1px solid #e3d8c8; border-radius:18px; padding:14px; background:#fffdfa; }}
    .note-box textarea {{ width:100%; min-height:110px; resize:vertical; border:1px solid #d5cbba; border-radius:14px; padding:12px 14px; font:inherit; background:white; }}
    .note-toolbar {{ display:flex; gap:10px; align-items:center; flex-wrap:wrap; margin-top:10px; }}
    .cols {{ display:grid; grid-template-columns:1fr 1fr; gap:14px; }}
    h1 {{ margin:0 0 8px; font-size:42px; line-height:1.1; }}
    h2 {{ margin:0 0 10px; font-size:28px; }}
    h3 {{ margin:0 0 8px; font-size:20px; }}
    p, li {{ line-height:1.8; }}
    ul {{ margin:0; padding-left:20px; }}
    @media (max-width: 720px) {{ .cols {{ grid-template-columns:1fr; }} .top-nav a, .actions a {{ width:100%; text-align:center; }} h1 {{ font-size:32px; }} }}
  </style>
</head>
<body>
  <main class="wrap">
    <div class="top-nav">
      <a href="/">返回时间线</a>
    </div>
    <section class="hero">
      <div class="actions"><a href="/reading">返回深度阅读</a>{pdf_link}<a href="#" id="run-metadata">{metadata_label}</a><a href="#" id="run-analysis">{analyze_label}</a></div>
      <h1>{escape(paper.get("title") or "Untitled Paper")}</h1>
      <div class="meta">{escape(", ".join(paper.get("authors") or []) or "未知作者")} · {escape(str(paper.get("venue") or "未知 venue"))} · {escape(str(paper.get("year") or "未知年份"))}</div>
      <div class="meta">Theme: {escape(overview.get("research_theme") or "待生成")} · DOI: {escape(paper.get("doi") or "无")} · Analysis: {escape(analysis_status)}</div>
      <div class="progress-shell" id="metadata-progress-shell">
        <div class="progress-row">
          <strong>元数据识别</strong>
          <span id="metadata-stage">{escape(metadata_status)}</span>
        </div>
        <div class="meta" id="metadata-message">{escape(metadata_message or "等待元数据识别。")}</div>
      </div>
      <div class="progress-shell" id="analysis-progress-shell">
        <div class="progress-row">
          <strong id="analysis-stage">{escape(analysis_status)}</strong>
          <span id="analysis-percent">{analysis_progress}%</span>
        </div>
        <div class="meta" id="analysis-message">{escape(analysis_message or "准备开始分析。")}</div>
        <div class="progress-track"><div class="progress-bar" id="analysis-progress-bar" style="width:{analysis_progress}%;"></div></div>
      </div>
    </section>
    <section class="section">
      <h2>Overview</h2>
      <div class="grid cards">
        <div class="card"><h3>Paper Type</h3><p>{escape(overview.get("paper_type") or "等待分析生成")}</p></div>
        <div class="card"><h3>Core Problem</h3><p>{escape(overview.get("core_problem") or "等待分析生成")}</p></div>
        <div class="card"><h3>Core Approach</h3><p>{escape(overview.get("core_approach") or "等待分析生成")}</p></div>
      </div>
      <div class="card" style="margin-top:12px;"><h3>Contributions</h3><ul>{render_list(overview.get("contributions"))}</ul></div>
      <div class="note-box">
        <h3>Overview Notes</h3>
        <textarea class="module-note-input" data-module="overview" placeholder="手工记录你对 Overview 的阅读笔记...">{escape(notes.get("overview") or "")}</textarea>
        <div class="note-toolbar"><button class="save-note" type="button" data-module="overview">保存 Notes</button><span class="meta module-note-status" data-module="overview">手工 Notes 会保存在当前论文下。</span></div>
      </div>
    </section>
    <section class="section">
      <h2>Problem</h2>
      <div class="grid cards">
        <div class="card"><h3>Background</h3><p>{escape(problem.get("background") or "等待分析生成")}</p></div>
        <div class="card"><h3>Gap</h3><p>{escape(problem.get("gap") or "等待分析生成")}</p></div>
        <div class="card"><h3>Importance</h3><p>{escape(problem.get("importance") or "等待分析生成")}</p></div>
        <div class="card"><h3>Goal</h3><p>{escape(problem.get("research_goal") or "等待分析生成")}</p></div>
      </div>
      <div class="card" style="margin-top:12px;"><h3>Paper Logic</h3><ul>{logic}</ul></div>
      <div class="note-box">
        <h3>Problem Notes</h3>
        <textarea class="module-note-input" data-module="problem" placeholder="手工记录你对 Problem 的阅读笔记...">{escape(notes.get("problem") or "")}</textarea>
        <div class="note-toolbar"><button class="save-note" type="button" data-module="problem">保存 Notes</button><span class="meta module-note-status" data-module="problem">手工 Notes 会保存在当前论文下。</span></div>
      </div>
    </section>
    <section class="section">
      <h2>Method</h2>
      <div class="grid cards">
        <div class="card"><h3>Object</h3><p>{escape(method.get("object_of_study") or "等待分析生成")}</p></div>
        <div class="card"><h3>Method Goal</h3><p>{escape(method.get("method_goal") or "等待分析生成")}</p></div>
        <div class="card"><h3>Participants / Data</h3><p>{escape(method.get("participants_or_data") or "等待分析生成")}</p></div>
        <div class="card"><h3>Evaluation</h3><p>{escape(method.get("evaluation_setup") or "等待分析生成")}</p></div>
      </div>
      <div class="card" style="margin-top:12px;"><h3>Pipeline</h3><ul>{render_list(method.get("pipeline"))}</ul></div>
      <div class="note-box">
        <h3>Method Notes</h3>
        <textarea class="module-note-input" data-module="method" placeholder="手工记录你对 Method 的阅读笔记...">{escape(notes.get("method") or "")}</textarea>
        <div class="note-toolbar"><button class="save-note" type="button" data-module="method">保存 Notes</button><span class="meta module-note-status" data-module="method">手工 Notes 会保存在当前论文下。</span></div>
      </div>
    </section>
    <section class="section">
      <h2>Results</h2>
      <div class="card"><h3>Claim-Evidence Match</h3><p>{escape(results.get("claim_evidence_match") or "等待分析生成")}</p></div>
      <div class="card" style="margin-top:12px;"><h3>Findings</h3><ul>{findings}</ul></div>
      <div class="note-box">
        <h3>Results Notes</h3>
        <textarea class="module-note-input" data-module="results" placeholder="手工记录你对 Results 的阅读笔记...">{escape(notes.get("results") or "")}</textarea>
        <div class="note-toolbar"><button class="save-note" type="button" data-module="results">保存 Notes</button><span class="meta module-note-status" data-module="results">手工 Notes 会保存在当前论文下。</span></div>
      </div>
    </section>
    <section class="section">
      <h2>Critique</h2>
      <div class="cols">
        <div class="card"><h3>Strengths</h3><ul>{render_list(critique.get("strengths"))}</ul></div>
        <div class="card"><h3>Limitations</h3><ul>{render_list(critique.get("limitations"))}</ul></div>
      </div>
      <div class="note-box">
        <h3>Critique Notes</h3>
        <textarea class="module-note-input" data-module="critique" placeholder="手工记录你对 Critique 的阅读笔记...">{escape(notes.get("critique") or "")}</textarea>
        <div class="note-toolbar"><button class="save-note" type="button" data-module="critique">保存 Notes</button><span class="meta module-note-status" data-module="critique">手工 Notes 会保存在当前论文下。</span></div>
      </div>
    </section>
    <section class="section">
      <h2>提问</h2>
      <div class="qa-form">
        <textarea id="qa-question" placeholder="比如：这篇论文的方法创新点是什么？实验设计有哪些局限？"></textarea>
        <div class="qa-actions">
          <button id="ask-question" type="button">提交问题</button>
          <span class="meta" id="qa-status">提问内容会保存到当前论文的阅读历史中。</span>
        </div>
      </div>
      <div class="qa-list" id="qa-history">{qa_history_html}</div>
    </section>
  </main>
  <script>
    const runBtn = document.getElementById('run-analysis');
    const metadataBtn = document.getElementById('run-metadata');
    const progressBar = document.getElementById('analysis-progress-bar');
    const progressPercent = document.getElementById('analysis-percent');
    const progressStage = document.getElementById('analysis-stage');
    const progressMessage = document.getElementById('analysis-message');
    const metadataStage = document.getElementById('metadata-stage');
    const metadataMessage = document.getElementById('metadata-message');
    let pollingTimer = null;
    let lastMetadataState = '{metadata_status}';
    const askBtn = document.getElementById('ask-question');
    const qaQuestion = document.getElementById('qa-question');
    const qaStatus = document.getElementById('qa-status');
    const qaHistory = document.getElementById('qa-history');
    const noteButtons = Array.from(document.querySelectorAll('.save-note'));

    function escapeHtml(value) {{
      return String(value || '')
        .replaceAll('&', '&amp;')
        .replaceAll('<', '&lt;')
        .replaceAll('>', '&gt;')
        .replaceAll('"', '&quot;')
        .replaceAll("'", '&#39;');
    }}

    function appendQaItem(item) {{
      if (!qaHistory) return;
      const empty = document.getElementById('qa-empty');
      if (empty) empty.remove();
      const wrapper = document.createElement('article');
      wrapper.className = 'qa-item';
      wrapper.innerHTML = `
        <div class="qa-meta">${{escapeHtml(item.created_at || '')}}</div>
        <div class="qa-q"><strong>问：</strong>${{escapeHtml(item.question || '')}}</div>
        <div class="qa-a"><strong>答：</strong>${{escapeHtml(item.answer || '').replaceAll('\\n', '<br>')}}</div>
        <div class="qa-toolbar"><button class="delete-qa" type="button" data-qa-id="${{escapeHtml(item.id || '')}}">删除这条提问</button></div>
      `;
      qaHistory.prepend(wrapper);
    }}

    function renderStatus(payload) {{
      if (metadataStage) metadataStage.textContent = payload.metadata || 'pending';
      if (metadataMessage) metadataMessage.textContent = payload.metadata_message || '等待元数据识别。';
      if (metadataBtn) {{
        if (payload.metadata === 'processing') {{
          metadataBtn.textContent = '识别中...';
          metadataBtn.style.pointerEvents = 'none';
          metadataBtn.style.opacity = '0.7';
        }} else {{
          metadataBtn.textContent = payload.metadata === 'completed' || payload.metadata === 'failed' ? '重新识别元数据' : '识别元数据';
          metadataBtn.style.pointerEvents = '';
          metadataBtn.style.opacity = '';
        }}
      }}
      const progress = Number(payload.analysis_progress || 0);
      if (progressBar) progressBar.style.width = progress + '%';
      if (progressPercent) progressPercent.textContent = progress + '%';
      if (progressStage) progressStage.textContent = payload.analysis || 'pending';
      if (progressMessage) progressMessage.textContent = payload.analysis_message || '准备开始分析。';
      if (runBtn) {{
        if (payload.analysis === 'in_progress') {{
          runBtn.textContent = '分析中...';
          runBtn.style.pointerEvents = 'none';
          runBtn.style.opacity = '0.7';
        }} else {{
          runBtn.textContent = payload.analysis === 'completed' ? '重新分析' : '开始分析';
          runBtn.style.pointerEvents = '';
          runBtn.style.opacity = '';
        }}
      }}
    }}

    async function fetchStatus() {{
      const resp = await fetch('/api/reading/{paper_id}/status', {{
        credentials: 'same-origin'
      }});
      const data = await resp.json().catch(() => ({{ ok:false, error:'状态获取失败' }}));
      if (resp.status === 401) {{
        const error = new Error(data.error || 'unauthorized');
        error.code = 'unauthorized';
        throw error;
      }}
      if (!resp.ok || data.ok === false) {{
        throw new Error(data.error || '状态获取失败');
      }}
      return data.status;
    }}

    async function pollStatus() {{
      try {{
        const status = await fetchStatus();
        const previousMetadataState = lastMetadataState;
        lastMetadataState = status.metadata || 'pending';
        renderStatus(status);
        if (previousMetadataState === 'processing' && status.metadata === 'completed') {{
          window.location.reload();
          return;
        }}
        if (status.analysis === 'in_progress' || status.metadata === 'processing') {{
          pollingTimer = window.setTimeout(pollStatus, 2000);
          return;
        }}
        pollingTimer = null;
        if (status.analysis === 'completed') {{
          window.location.reload();
          return;
        }}
        if (status.analysis === 'failed') {{
          alert(status.analysis_message || '分析失败');
        }}
      }} catch (error) {{
        if (error && error.code === 'unauthorized') {{
          window.location.href = '/login';
          return;
        }}
        pollingTimer = window.setTimeout(pollStatus, 3000);
      }}
    }}

    if (runBtn) {{
      runBtn.addEventListener('click', async (event) => {{
        event.preventDefault();
        renderStatus({{ analysis: 'in_progress', analysis_progress: 5, analysis_message: '已提交分析任务，正在排队。' }});
        const resp = await fetch('/api/reading/{paper_id}/analyze', {{
          method: 'POST',
          credentials: 'same-origin'
        }});
        const data = await resp.json().catch(() => ({{ ok:false, error:'分析失败' }}));
        if (!resp.ok || data.ok === false) {{
          alert(data.error || '分析失败');
          renderStatus({{ analysis: '{analysis_status}', analysis_progress: {analysis_progress}, analysis_message: '{escape(analysis_message or "准备开始分析。")}' }});
          return;
        }}
        renderStatus(data.status || {{ analysis: 'in_progress', analysis_progress: 5, analysis_message: '已提交分析任务。' }});
        if (!pollingTimer) pollStatus();
      }});
      if ('{analysis_status}' === 'in_progress' || '{metadata_status}' === 'processing') {{
        pollStatus();
      }}
    }}

    if (metadataBtn) {{
      metadataBtn.addEventListener('click', async (event) => {{
        event.preventDefault();
        renderStatus({{
          metadata: 'processing',
          metadata_message: '已提交元数据识别任务，正在排队。',
          analysis: '{analysis_status}',
          analysis_progress: {analysis_progress},
          analysis_message: '{escape(analysis_message or "准备开始分析。")}'
        }});
        const resp = await fetch('/api/reading/{paper_id}/metadata', {{
          method: 'POST',
          credentials: 'same-origin'
        }});
        const data = await resp.json().catch(() => ({{ ok:false, error:'元数据识别失败' }}));
        if (!resp.ok || data.ok === false) {{
          alert(data.error || '元数据识别失败');
          renderStatus({{
            metadata: '{metadata_status}',
            metadata_message: '{escape(metadata_message or "等待元数据识别。")}',
            analysis: '{analysis_status}',
            analysis_progress: {analysis_progress},
            analysis_message: '{escape(analysis_message or "准备开始分析。")}'
          }});
          return;
        }}
        renderStatus(data.status || {{
          metadata: 'processing',
          metadata_message: '已提交元数据识别任务。',
          analysis: '{analysis_status}',
          analysis_progress: {analysis_progress},
          analysis_message: '{escape(analysis_message or "准备开始分析。")}'
        }});
        if (!pollingTimer) pollStatus();
      }});
    }}

    if (askBtn && qaQuestion) {{
      askBtn.addEventListener('click', async () => {{
        const question = qaQuestion.value.trim();
        if (!question) {{
          alert('请先输入问题。');
          return;
        }}
        askBtn.disabled = true;
        askBtn.textContent = '回答中...';
        if (qaStatus) qaStatus.textContent = '正在根据论文内容生成回答...';
        const resp = await fetch('/api/reading/{paper_id}/questions', {{
          method: 'POST',
          credentials: 'same-origin',
          headers: {{ 'Content-Type': 'application/json' }},
          body: JSON.stringify({{ question }})
        }});
        const data = await resp.json().catch(() => ({{ ok:false, error:'提问失败' }}));
        if (!resp.ok || data.ok === false) {{
          alert(data.error || '提问失败');
          askBtn.disabled = false;
          askBtn.textContent = '提交问题';
          if (qaStatus) qaStatus.textContent = '提问内容会保存到当前论文的阅读历史中。';
          return;
        }}
        appendQaItem(data.item || {{}});
        qaQuestion.value = '';
        askBtn.disabled = false;
        askBtn.textContent = '提交问题';
        if (qaStatus) qaStatus.textContent = '回答已保存到提问历史。';
      }});
    }}

    if (qaHistory) {{
      qaHistory.addEventListener('click', async (event) => {{
        const btn = event.target.closest('.delete-qa');
        if (!btn) return;
        const qaId = btn.dataset.qaId || '';
        if (!qaId) return;
        if (!confirm('确定删除这条提问记录吗？')) return;
        const resp = await fetch('/api/reading/{paper_id}/questions/' + encodeURIComponent(qaId), {{
          method: 'DELETE',
          credentials: 'same-origin'
        }});
        const data = await resp.json().catch(() => ({{ ok:false, error:'删除失败' }}));
        if (!resp.ok || data.ok === false) {{
          alert(data.error || '删除失败');
          return;
        }}
        const item = btn.closest('.qa-item');
        if (item) item.remove();
        if (!qaHistory.querySelector('.qa-item')) {{
          qaHistory.innerHTML = '<div class="meta" id="qa-empty">还没有提问记录，先问一个你关心的问题吧。</div>';
        }}
      }});
    }}

    noteButtons.forEach((button) => {{
      button.addEventListener('click', async () => {{
        const moduleName = button.dataset.module || '';
        const input = document.querySelector('.module-note-input[data-module="' + moduleName + '"]');
        const status = document.querySelector('.module-note-status[data-module="' + moduleName + '"]');
        const content = input ? input.value : '';
        button.disabled = true;
        button.textContent = '保存中...';
        if (status) status.textContent = '正在保存手工 Notes...';
        const resp = await fetch('/api/reading/{paper_id}/notes', {{
          method: 'POST',
          credentials: 'same-origin',
          headers: {{ 'Content-Type': 'application/json' }},
          body: JSON.stringify({{ module: moduleName, content }})
        }});
        const data = await resp.json().catch(() => ({{ ok:false, error:'保存失败' }}));
        if (!resp.ok || data.ok === false) {{
          alert(data.error || '保存失败');
          button.disabled = false;
          button.textContent = '保存 Notes';
          if (status) status.textContent = '手工 Notes 会保存在当前论文下。';
          return;
        }}
        button.disabled = false;
        button.textContent = '保存 Notes';
        if (status) status.textContent = 'Notes 已保存。';
      }});
    }});
  </script>
</body>
</html>"""


def build_library_html():
    items = list_citations()
    all_groups = list_reading_groups()
    all_tags = []
    seen_tags = set()
    group_counts = {int(g["id"]): 0 for g in all_groups if g.get("id") is not None}
    for item in items:
        for tag in [part.strip() for part in (item.get("tags") or "").split(",") if part.strip()]:
            low = tag.lower()
            if low in seen_tags:
                continue
            seen_tags.add(low)
            all_tags.append(tag)
    upload_group_options = "".join(f'<option value="{g["id"]}">{escape(g["name"])}</option>' for g in all_groups)
    cards = []
    for item in items:
        doi_text = f"DOI: {item['doi']}" if item.get("doi") else "无 DOI"
        url_html = f'<a class="action-link action-link-secondary" href="{item["url"]}" target="_blank" rel="noreferrer">原文链接</a>' if item.get("url") else ""
        expansion_paper = escape(
            json.dumps(
                {
                    "title": item.get("title") or "",
                    "doi": item.get("doi") or "",
                    "url": item.get("url") or "",
                    "authors": item.get("authors") or "",
                    "year": item.get("year") or "",
                    "venue": item.get("venue") or "",
                    "abstract": item.get("abstract") or "",
                    "matched_kw": item.get("matched_kw") or "",
                    "csv_index": item.get("source_csv_index"),
                },
                ensure_ascii=False,
            )
        )
        has_pdf = citation_has_pdf(item)
        reading_ready = bool(item.get("reading_paper_id")) and reading_json_ready(item.get("reading_paper_id"))
        upload_label = "更新 PDF" if has_pdf else "上传 PDF"
        if has_pdf:
            reading_label = "打开深度阅读" if reading_ready else "生成深度阅读"
            reading_button = f'<button class="deep-reading-link action-link action-link-primary" type="button" data-id="{item["id"]}" data-paper-id="{item.get("reading_paper_id") or ""}" data-ready="{str(reading_ready).lower()}">{reading_label}</button>'
            reading_hint = "已绑定 PDF，可直接进入深度阅读"
        else:
            reading_button = ""
            reading_hint = "上传 PDF 后可进入深度阅读"
        groups = get_citation_groups(item["id"])
        for group in groups:
            try:
                group_counts[int(group["id"])] = group_counts.get(int(group["id"]), 0) + 1
            except Exception:
                continue
        group_badges = "".join(f'<span class="group-badge" data-group-id="{g["id"]}">{escape(g["name"])}</span>' for g in groups) or '<span class="muted">未加入任何 Group</span>'
        group_ids = ",".join(str(g["id"]) for g in groups)
        group_options = "".join(f'<option value="{g["id"]}">{escape(g["name"])}</option>' for g in all_groups)
        tags = [part.strip() for part in (item.get("tags") or "").split(",") if part.strip()]
        tag_badges = "".join(
            f'<button class="tag" type="button" data-filter-tag="{escape(tag.lower())}">{escape(tag)}</button>'
            for tag in tags
        ) or '<span class="muted">无 tags</span>'
        cards.append(
            f"""
            <article class="card" data-tags="{escape(item.get("tags") or "")}" data-group-ids="{group_ids}" data-citation-id="{item["id"]}" data-expansion-paper="{expansion_paper}" data-source-search-slug="{escape(item.get("source_search_slug") or "")}">
              <div class="checkrow">
                <label class="checklabel">
                  <input class="cite-check" type="checkbox" value="{item["id"]}">
                  <span>选择导出</span>
                </label>
              </div>
              <div class="meta">#{item["id"]} · {item["created_at"]}</div>
              <h2>{item["title"]}</h2>
              <div class="meta">{item.get("authors") or "未知作者"} · {item.get("venue") or "未知 venue"} · {item.get("year") or "未知年份"}</div>
              <div class="meta">{doi_text} · 来自搜索：{item.get("source_search_slug") or "未知"}</div>
              <div class="group-row"><strong>Groups: </strong>{group_badges}</div>
              <div class="group-editor" style="display:flex; gap:8px; flex-wrap:wrap; margin:10px 0;">
                <select class="group-select" style="flex:1; padding:8px; border-radius:10px; border:1px solid #d5cbba;">
                  <option value="">选择 Group...</option>
                  {group_options}
                </select>
                <button class="add-to-group" type="button" data-id="{item["id"]}" style="padding:8px 14px; border-radius:10px;">加入</button>
                <button class="remove-from-group" type="button" data-id="{item["id"]}" style="padding:8px 14px; border-radius:10px; background:#6f6455;">移出</button>
              </div>
              <div class="tag-row">{tag_badges}</div>
              <div class="tag-editor">
                <input class="tag-input" type="text" value="{item.get("tags") or ''}" placeholder="输入 tags，逗号分隔">
                <button class="save-tag" type="button" data-id="{item["id"]}">保存 tags</button>
              </div>
              <p>{item.get("abstract") or "暂无摘要"}</p>
              <div class="links">
                {url_html}
                <button class="upload-pdf-link action-link action-link-upload" type="button" data-id="{item["id"]}">{upload_label}</button>
                <input class="upload-pdf-input" type="file" accept="application/pdf,.pdf" style="display:none;">
                {reading_button}
                <button class="expand-reading-link action-link action-link-secondary" type="button">延展搜索</button>
                <button class="remove-reading-link action-link action-link-danger" type="button" data-id="{item["id"]}">删除深度阅读</button>
              </div>
              <div class="links-meta">
                <span class="muted">{reading_hint}</span>
              </div>
              <div class="upload-progress" style="display:none; margin-top:10px;">
                <div class="meta upload-progress-label">准备上传...</div>
                <div style="width:100%; height:8px; border-radius:999px; background:#ead8ca; overflow:hidden; margin-top:6px;">
                  <div class="upload-progress-bar" style="height:100%; width:0%; background:linear-gradient(90deg, #c8733f, #9c4f2f);"></div>
                </div>
              </div>
            </article>
            """
        )
    group_filter_html = "".join(
        f'<button class="tag" type="button" data-group-filter="{g["id"]}">{escape(g["name"])} ({group_counts.get(int(g["id"]), 0)})</button>'
        for g in all_groups
        if group_counts.get(int(g["id"]), 0) > 0
    )
    body = "\n".join(cards) if cards else '<div class="empty">深度阅读模块还是空的，先去搜索结果页加入几篇，或直接上传 PDF 吧。</div>'
    return f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Deep Reading</title>
  <style>
    body {{ margin:0; font-family: Georgia, "Noto Serif SC", serif; background:#f2efe8; color:#1e1d1a; }}
    .wrap {{ max-width:1020px; margin:0 auto; padding:28px 18px 72px; }}
    .hero, .card, .empty {{
      border:1px solid #d5cbba; border-radius:24px; background:rgba(255,251,244,0.96);
      box-shadow:0 18px 40px rgba(76,50,28,0.08);
    }}
    .hero {{ padding:28px; margin-bottom:20px; }}
    .row {{ display:flex; justify-content:space-between; gap:12px; flex-wrap:wrap; }}
    .actions {{ display:flex; gap:10px; flex-wrap:wrap; margin-top:14px; }}
    h1 {{ margin:0 0 8px; font-size:40px; }}
    .muted, .meta {{ color:#6f685c; line-height:1.7; font-size:14px; }}
    .list {{ display:grid; gap:16px; }}
    .card {{ padding:18px; }}
    .checkrow {{ margin-bottom:6px; }}
    .checklabel {{ display:inline-flex; align-items:center; gap:8px; color:#6f685c; font-size:14px; }}
    .filters {{ display:flex; gap:10px; flex-wrap:wrap; margin-top:14px; }}
    .filter-toolbar {{
      display:flex;
      gap:10px;
      flex-wrap:wrap;
      align-items:center;
      margin-top:14px;
    }}
    .filter-search {{
      flex:1 1 280px;
      min-width:220px;
      padding:10px 14px;
      border-radius:999px;
      border:1px solid #d5cbba;
      font:inherit;
      color:#1e1d1a;
      background:#fffdf9;
    }}
    .filter-search-hint {{
      margin-top:8px;
      color:#6f685c;
      font-size:14px;
      line-height:1.6;
    }}
    .filters .tag, .tag-row .tag {{
      border:none; border-radius:999px; background:#ead8ca; color:#6f685c;
      padding:8px 12px; cursor:pointer; font:inherit;
    }}
    .filters .tag.active {{ background:#9c4f2f; color:white; }}
    .filters .tag.flash {{
      box-shadow: 0 0 0 4px rgba(156,79,47,0.18);
      transform: translateY(-1px);
      transition: box-shadow 0.2s ease, transform 0.2s ease;
    }}
    .tag-row {{ display:flex; gap:8px; flex-wrap:wrap; margin:10px 0; }}
    .tag-editor {{ display:flex; gap:10px; flex-wrap:wrap; margin:12px 0; }}
    .group-row {{ display:flex; gap:8px; flex-wrap:wrap; margin:10px 0; align-items:center; }}
    .group-badge {{ background:#c8e6c9; color:#2e7d32; padding:4px 10px; border-radius:999px; font-size:13px; }}
    .tag-editor input {{
      flex:1 1 280px; padding:10px 12px; border-radius:14px; border:1px solid #d5cbba; font:inherit;
    }}
    h2 {{ margin:8px 0; font-size:24px; line-height:1.3; }}
    p {{ line-height:1.8; }}
    .hero a, .hero button {{
      display:inline-block; background:#9c4f2f; color:white; text-decoration:none;
      padding:10px 14px; border-radius:999px; border:none; font:inherit; cursor:pointer;
    }}
    .links {{
      display:flex;
      gap:12px;
      flex-wrap:wrap;
      align-items:center;
      margin-top:14px;
    }}
    .links-meta {{
      margin-top:10px;
    }}
    .action-link {{
      display:inline-flex;
      align-items:center;
      justify-content:center;
      min-height:46px;
      padding:12px 18px;
      border-radius:16px;
      border:1px solid transparent;
      font:inherit;
      font-weight:700;
      letter-spacing:0.02em;
      text-decoration:none;
      cursor:pointer;
      transition:transform 0.15s ease, box-shadow 0.15s ease, background 0.15s ease;
    }}
    .action-link:hover {{
      transform:translateY(-1px);
      box-shadow:0 10px 20px rgba(76,50,28,0.12);
    }}
    .action-link-secondary {{
      background:#fffaf3;
      color:#7a4a2a;
      border-color:#d6b89b;
    }}
    .action-link-upload {{
      background:#fff;
      color:#8a4e22;
      border:2px dashed #c8733f;
      box-shadow:inset 0 0 0 1px rgba(200,115,63,0.08);
    }}
    .action-link-primary {{
      background:linear-gradient(135deg, #a6522d, #d06d3b);
      color:white;
      box-shadow:0 14px 28px rgba(156,79,47,0.22);
    }}
    .action-link-danger {{
      background:#fff4f1;
      color:#b33a2f;
      border-color:#e2a49a;
    }}
    .action-link-related {{
      background:#f2e6d8;
      color:#7a4a2a;
      border-color:#d6b89b;
    }}
    .action-link:disabled {{
      cursor:not-allowed;
      opacity:0.62;
      transform:none;
      box-shadow:none;
    }}
    .empty {{ padding:24px; text-align:center; }}
    @media (max-width: 720px) {{
      .wrap {{ padding:22px 14px 56px; }}
      .hero {{ padding:22px 18px; }}
      .actions {{ width:100%; }}
      .actions button, .hero a {{ width:100%; text-align:center; }}
      .filters .tag, .tag-row .tag {{ width:100%; text-align:center; }}
      .tag-editor input, .tag-editor button {{ width:100%; }}
      .links .action-link {{ width:100%; text-align:center; }}
      h1 {{ font-size:32px; }}
      h2 {{ font-size:20px; }}
    }}
  </style>
</head>
<body>
  <main class="wrap">
    <section class="hero">
      <div class="row">
        <div>
          <h1>深度阅读</h1>
          <div class="muted">这里保存文献、PDF 与深度阅读入口。你可以从搜索页加入时上传 PDF，也可以在这里上传 PDF 并由系统创建或匹配到数据库文献。当前共 {len(items)} 篇。</div>
          <div class="actions">
            <button id="select-all" type="button">全选 / 取消</button>
            <button id="export-json" type="button">导出所选 JSON</button>
            <button id="manage-groups" type="button">管理 Reading Groups</button>
          </div>
          <div id="active-openclaw-jobs" style="display:none; margin-top:14px; padding:14px; border:1px solid #d5cbba; border-radius:14px; background:#faf8f5;">
            <div style="display:flex; justify-content:space-between; gap:10px; flex-wrap:wrap; align-items:center;">
              <strong>当前正在解析</strong>
              <span class="meta" id="active-openclaw-summary">idle</span>
            </div>
            <div class="meta" id="active-openclaw-message" style="margin-top:6px;">当前没有正在运行的解析任务。</div>
            <div id="active-openclaw-list" style="margin-top:10px; display:grid; gap:10px;"></div>
          </div>
          <div id="openclaw-batch-upload" style="display:grid; grid-template-columns:repeat(auto-fit, minmax(220px, 1fr)); gap:8px; margin-top:14px; padding:14px; border:1px solid #d5cbba; border-radius:14px; background:#f8f4ee;">
            <div style="grid-column:1 / -1; color:#6f685c; font-size:14px;">把一个或多个 PDF 直接交给 OpenClaw 即可。系统会自动读取标题、作者、会议、年份和 DOI，尝试匹配已有文献；如果是重复 PDF 或重复论文，也会自动复用或合并。</div>
            <div style="grid-column:1 / -1; color:#6f685c; font-size:14px; line-height:1.7;">新手用法：1. 先选一个 Group（可选）。2. 选择一个或多个 PDF。3. 点下面按钮后等待后台处理。当前模型链路：`{escape(OPENCLAW_INGEST_MODEL)}` → `{escape(OPENCLAW_INGEST_CHECK_MODEL)}` → `{escape(OPENCLAW_INGEST_FALLBACK_MODEL)}`。</div>
            <select id="openclaw-group">
              <option value="">暂不加入 Group</option>
              {upload_group_options}
            </select>
            <input type="file" id="openclaw-pdfs" accept="application/pdf,.pdf" multiple>
            <button id="openclaw-upload-btn" type="button">开始处理 PDF</button>
            <div id="openclaw-upload-progress" style="display:none; grid-column:1 / -1;">
              <div class="meta" id="openclaw-upload-progress-label">准备上传...</div>
              <div style="width:100%; height:8px; border-radius:999px; background:#ead8ca; overflow:hidden; margin-top:6px;">
                <div id="openclaw-upload-progress-bar" style="height:100%; width:0%; background:linear-gradient(90deg, #b96b33, #7b3f1d);"></div>
              </div>
            </div>
            <div id="openclaw-job-status" style="display:none; grid-column:1 / -1; padding:12px; border-radius:12px; background:#fffaf5; border:1px solid #e3d5c3;">
              <div style="display:flex; justify-content:space-between; gap:10px; flex-wrap:wrap; align-items:center;">
                <strong>OpenClaw PDF 处理进度</strong>
                <span class="meta" id="openclaw-job-stage">idle</span>
              </div>
              <div class="meta" id="openclaw-job-message" style="margin-top:6px;">尚未启动任务。</div>
              <div class="meta" id="openclaw-job-progress" style="margin-top:6px;">已完成 0 / 0</div>
            </div>
          </div>
          <div id="group-management" style="display:none; margin-top:14px; padding:14px; border:1px solid #d5cbba; border-radius:14px; background:#faf8f5;">
            <div style="font-weight:600; margin-bottom:8px;">Reading Groups</div>
            <div id="group-list" style="margin-bottom:10px;"></div>
            <div style="display:flex; gap:8px; flex-wrap:wrap;">
              <input type="text" id="new-group-name" placeholder="新 Group 名称" style="flex:1; padding:8px 12px; border-radius:10px; border:1px solid #d5cbba;">
              <input type="text" id="new-group-desc" placeholder="描述（可选）" style="flex:2; padding:8px 12px; border-radius:10px; border:1px solid #d5cbba;">
              <button id="create-group" type="button" style="padding:8px 14px; border-radius:10px;">创建</button>
            </div>
          </div>
          <div class="filter-toolbar">
            <input id="library-tag-search" class="filter-search" type="search" placeholder="搜索 tag，默认只展示相关 tag">
          </div>
          <div class="filters" id="library-tag-filters">
            <button class="tag active" type="button" data-filter="all">全部</button>
          </div>
          <div class="filter-search-hint" id="library-tag-search-meta">输入上方搜索词筛选 tag，或点“全部”展开完整 tag 列表。</div>
          <div class="filters" style="margin-top:10px;">
            <button class="tag active" type="button" data-group-filter="all">全部 Group</button>
            {group_filter_html}
          </div>
          <div class="filters" id="library-expansion-filters" style="margin-top:10px;">
            <button class="tag active" type="button" data-expansion-filter="all">全部论文</button>
            <button class="tag" type="button" data-expansion-filter="expanded">已延展</button>
            <button class="tag" type="button" data-expansion-filter="unexpanded">未延展</button>
          </div>
        </div>
        <a href="/">返回时间线</a>
      </div>
    </section>
    <section class="list">{body}</section>
  </main>
  <script>
    const selectAllBtn = document.getElementById('select-all');
    const exportBtn = document.getElementById('export-json');
    const checks = () => Array.from(document.querySelectorAll('.cite-check'));
    const cards = () => Array.from(document.querySelectorAll('.card'));
    const tagFilterContainer = document.getElementById('library-tag-filters');
    const tagSearchInput = document.getElementById('library-tag-search');
    const tagSearchMeta = document.getElementById('library-tag-search-meta');
    const filterButtons = () => Array.from(document.querySelectorAll('#library-tag-filters [data-filter]'));
    const groupFilterButtons = () => Array.from(document.querySelectorAll('[data-group-filter]'));
    const expansionFilterButtons = () => Array.from(document.querySelectorAll('[data-expansion-filter]'));
    let activeGroupFilter = 'all';
    let activeTagFilter = 'all';
    let activeExpansionFilter = 'all';
    let tagSearchQuery = '';
    let showAllTags = false;
    let expansionIndex = {{}};

    function escapeHtml(value) {{
      return (value || '').toString()
        .replaceAll('&', '&amp;')
        .replaceAll('<', '&lt;')
        .replaceAll('>', '&gt;')
        .replaceAll('"', '&quot;');
    }}

    function text(value) {{
      return value === null || value === undefined ? '' : String(value);
    }}

    function normalizeDoi(value) {{
      return text(value)
        .trim()
        .toLowerCase()
        .replace(/^https?:\/\/(dx\.)?doi\.org\//, '')
        .replace(/^doi:\s*/, '');
    }}

    const readingExpansionStatus = (() => {{
      const el = document.createElement('div');
      el.style.position = 'fixed';
      el.style.right = '20px';
      el.style.bottom = '20px';
      el.style.maxWidth = '360px';
      el.style.padding = '10px 14px';
      el.style.borderRadius = '12px';
      el.style.background = 'rgba(61, 50, 39, 0.92)';
      el.style.color = '#fffaf2';
      el.style.fontSize = '14px';
      el.style.lineHeight = '1.5';
      el.style.boxShadow = '0 10px 30px rgba(43, 35, 25, 0.18)';
      el.style.zIndex = '9999';
      el.style.display = 'none';
      document.body.appendChild(el);
      return el;
    }})();

    function showReadingExpansionStatus(message) {{
      const textValue = text(message).trim();
      if (!textValue) return;
      readingExpansionStatus.textContent = textValue;
      readingExpansionStatus.style.display = 'block';
    }}

    function hideReadingExpansionStatus() {{
      readingExpansionStatus.style.display = 'none';
      readingExpansionStatus.textContent = '';
    }}

    async function apiPost(path, body) {{
      const resp = await fetch(path, {{
        method: 'POST',
        credentials: 'same-origin',
        headers: {{ 'Content-Type': 'application/json' }},
        body: JSON.stringify(body)
      }});
      const data = await resp.json().catch(() => ({{ ok:false, error:'请求失败' }}));
      if (!resp.ok || data.ok === false) {{
        throw new Error(data.error || '请求失败');
      }}
      return data;
    }}

    async function apiGet(path) {{
      const controller = new AbortController();
      const timer = setTimeout(() => controller.abort(), 15000);
      let resp;
      try {{
        resp = await fetch(path, {{
          credentials: 'same-origin',
          signal: controller.signal
        }});
      }} catch (error) {{
        if (error && error.name === 'AbortError') {{
          throw new Error('扩展搜索请求超时，请稍后重试');
        }}
        throw error;
      }} finally {{
        clearTimeout(timer);
      }}
      const data = await resp.json().catch(() => ({{ ok:false, error:'请求失败' }}));
      if (!resp.ok || data.ok === false) {{
        throw new Error(data.error || '请求失败');
      }}
      return data;
    }}

    function sleep(ms) {{
      return new Promise((resolve) => setTimeout(resolve, ms));
    }}

    function getExpansionPaper(card) {{
      try {{
        return JSON.parse(card.dataset.expansionPaper || '{{}}');
      }} catch (error) {{
        return {{}};
      }}
    }}

    function updateExpandButtonState(card) {{
      const button = card.querySelector('.expand-reading-link');
      if (!button) return;
      const paper = getExpansionPaper(card);
      const doi = normalizeDoi(paper.doi);
      const expanded = Boolean(doi && expansionIndex[doi] && expansionIndex[doi].site_url);
      card.dataset.expanded = expanded ? 'true' : 'false';
      if (!doi) {{
        card.dataset.expanded = 'false';
        button.disabled = true;
        button.textContent = '缺少 DOI';
        button.classList.remove('action-link-related');
        button.title = '当前阅读库仅能基于 DOI 发起延展搜索';
        return;
      }}
      button.disabled = false;
      button.textContent = expanded ? '查看相关论文' : '延展搜索';
      button.classList.toggle('action-link-related', expanded);
      button.title = expanded ? '已存在延展历史，点击可直接打开旧页面' : '基于该文献生成延展搜索结果';
    }}

    function updateAllExpandButtonStates() {{
      cards().forEach((card) => updateExpandButtonState(card));
    }}

    async function waitReferenceExpansionJob(jobId) {{
      const deadline = Date.now() + 20 * 60 * 1000;
      while (Date.now() < deadline) {{
        const data = await apiGet(`/api/papers/expand-references/jobs/${{encodeURIComponent(jobId)}}`);
        const job = data.job || {{}};
        const status = text(job.status).trim().toLowerCase();
        const message = text(job.step_message).trim();
        if (message) {{
          showReadingExpansionStatus(message);
        }}
        if (status === 'completed') return job;
        if (status === 'failed') {{
          throw new Error(job.error || job.step_message || '扩展搜索失败');
        }}
        await sleep(1200);
      }}
      throw new Error('扩展搜索等待超时，请稍后到时间线中查看结果');
    }}

    async function loadExpansions() {{
      try {{
        const resp = await fetch('/api/expansions', {{ credentials: 'same-origin' }});
        const data = await resp.json().catch(() => ({{ ok:false }}));
        if (!resp.ok || data.ok === false) {{
          expansionIndex = {{}};
          return;
        }}
        expansionIndex = data.items || {{}};
      }} catch (error) {{
        expansionIndex = {{}};
      }}
    }}

    if (selectAllBtn) {{
      selectAllBtn.addEventListener('click', () => {{
        const all = checks();
        const shouldSelect = all.some((box) => !box.checked);
        all.forEach((box) => {{
          box.checked = shouldSelect;
        }});
      }});
    }}

    if (exportBtn) {{
      exportBtn.addEventListener('click', async () => {{
        const ids = checks().filter((box) => box.checked).map((box) => Number(box.value));
        if (!ids.length) {{
          alert('请先选择至少一篇论文。');
          return;
        }}
        const resp = await fetch('/api/citations/export', {{
          method: 'POST',
          credentials: 'same-origin',
          headers: {{ 'Content-Type': 'application/json' }},
          body: JSON.stringify({{ ids }})
        }});
        if (!resp.ok) {{
          const data = await resp.json().catch(() => ({{ error: '导出失败' }}));
          alert(data.error || '导出失败');
          return;
        }}
        const blob = await resp.blob();
        const url = URL.createObjectURL(blob);
        const a = document.createElement('a');
        a.href = url;
        a.download = 'citations-export.json';
        document.body.appendChild(a);
        a.click();
        a.remove();
        URL.revokeObjectURL(url);
      }});
    }}

    function normalizeCardTags(card) {{
      return (card.dataset.tags || '').split(',').map((x) => x.trim()).filter(Boolean);
    }}

    function matchesExpansionFilter(card) {{
      const expanded = (card.dataset.expanded || 'false') === 'true';
      if (activeExpansionFilter === 'expanded') return expanded;
      if (activeExpansionFilter === 'unexpanded') return !expanded;
      return true;
    }}

    function tagsForActiveScope() {{
      const seen = new Set();
      const ordered = [];
      cards().forEach((card) => {{
        const groupIds = (card.dataset.groupIds || '').split(',').map((x) => x.trim()).filter(Boolean);
        const groupVisible = activeGroupFilter === 'all' || groupIds.includes(activeGroupFilter);
        if (!groupVisible || !matchesExpansionFilter(card)) return;
        normalizeCardTags(card).forEach((tag) => {{
          const low = tag.toLowerCase();
          if (seen.has(low)) return;
          seen.add(low);
          ordered.push(tag);
        }});
      }});
      return ordered;
    }}

    function applyLibraryFilters() {{
      cards().forEach((card) => {{
        const tags = normalizeCardTags(card).map((tag) => tag.toLowerCase());
        const groupIds = (card.dataset.groupIds || '').split(',').map((x) => x.trim()).filter(Boolean);
        const tagVisible = activeTagFilter === 'all' || tags.includes(activeTagFilter);
        const groupVisible = activeGroupFilter === 'all' || groupIds.includes(activeGroupFilter);
        const expansionVisible = matchesExpansionFilter(card);
        card.style.display = tagVisible && groupVisible && expansionVisible ? '' : 'none';
      }});
    }}

    function bindTagFilterButtons() {{
      filterButtons().forEach((btn) => {{
        btn.addEventListener('click', () => {{
          const nextFilter = btn.dataset.filter || 'all';
          const clickingActiveAll = nextFilter === 'all' && activeTagFilter === 'all';
          activeTagFilter = nextFilter;
          if (nextFilter === 'all' && !tagSearchQuery.trim()) {{
            showAllTags = clickingActiveAll ? !showAllTags : true;
          }} else if (nextFilter !== 'all') {{
            showAllTags = false;
          }}
          filterButtons().forEach((item) => item.classList.toggle('active', item === btn));
          renderLibraryTagFilters();
          applyLibraryFilters();
        }});
      }});
    }}

    function renderLibraryTagFilters() {{
      if (!tagFilterContainer) return;
      const availableTags = tagsForActiveScope();
      const availableTagSet = new Set(availableTags.map((tag) => tag.toLowerCase()));
      if (activeTagFilter !== 'all' && !availableTagSet.has(activeTagFilter)) {{
        activeTagFilter = 'all';
      }}
      const normalizedQuery = tagSearchQuery.trim().toLowerCase();
      let visibleTags = [];
      if (normalizedQuery) {{
        visibleTags = availableTags.filter((tag) => tag.toLowerCase().includes(normalizedQuery));
      }} else if (activeTagFilter !== 'all') {{
        visibleTags = availableTags.filter((tag) => tag.toLowerCase() === activeTagFilter);
      }} else if (showAllTags) {{
        visibleTags = availableTags;
      }}
      const buttons = [];
      buttons.push('<button class="tag' + (activeTagFilter === 'all' ? ' active' : '') + '" type="button" data-filter="all">全部</button>');
      visibleTags.forEach((tag) => {{
        const low = tag.toLowerCase();
        buttons.push('<button class="tag' + (activeTagFilter === low ? ' active' : '') + '" type="button" data-filter="' + escapeHtml(low) + '">' + escapeHtml(tag) + '</button>');
      }});
      tagFilterContainer.innerHTML = buttons.join('');
      if (tagSearchMeta) {{
        if (!availableTags.length) {{
          tagSearchMeta.textContent = '当前 Group 下还没有可用 tag。';
        }} else if (normalizedQuery) {{
          tagSearchMeta.textContent = visibleTags.length
            ? `已找到 ${{visibleTags.length}} 个与“${{tagSearchQuery.trim()}}”相关的 tag。`
            : `没有找到与“${{tagSearchQuery.trim()}}”相关的 tag。`;
        }} else if (activeTagFilter !== 'all') {{
          const activeTag = availableTags.find((tag) => tag.toLowerCase() === activeTagFilter) || '';
          tagSearchMeta.textContent = activeTag
            ? `当前按 tag「${{activeTag}}」筛选。`
            : '当前正在按 tag 筛选。';
        }} else if (showAllTags) {{
          tagSearchMeta.textContent = `当前已展开全部 ${{availableTags.length}} 个 tag。`;
        }} else {{
          tagSearchMeta.textContent = '输入上方搜索词筛选 tag，或点“全部”展开完整 tag 列表。';
        }}
      }}
      bindTagFilterButtons();
    }}

    function emphasizeActiveTopTag() {{
      const activeBtn = filterButtons().find((item) => item.classList.contains('active'));
      if (!activeBtn || activeTagFilter === 'all') return;
      activeBtn.classList.remove('flash');
      activeBtn.scrollIntoView({{ behavior: 'smooth', block: 'nearest', inline: 'center' }});
      window.setTimeout(() => activeBtn.classList.add('flash'), 20);
      window.setTimeout(() => activeBtn.classList.remove('flash'), 1400);
    }}

    groupFilterButtons().forEach((btn) => {{
      btn.addEventListener('click', () => {{
        activeGroupFilter = btn.dataset.groupFilter || 'all';
        groupFilterButtons().forEach((item) => item.classList.toggle('active', item === btn));
        showAllTags = false;
        renderLibraryTagFilters();
        applyLibraryFilters();
      }});
    }});

    expansionFilterButtons().forEach((btn) => {{
      btn.addEventListener('click', () => {{
        activeExpansionFilter = btn.dataset.expansionFilter || 'all';
        expansionFilterButtons().forEach((item) => item.classList.toggle('active', item === btn));
        showAllTags = false;
        renderLibraryTagFilters();
        applyLibraryFilters();
      }});
    }});

    if (tagSearchInput) {{
      tagSearchInput.addEventListener('input', () => {{
        tagSearchQuery = tagSearchInput.value || '';
        showAllTags = false;
        renderLibraryTagFilters();
      }});
    }}

    document.querySelectorAll('[data-filter-tag]').forEach((btn) => {{
      btn.addEventListener('click', () => {{
        const target = (btn.dataset.filterTag || '').toLowerCase();
        if (!target) return;
        activeTagFilter = target;
        tagSearchQuery = btn.textContent || '';
        if (tagSearchInput) {{
          tagSearchInput.value = tagSearchQuery;
        }}
        showAllTags = false;
        renderLibraryTagFilters();
        applyLibraryFilters();
        emphasizeActiveTopTag();
      }});
    }});

    renderLibraryTagFilters();
    applyLibraryFilters();
    updateAllExpandButtonStates();

    document.querySelectorAll('.save-tag').forEach((btn) => {{
      btn.addEventListener('click', async () => {{
        const card = btn.closest('.card');
        const input = card.querySelector('.tag-input');
        const id = Number(btn.dataset.id);
        const tags = input.value;
        const resp = await fetch('/api/citations/tags', {{
          method: 'POST',
          credentials: 'same-origin',
          headers: {{ 'Content-Type': 'application/json' }},
          body: JSON.stringify({{ id, tags }})
        }});
        const data = await resp.json().catch(() => ({{ ok:false, error:'保存失败' }}));
        if (!resp.ok || data.ok === false) {{
          alert(data.error || '保存失败');
          return;
        }}
        window.location.reload();
      }});
    }});

    // Group management
    const manageGroupsBtn = document.getElementById('manage-groups');
    const groupManagementDiv = document.getElementById('group-management');
    const groupListDiv = document.getElementById('group-list');
    const createGroupBtn = document.getElementById('create-group');
    const newGroupNameInput = document.getElementById('new-group-name');
    const newGroupDescInput = document.getElementById('new-group-desc');
    const openclawUploadBtn = document.getElementById('openclaw-upload-btn');
    const openclawUploadProgress = document.getElementById('openclaw-upload-progress');
    const openclawUploadProgressBar = document.getElementById('openclaw-upload-progress-bar');
    const openclawUploadProgressLabel = document.getElementById('openclaw-upload-progress-label');
    const openclawJobStatus = document.getElementById('openclaw-job-status');
    const openclawJobStage = document.getElementById('openclaw-job-stage');
    const openclawJobMessage = document.getElementById('openclaw-job-message');
    const openclawJobProgress = document.getElementById('openclaw-job-progress');
    const activeOpenClawJobs = document.getElementById('active-openclaw-jobs');
    const activeOpenClawSummary = document.getElementById('active-openclaw-summary');
    const activeOpenClawMessage = document.getElementById('active-openclaw-message');
    const activeOpenClawList = document.getElementById('active-openclaw-list');
    let openclawPollingTimer = null;
    let openclawCurrentJobId = '';

    function setProgress(container, bar, label, percent, text) {{
      if (container) container.style.display = 'block';
      if (bar) bar.style.width = Math.max(0, Math.min(100, Number(percent || 0))) + '%';
      if (label) label.textContent = text || '处理中...';
    }}

    function uploadWithProgress(url, formData, onProgress) {{
      return new Promise((resolve, reject) => {{
        const xhr = new XMLHttpRequest();
        xhr.open('POST', url, true);
        xhr.withCredentials = true;
        xhr.upload.addEventListener('progress', (event) => {{
          if (!event.lengthComputable) return;
          const percent = Math.round((event.loaded / event.total) * 100);
          onProgress(percent, percent < 100 ? `正在上传 PDF... ${{percent}}%` : '上传完成，等待服务器处理...');
        }});
        xhr.onload = () => {{
          let data = null;
          try {{
            data = JSON.parse(xhr.responseText || '{{}}');
          }} catch (error) {{
            data = {{ ok: false, error: '响应解析失败' }};
          }}
          if (xhr.status >= 200 && xhr.status < 300 && data.ok !== false) {{
            resolve(data);
            return;
          }}
          reject(new Error((data && data.error) || '上传失败'));
        }};
        xhr.onerror = () => reject(new Error('网络错误，上传失败'));
        xhr.send(formData);
      }});
    }}

    function jobProgressPercent(job) {{
      const total = Math.max(0, Number(job.total || 0));
      const completed = Math.max(0, Number(job.completed || 0));
      const failed = Math.max(0, Number(job.failed || 0));
      if (!total) return job.running ? 8 : 100;
      return Math.max(8, Math.min(100, Math.round(((completed + failed) / total) * 100)));
    }}

    function renderActiveOpenClawJobs(jobs) {{
      if (!activeOpenClawJobs) return;
      const activeJobs = (jobs || []).filter((job) => job && job.running);
      if (!activeJobs.length) {{
        activeOpenClawJobs.style.display = 'block';
        if (activeOpenClawSummary) activeOpenClawSummary.textContent = 'idle';
        if (activeOpenClawMessage) activeOpenClawMessage.textContent = '当前没有正在运行的解析任务。';
        if (activeOpenClawList) activeOpenClawList.innerHTML = '';
        return;
      }}
      activeOpenClawJobs.style.display = 'block';
      if (activeOpenClawSummary) activeOpenClawSummary.textContent = `${{activeJobs.length}} 个任务运行中`;
      if (activeOpenClawMessage) activeOpenClawMessage.textContent = '新上传的 PDF 会自动出现在这里。';
      if (activeOpenClawList) {{
        activeOpenClawList.innerHTML = activeJobs.map((job) => {{
          const total = Number(job.total || 0);
          const completed = Number(job.completed || 0);
          const failed = Number(job.failed || 0);
          const current = job.current_title ? `当前：${{job.current_title}}` : '等待后台开始处理';
          const percent = jobProgressPercent(job);
          return `
            <div style="padding:12px; border:1px solid #e3d5c3; border-radius:12px; background:#fffaf5;">
              <div style="display:flex; justify-content:space-between; gap:10px; flex-wrap:wrap; align-items:center;">
                <strong>${{job.kind === 'openclaw_refresh_paper' ? '单篇深度解析' : 'PDF 批量导入'}}</strong>
                <span class="meta">${{job.status || 'running'}}</span>
              </div>
              <div class="meta" style="margin-top:6px;">${{job.message || ''}}</div>
              <div class="meta" style="margin-top:4px;">${{current}}</div>
              <div style="margin-top:8px; height:8px; border-radius:999px; background:#ead8ca; overflow:hidden;">
                <div style="height:100%; width:${{percent}}%; background:linear-gradient(90deg, #b96b33, #7b3f1d);"></div>
              </div>
              <div class="meta" style="margin-top:6px;">已完成 ${{completed}} / ${{total}} · 失败 ${{failed}}</div>
            </div>
          `;
        }}).join('');
      }}
    }}

    async function fetchOpenClawJobs() {{
      const resp = await fetch('/api/openclaw-intake/jobs', {{ credentials: 'same-origin' }});
      const data = await resp.json().catch(() => ({{ ok:false, error:'任务列表获取失败' }}));
      if (!resp.ok || data.ok === false) throw new Error(data.error || '任务列表获取失败');
      return data.jobs || [];
    }}

    function renderOpenClawJob(job) {{
      if (!openclawJobStatus || !job) return;
      openclawJobStatus.style.display = 'block';
      if (openclawJobStage) openclawJobStage.textContent = job.status || 'idle';
      if (openclawJobMessage) openclawJobMessage.textContent = job.message || '任务进行中。';
      if (openclawJobProgress) {{
        const completed = Number(job.completed || 0);
        const total = Number(job.total || 0);
        const failed = Number(job.failed || 0);
        const current = job.current_title ? ` · 当前：${{job.current_title}}` : '';
        openclawJobProgress.textContent = `已完成 ${{completed}} / ${{total}} · 失败 ${{failed}}${{current}}`;
      }}
      if (openclawUploadBtn) {{
        openclawUploadBtn.disabled = !!job.running;
        openclawUploadBtn.textContent = job.running ? 'OpenClaw 正在处理中...' : '开始处理 PDF';
      }}
    }}

    async function fetchOpenClawJob(jobId) {{
      const resp = await fetch('/api/openclaw-intake/jobs/' + encodeURIComponent(jobId), {{ credentials: 'same-origin' }});
      const data = await resp.json().catch(() => ({{ ok:false, error:'任务状态获取失败' }}));
      if (!resp.ok || data.ok === false) throw new Error(data.error || '任务状态获取失败');
      return data.job || {{}};
    }}

    async function pollOpenClawJob() {{
      try {{
        const jobs = await fetchOpenClawJobs();
        renderActiveOpenClawJobs(jobs);
        const trackedJob = openclawCurrentJobId ? (jobs.find((job) => job.id === openclawCurrentJobId) || null) : null;
        if (trackedJob) {{
          renderOpenClawJob(trackedJob);
        }}
        if ((trackedJob && trackedJob.running) || jobs.some((job) => job && job.running)) {{
          openclawPollingTimer = window.setTimeout(pollOpenClawJob, 2500);
          return;
        }}
        openclawPollingTimer = null;
      }} catch (error) {{
        openclawPollingTimer = window.setTimeout(pollOpenClawJob, 4000);
      }}
    }}

    async function loadGroups() {{
      const resp = await fetch('/api/reading-groups', {{ credentials: 'same-origin' }});
      if (!resp.ok) return;
      const data = await resp.json();
      if (!data.ok) return;
      let html = '';
      data.groups.forEach(g => {{
        html += `<div style="display:flex; justify-content:space-between; align-items:center; padding:6px 0; border-bottom:1px solid #e0e0e0;">
          <span><strong>${{text(g.name)}}</strong>${{g.description ? ' - ' + g.description : ''}}</span>
          <button class="delete-group" data-id="${{g.id}}" style="padding:4px 10px; border-radius:8px; font-size:12px; background:#c62828;">删除</button>
        </div>`;
      }});
      groupListDiv.innerHTML = html || '<span class="muted">暂无 Groups</span>';
      document.querySelectorAll('.delete-group').forEach(btn => {{
        btn.addEventListener('click', async () => {{
          const id = btn.dataset.id;
          if (!confirm('确定删除此 Group？其中的文章不会被删除。')) return;
          const resp = await fetch('/api/reading-groups/' + id, {{ method: 'DELETE', credentials: 'same-origin' }});
          if (resp.ok) loadGroups();
        }});
      }});
    }}

    if (manageGroupsBtn) {{
      manageGroupsBtn.addEventListener('click', () => {{
        const visible = groupManagementDiv.style.display !== 'none';
        groupManagementDiv.style.display = visible ? 'none' : 'block';
        if (!visible) loadGroups();
      }});
    }}

    if (createGroupBtn) {{
      createGroupBtn.addEventListener('click', async () => {{
        const name = newGroupNameInput.value.trim();
        const description = newGroupDescInput.value.trim();
        if (!name) {{ alert('请输入 Group 名称'); return; }}
        const resp = await fetch('/api/reading-groups', {{
          method: 'POST',
          credentials: 'same-origin',
          headers: {{ 'Content-Type': 'application/json' }},
          body: JSON.stringify({{ name, description }})
        }});
        if (resp.ok) {{
          newGroupNameInput.value = '';
          newGroupDescInput.value = '';
          loadGroups();
          window.location.reload();
        }}
      }});
    }}

    // Add/remove citation from group
    document.querySelectorAll('.add-to-group').forEach(btn => {{
      btn.addEventListener('click', async () => {{
        const card = btn.closest('.card');
        const select = card.querySelector('.group-select');
        const groupId = select.value;
        if (!groupId) {{ alert('请选择 Group'); return; }}
        const citationId = btn.dataset.id;
        const resp = await fetch('/api/citations/' + citationId + '/groups/' + groupId, {{
          method: 'POST', credentials: 'same-origin'
        }});
        if (resp.ok) window.location.reload();
      }});
    }});

    document.querySelectorAll('.remove-from-group').forEach(btn => {{
      btn.addEventListener('click', async () => {{
        const card = btn.closest('.card');
        const select = card.querySelector('.group-select');
        const groupId = select.value;
        if (!groupId) {{ alert('请选择 Group'); return; }}
        const citationId = btn.dataset.id;
        const resp = await fetch('/api/citations/' + citationId + '/groups/' + groupId, {{
          method: 'DELETE', credentials: 'same-origin'
        }});
        if (resp.ok) window.location.reload();
      }});
    }});

    document.querySelectorAll('.upload-pdf-link').forEach((btn) => {{
      btn.addEventListener('click', () => {{
        const card = btn.closest('.card');
        const input = card.querySelector('.upload-pdf-input');
        if (input) input.click();
      }});
    }});

    document.querySelectorAll('.upload-pdf-input').forEach((input) => {{
      input.addEventListener('change', async () => {{
        const file = input.files && input.files[0];
        if (!file) return;
        const card = input.closest('.card');
        const citationId = card.dataset.citationId;
        const progressBox = card.querySelector('.upload-progress');
        const progressBar = card.querySelector('.upload-progress-bar');
        const progressLabel = card.querySelector('.upload-progress-label');
        const formData = new FormData();
        formData.append('pdf', file);
        try {{
          const data = await uploadWithProgress('/api/citations/' + citationId + '/pdf', formData, (percent, text) => {{
            setProgress(progressBox, progressBar, progressLabel, percent, text);
          }});
          setProgress(progressBox, progressBar, progressLabel, 100, 'PDF 已上传并绑定文献。');
          if (data.reading_url) {{
            window.location.href = data.reading_url;
            return;
          }}
          window.location.reload();
        }} catch (error) {{
          alert(error.message || '上传失败');
          input.value = '';
          return;
        }}
      }});
    }});

    document.querySelectorAll('.deep-reading-link').forEach((btn) => {{
      btn.addEventListener('click', async () => {{
        const ready = (btn.dataset.ready || '') === 'true';
        const paperId = btn.dataset.paperId || '';
        if (ready && paperId) {{
          window.location.href = '/reading/' + encodeURIComponent(paperId);
          return;
        }}
        const citationId = btn.dataset.id;
        const resp = await fetch('/api/citations/' + citationId + '/reading', {{
          method: 'POST',
          credentials: 'same-origin'
        }});
        const data = await resp.json().catch(() => ({{ ok:false, error:'生成失败' }}));
        if (!resp.ok || data.ok === false) {{
          alert(data.error || '生成失败');
          return;
        }}
        window.location.href = data.reading_url;
      }});
    }});

    document.querySelectorAll('.expand-reading-link').forEach((btn) => {{
      btn.addEventListener('click', async () => {{
        const card = btn.closest('.card');
        if (!card) return;
        const paper = getExpansionPaper(card);
        const doi = normalizeDoi(paper.doi);
        if (!doi) {{
          alert('该文献当前缺少 DOI，暂时无法从深度阅读页发起延展搜索。');
          return;
        }}
        if (expansionIndex[doi] && expansionIndex[doi].site_url) {{
          showReadingExpansionStatus('已找到现有相关论文页，正在打开...');
          window.setTimeout(hideReadingExpansionStatus, 1200);
          window.open(expansionIndex[doi].site_url, '_blank', 'noopener');
          return;
        }}
        btn.disabled = true;
        showReadingExpansionStatus('已进入扩展搜索队列，正在准备结果...');
        try {{
          const started = await apiPost('/api/papers/expand-references', {{
            search_slug: card.dataset.sourceSearchSlug || '',
            paper
          }});
          const job = started.job || {{}};
          if (!job.id) {{
            throw new Error('扩展搜索任务创建失败');
          }}
          const finished = job.status === 'completed' ? job : await waitReferenceExpansionJob(job.id);
          if (finished.site_url) {{
            expansionIndex[doi] = {{ site_url: finished.site_url }};
          }}
          updateExpandButtonState(card);
          if (!finished.site_url) {{
            throw new Error('扩展搜索结果未生成链接');
          }}
          showReadingExpansionStatus('延展搜索页面已生成，正在打开...');
          window.open(finished.site_url, '_blank', 'noopener');
          window.setTimeout(hideReadingExpansionStatus, 1200);
        }} catch (error) {{
          hideReadingExpansionStatus();
          alert(error.message || '扩展搜索失败');
        }} finally {{
          updateExpandButtonState(card);
        }}
      }});
    }});

    document.querySelectorAll('.remove-reading-link').forEach((btn) => {{
      btn.addEventListener('click', async () => {{
        const citationId = btn.dataset.id;
        if (!confirm('确定删除这篇深度阅读文献吗？相关的阅读页、分析、提问、Notes、分组关联与独占 PDF 都会一起删除。')) return;
        const resp = await fetch('/api/citations/' + citationId + '/reading', {{
          method: 'DELETE',
          credentials: 'same-origin'
        }});
        const data = await resp.json().catch(() => ({{ ok:false, error:'移除失败' }}));
        if (!resp.ok || data.ok === false) {{
          alert(data.error || '移除失败');
          return;
        }}
        window.location.reload();
      }});
    }});

    if (openclawUploadBtn) {{
      openclawUploadBtn.addEventListener('click', async () => {{
        const files = Array.from((document.getElementById('openclaw-pdfs').files || []));
        if (!files.length) {{
          alert('请至少选择一个 PDF。');
          return;
        }}
        const formData = new FormData();
        const groupId = document.getElementById('openclaw-group').value;
        if (groupId) formData.append('group_id', groupId);
        files.forEach((file, index) => formData.append('pdf_' + index, file));
        try {{
          const data = await uploadWithProgress('/api/openclaw-intake/upload', formData, (percent, text) => {{
            setProgress(openclawUploadProgress, openclawUploadProgressBar, openclawUploadProgressLabel, percent, text);
          }});
          setProgress(openclawUploadProgress, openclawUploadProgressBar, openclawUploadProgressLabel, 100, 'PDF 已提交到 OpenClaw，正在后台处理与合并。');
          openclawCurrentJobId = data.job_id || '';
          renderOpenClawJob(data.job || {{}});
          alert('PDF 已成功提交到 OpenClaw。你可以留在当前页面查看进度，也可以稍后回来，后台会继续处理。');
          if (openclawPollingTimer) window.clearTimeout(openclawPollingTimer);
          pollOpenClawJob().catch(() => {{}});
        }} catch (error) {{
          alert(error.message || 'OpenClaw PDF 导入失败');
        }}
      }});
    }}

    loadExpansions().finally(() => {{
      updateAllExpandButtonStates();
      renderLibraryTagFilters();
      applyLibraryFilters();
    }});
    pollOpenClawJob().catch(() => {{}});
  </script>
</body>
</html>"""


# HTTP request handling
