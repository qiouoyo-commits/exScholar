---
name: ccf-research
description: >
  ALWAYS activate this skill when the user says "找论文"、"查论文"、"搜论文"、"ccf搜"、"顶会论文" or any variant.
  Also activate when user describes a research topic/direction and wants related academic papers from top conferences.
  Magic word that guarantees activation: "读文献"（用户说"读文献"时必须触发此 skill，无例外）.
metadata:
  openclaw:
    os: ["linux"]
    requires:
      bins: ["bash", "python3", "oc-conda-run"]
---

# exScholar Research Skill

当用户描述一个研究兴趣时：先通过简短问答确认搜索方案，再执行搜索（默认含摘要），最后生成 CSV、JSON 和静态网页，并把网页网址发给用户。

---

## 数据存储结构

每次搜索结果保存在独立目录下，不互相覆盖：

```
/home/ubuntu/tools/exScholar/data/searches/
/home/ubuntu/tools/exScholar/data/expansions/
└── YYYY-MM-DD_<slug>/          ← 日期 + 话题简称，每次搜索独立一个目录
    ├── search.json             ← 搜索参数记录（关键词/venues/日期/是否含摘要）
    ├── papers.csv              ← 论文列表（matched_kw/title/venue/year/authors/doi/url/abstract）
    ├── papers.json             ← 面向展示站点的 JSON（csv_index/title/content/...）
    └── site/index.html         ← 可直接打开的静态网页
```

`<slug>` 是话题的英文简称，由 Phase 1 确认时确定，仅含英文小写字母、数字、连字符。
示例：`physio-ui`、`posture-hci`、`llm-agent`

---

## Phase 1 — 询问确认（必须先完成，再搜索）

**不要直接开始搜索。** 先展示草稿方案，等用户确认后再执行。

### 展示草稿方案

按以下格式一次性展示，语气自然简洁：

```
好的，我理解你的方向是：[用一句话概括话题]

搜索方案：

关键词组（每组独立搜索后合并去重）：
  ① [英文关键词组1]   ← [理由]
  ② [英文关键词组2]   ← [理由]

搜索范围：[venue1], [venue2], [venue3]
年份：[不限 / 2022 年至今]
话题简称（用于目录命名）：[slug]

默认会爬取摘要，预计约 X 分钟。有没有要调整的？
```

估算时间公式：`命中篇数 × 3 秒 ÷ 60`，向上取整到分钟。
命中篇数 = 关键词组数 × venue 数 × top（保守估计，实际因去重会少）。

### 询问要点

- 关键词是否准确？有无遗漏的同义词或具体术语（如传感器类型、技术名称）？
- Venues 是否覆盖到位？话题是否跨领域？
- 是否需要限制年份？
- 如果时间较长且用户只想快速浏览标题，提示可以加 `--no-abstract` 跳过摘要

### 处理用户反馈

- 用户确认 → 进入 Phase 2
- 用户修改关键词/venues → 更新方案，简短确认后进入 Phase 2
- 用户说"先不要摘要" → 在命令中加 `--no-abstract`

---

## Phase 2 — 执行搜索

**每步完成后立即向用户反馈，不要等全部搜索结束再说话。**

### 运行环境要求

- 必须在 `openclaw-analytics` conda 环境中执行，优先使用 `oc-conda-run`
- 仓库固定入口为 `/home/ubuntu/tools/exScholar/run_search.sh`，关键词搜索优先走这个脚本
- 静态站点固定服务端口为 `38128`，公开基址由 `.env.local` 中的 `PUBLIC_SITE_BASE_URL` 控制
- 不要直接使用系统默认 `python` 或 base 环境的 Python 3.13；该环境下摘要依赖 `aiohttp` 可能不可用
- 首次使用前确保已安装 Playwright 浏览器：

```bash
oc-conda-run -- python -m playwright install chromium
```

### 命令格式

