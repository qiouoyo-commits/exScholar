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
      bins: ["bash"]
---

# exScholar Research Skill

当用户描述一个研究兴趣时：先通过智能体生成更贴合学术表达的检索建议，再通过简短问答确认搜索方案，执行搜索（默认含摘要），最后让智能体结合标题和摘要复核相关性、自动打标签，并生成 CSV、JSON 和静态网页，把网页网址发给用户。

---

## 0. 触发边界

- 这条 skill 负责普通的“找论文 / 查论文 / 搜论文 / 读文献”
- 不负责基于图片或截图识别论文
- 如果用户明确要根据图片找论文，应改走 [picsearch/SKILL.md](/home/ubuntu/tools/exScholar/skills/picsearch/SKILL.md)
- 如果用户明确要根据一串论文标题批量补链接，应改走 [textsearch/SKILL.md](/home/ubuntu/tools/exScholar/skills/textsearch/SKILL.md)
- 如果用户明确要根据 Google Scholar 页面截图批量补链接，应改走 [picsearch/SKILL.md](/home/ubuntu/tools/exScholar/skills/picsearch/SKILL.md)
- `picsearch` 当前既支持单篇论文截图，也支持 Google Scholar 页面截图批量补链接；补链接后还会尽量继续抓取摘要
- `textsearch` 当前只处理纯文本标题输入；旧的 `titlesearch` 名称已经废弃，当前统一使用 `textsearch`

---

## 数据存储结构

当前项目是多用户模式。网页登录触发的搜索会写入当前登录用户自己的目录；非网页登录触发的默认 OpenClaw 搜索会写入 `data/users/qioyo/`。

每次搜索结果保存在独立目录下，不互相覆盖。典型目录形态如下：

```
/home/ubuntu/tools/exScholar/data/users/<username>/searches/
/home/ubuntu/tools/exScholar/data/users/<username>/expansions/
└── YYYY-MM-DD_<slug>/          ← 日期 + 话题简称，每次搜索独立一个目录
    ├── search.json             ← 搜索参数记录（关键词/venues/日期/是否含摘要）
    ├── papers.csv              ← 论文列表（matched_kw/title/venue/year/authors/doi/url/abstract/...）
    ├── papers.json             ← 面向展示站点的 JSON（csv_index/title/content/...）
    └── site/index.html         ← 可直接打开的静态网页
```

`<slug>` 是话题的英文简称，由 Phase 1 确认时确定，仅含英文小写字母、数字、连字符。
示例：`physio-ui`、`posture-hci`、`llm-agent`

---

## 当前搜索链路

当前 `ccf-research` 和网页端 natural-language research 共用同一条搜索链路：

1. 用户输入自然语言研究需求
2. 智能体先生成一版更贴合学术表达的检索词建议
   - 对“影响因素 / 决定因素 / 预测因素 / 作用机制”这类中文需求，要优先改写成更像论文标题的英文名词短语，而不是 `impact analysis`、`effects assessment` 这类解释型短语
3. 基于建议词生成正式搜索方案，并把建议词合并进最终可执行关键词
4. 用户确认后执行搜索
5. 如果第一次召回结果过少，系统会自动补充一轮建议检索词后重试
6. 搜索结果出来后，再由智能体结合标题和摘要复核相关性
7. 为每篇论文补充 `relevance_label`、`relevance_score`、`autotags`、`review_reason`
8. 最终导出网页、CSV、JSON

这意味着：

- 前置阶段不会直接拿口语化描述去搜
- 前置阶段会主动规避 `impact analysis`、`factors influence`、`interaction evaluation` 这类不利于标题检索的短语
- 结果阶段不会只按原始召回顺序展示
- 高相关结果会优先排在前面，并带自动标签
- 单次最终结果默认不超过 200 篇，但会尽量覆盖每个关键词和 venue 的命中
- DBLP 请求现在按单次组合回退到 OpenAlex；某一组临时失败不会让整轮搜索都放弃 DBLP

---

## Phase 1 — 询问确认（必须先完成，再搜索）

**不要直接开始搜索。** 先展示草稿方案，等用户确认后再执行。

### 展示草稿方案

按以下格式一次性展示，语气自然简洁：

```
好的，我理解你的方向是：[用一句话概括话题]

智能建议检索词：
  核心概念：[concept1], [concept2]
  建议关键词：[kw1], [kw2], [kw3]
  建议避免：[avoid1], [avoid2]

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

- 智能建议检索词是否贴合这个研究问题？有没有更像论文标题或摘要会出现的术语？
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

- 必须在 `openclaw-analytics` conda 环境中执行
- Python 解释器统一使用 `/home/ubuntu/miniconda3/envs/openclaw-analytics/bin/python`
- 仓库固定入口为 `/home/ubuntu/tools/exScholar/run_search.sh`，关键词搜索优先走这个脚本
- 静态站点固定服务端口为 `38128`，公开基址由 `.env.local` 中的 `PUBLIC_SITE_BASE_URL` 控制
- 不要直接使用系统默认 `python` 或 base 环境的 Python 3.13；该环境下摘要依赖 `aiohttp` 可能不可用
- 关键词搜索现在会自动进入 exScholar 的共享 research 并发槽位；如果网页端已有任务在跑，这里会先排队再开始
- 首次使用前确保已安装 Playwright 浏览器：

```bash
/home/ubuntu/miniconda3/envs/openclaw-analytics/bin/python -m playwright install chromium
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
/home/ubuntu/miniconda3/envs/openclaw-analytics/bin/python -m app.pipeline.search ...
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

如果用户要求跑主爬虫而不是关键词搜索，也同样使用 `openclaw-analytics` 的 Python：

```bash
/home/ubuntu/miniconda3/envs/openclaw-analytics/bin/python -m app.pipeline.main -ccf a -c conf -m 20 -p 10
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

当前结果里如果出现以下字段，属于正常输出：

- `relevance_label`
- `relevance_score`
- `autotags`
- `review_reason`

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
- 当前链路会先生成一版“学术化检索建议”，再产出正式 plan；如果建议词不贴切，应优先调整建议词。
- 如果用户需求属于“因素 / 机制 / 预测”类问题，优先使用 `user experience factors`、`usability predictors`、`interaction outcomes`、`human factors in HCI` 这类名词短语。
- 搜索结束后会基于标题和摘要做二次复核，所以页面中的排序和标签可能与原始召回顺序不同。
- 某关键词组命中 0 篇时，提示用户换同义词或放宽措辞。
- `papers.csv` 的 `matched_kw` 列记录每篇由哪组关键词命中，网页中也可据此展示。
- 无摘要的论文（`abstract` 为空）在网页中标注"暂无内容"，不影响标题级别的浏览。
- CSV、JSON 和静态网页会一并保存，下次可直接打开网页查看，无需重新爬取。
- `search.json` 中的 `fallback_events` 可以用来判断某次结果变少到底是检索词问题还是 DBLP 临时波动。