```bash
/home/ubuntu/tools/exScholar/run_search.sh \
  --keywords "physiological notification;biosignal alert;EEG stress" \
  --venues "chi,uist,cscw,ubicomp" \
  --slug "physio-ui" \
  --top 100 \
  --year-from 2020
```

如需排查问题或直接执行 Python 命令，再退回到：

```bash
oc-conda-run -- python -m app.pipeline.search ...
```

参数说明：
- `--keywords`：分号分隔多组，每组独立搜索 DBLP 标题，结果合并去重
- `--venues`：逗号分隔会议缩写；不传则全 DBLP 范围
- `--slug`：话题简称，只含小写字母/数字/连字符，用于目录命名
- `--top`：每组 × 每个 venue 各取最多 N 篇（默认 100）
- `--year-from`：最早年份（可选）
- `--no-abstract`：跳过摘要爬取（默认**不加**，即默认爬取摘要）

> 默认爬取摘要：无代理，并发数 2，请求间随机延迟 2-4 秒。
> 预计耗时 = 命中篇数 × 3 秒 ÷ 60（分钟）。

如果用户要求跑主爬虫而不是关键词搜索，也同样使用 `oc-conda-run`：

```bash
oc-conda-run -- python -m app.pipeline.main -ccf a -c conf -m 20 -p 10
```

### 分步反馈规则

每一个阶段结束后，**立即**向用户发一条简短消息，不要等全流程跑完再说话：

| 时机 | 反馈内容 |
|------|---------|
| 搜索命令启动前 | "开始搜索，共 N 组关键词 × M 个 venues，请稍等..." |
| 搜索命令执行完 | 每组关键词命中多少篇，去重后总共多少篇 |
| 摘要爬取启动前 | "开始爬取摘要，共 X 篇，预计约 Y 分钟..." |
| 摘要爬取完成后 | "摘要获取完成：X 篇成功，Y 篇失败，开始生成 JSON 和静态网页..." |
| 静态网页生成后 | 告知 `site_url`、CSV 路径和 JSON 路径 |

脚本输出（stdout）已包含实时进度，直接转述关键数字即可，不需要重复全部日志。

---

## Phase 3 — 生成 JSON 与静态网页

搜索完成后，不再生成报告。改为确认以下产物存在：

- `papers.csv`
- `papers.json`
- `site/index.html`

其中 `papers.json` 至少应包含：

- `csv_index`
- `title`
- `content`

最终给用户发：

1. 网站网址：`site_url`
2. CSV 路径
3. JSON 路径

默认网址形态：

`http://<PUBLIC_SITE_HOST>:38128/searches/YYYY-MM-DD_<slug>/site/`

不再追加论文分析、推荐精读或研究空白总结，除非用户明确要求。

---

## Venue 参考表

```
HCI / 普适计算:    chi, uist, cscw, ubicomp
AI / NLP:          aaai, nips, acl, cvpr, iccv, icml, ijcai, iclr, emnlp, naacl, coling, eccv
系统 / 架构:       asplos, osdi, sosp, eurosys, usenix_atc, fast, isca, micro, hpca
安全:              ccs, sp, uss, ndss, crypto, eurocrypt
数据库 / 挖掘:     sigmod, kdd, icde, sigir, vldb
网络:              sigcomm, mobicom, infocom, nsdi
软件工程:          icse, fse_esec, ase, issta
图形 / 多媒体:     siggraph, mm, vis
理论:              stoc, focs, soda
```

---

## 注意事项

- DBLP 搜索只匹配**标题**，不匹配摘要。关键词选名词短语，避免动词。
- 某关键词组命中 0 篇时，提示用户换同义词或放宽措辞。
- `papers.csv` 的 `matched_kw` 列记录每篇由哪组关键词命中，网页中也可据此展示。
- 无摘要的论文（`abstract` 为空）在网页中标注"暂无内容"，不影响标题级别的浏览。
- CSV、JSON 和静态网页会一并保存，下次可直接打开网页查看，无需重新爬取。
