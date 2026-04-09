# exScholar 开发说明

这份文档面向继续维护 exScholar 的开发者，也适合作为 coding agent 的项目入口。当前仓库默认对应一套云服务器常驻部署：`exscholar-site.service` 和 `openclaw-gateway.service` 一起运行在服务器上，对外提供网页和 OpenClaw 能力。

为避免文档和单机部署路径强绑定，下面统一使用两个占位写法：

- `<repo-root>`：仓库根目录
- `<openclaw-python>`：`openclaw-analytics` 环境中的 Python，可通过 `OPENCLAW_ANALYTICS_PYTHON` 指向

## 1. 开发目标

exScholar 当前已经整理为以 `app/` 为中心的结构。后续开发建议继续遵守以下边界：

- 搜索链路改 `app/pipeline/`
- 站点和 API 改 `app/site/`
- PDF intake、模型调用、分析与问答改 `app/openclaw/`
- 通用工具改 `app/common/`
- 根目录只保留文档、环境、启动脚本和少量管理脚本

当前默认运行形态：

- 云服务器常驻运行
- 网站服务：`exscholar-site.service`
- OpenClaw 网关：`openclaw-gateway.service`
- research 子进程、CLI、站点服务统一复用 `openclaw-analytics` 环境

## 2. 项目结构

```text
.
├── app/
│   ├── common/
│   ├── openclaw/
│   ├── pipeline/
│   │   ├── config/
│   │   └── crawler/
│   └── site/
│       ├── core/
│       ├── http/
│       └── ui/
├── docs/
├── data/
├── skills/
├── run_search.sh
├── set_site_password.py
├── environment.yml
└── requirements.txt
```

## 3. 关键模块

搜索相关：

- [search.py](/home/ubuntu/tools/exScholar/app/pipeline/search.py)
- [main.py](/home/ubuntu/tools/exScholar/app/pipeline/main.py)
- [driver.py](/home/ubuntu/tools/exScholar/app/pipeline/driver.py)
- [venue.py](/home/ubuntu/tools/exScholar/app/pipeline/config/venue.py)
- [special_rules.py](/home/ubuntu/tools/exScholar/app/pipeline/config/special_rules.py)

OpenClaw 相关：

- [ingest.py](/home/ubuntu/tools/exScholar/app/openclaw/ingest.py)
- [intake_cli.py](/home/ubuntu/tools/exScholar/app/openclaw/intake_cli.py)
- [paper_lookup.py](/home/ubuntu/tools/exScholar/app/openclaw/paper_lookup.py)
- [picsearch_cli.py](/home/ubuntu/tools/exScholar/app/openclaw/picsearch_cli.py)
- [textsearch_cli.py](/home/ubuntu/tools/exScholar/app/openclaw/textsearch_cli.py)

站点与数据相关：

- [handler.py](/home/ubuntu/tools/exScholar/app/site/http/handler.py)
- [pages.py](/home/ubuntu/tools/exScholar/app/site/ui/pages.py)
- [base.py](/home/ubuntu/tools/exScholar/app/site/core/base.py)
- [auth.py](/home/ubuntu/tools/exScholar/app/site/core/auth.py)
- [citations.py](/home/ubuntu/tools/exScholar/app/site/core/citations.py)
- [reading.py](/home/ubuntu/tools/exScholar/app/site/core/reading.py)
- [jobs.py](/home/ubuntu/tools/exScholar/app/site/core/jobs.py)
- [research_jobs.py](/home/ubuntu/tools/exScholar/app/site/core/research_jobs.py)
- [references.py](/home/ubuntu/tools/exScholar/app/site/core/references.py)

## 4. 当前架构要点

### 4.1 多用户数据隔离

站点当前是多用户模式。

每个用户的数据位于：

```text
data/users/<username>/
```

其中包括：

- `searches/`
- `expansions/`
- `library/`
- `reading/`
- `openclaw_jobs/`
- `research_jobs/`
- `citation_library.sqlite3`

用户上下文由 [base.py](/home/ubuntu/tools/exScholar/app/site/core/base.py) 中的 `user_context(...)` 和动态路径解析管理。

### 4.2 OpenClaw 链路

网页和 CLI 统一走 `app.openclaw` 链路处理 PDF。

当前接管的入口：

- `/reading` 页面 PDF 上传
- 阅读页元数据识别
- 阅读页重新分析
- 阅读页问答
- 本地 CLI
- 微信附件触发
- OpenClaw 对话侧 `picsearch`、`textsearch` 标准动作

搜索结果页的“加入深度阅读”当前不做自动 PDF 抓取。
该入口只负责展示原文链接，并要求用户手动下载后上传 PDF。

当前对话侧补链接链路分两类：

- `picsearch`
  - 处理单篇论文截图或 Google Scholar 页面截图
  - 单篇截图：图片识别 -> DBLP 模糊匹配 -> 官方 web 候选筛选 -> DOI fallback
  - Scholar 页面截图：先识别多篇标题，再逐条走同一条补链接链路
- `textsearch`
  - 只处理纯文本标题输入
  - 支持一个标题或多个标题
  - 处理顺序：标题匹配 -> DBLP -> 官方 web 候选筛选 -> DOI fallback

`picsearch` 和 `textsearch` 在补链接后都会尽量继续抓取摘要，并分别写入当天 `Picsearch` / `Textsearch` timeline。

从这两个 timeline 加入深度阅读时，后端不会直接把来源名当作 Reading Group 名。当前逻辑会优先根据 `autotags`、标题和 venue 生成一个更像主题短名的 group，例如 `Human-Computer Interaction`、`Mobile Interaction`。

### 4.3 搜索并发

网页 Research 和 OpenClaw `ccf-research` skill 共享同一套搜索并发控制。

- 默认并发上限：`MAX_CONCURRENT_RESEARCH_JOBS=2`
- 实际限流在 [search.py](/home/ubuntu/tools/exScholar/app/pipeline/search.py) 中完成
- 子进程 research 使用 `OPENCLAW_ANALYTICS_PYTHON`

### 4.3.1 引用扩展任务

引用扩展不再在 HTTP 请求线程里同步完成。

当前行为：

- `/api/papers/expand-references` 只负责创建后台 job
- 前端通过 `GET /api/papers/expand-references/jobs/<id>` 轮询状态
- 搜索结果静态页和 Keywords 页都会显示中间步骤消息
- 两处前端轮询都带有：
  - 单次请求超时保护
  - 整体等待超时保护

当前 `site_url` 也统一为相对路径，避免外网 `PUBLIC_SITE_BASE_URL` 和内网访问不一致时的链接失效问题。

### 4.4 当前自然语言搜索链路

自然语言 research 和 `ccf-research` skill 当前共用同一条链路：

1. [ingest.py](/home/ubuntu/tools/exScholar/app/openclaw/ingest.py) 中的 `suggest_research_queries(...)` 先把用户自然语言需求改写成更贴合学术表达的检索建议
2. `plan_research_request(...)` 基于建议词生成正式 research plan，并把 `candidate_keywords/core_concepts` 合并进最终可执行关键词
   - 对“影响因素 / 决定因素 / 预测因素 / 作用机制”这类 HCI 需求，会优先改写成更像标题检索的名词短语，例如 `user experience factors`、`usability predictors`、`human factors in HCI`
3. [research_jobs.py](/home/ubuntu/tools/exScholar/app/site/core/research_jobs.py) 启动搜索子进程
4. [search.py](/home/ubuntu/tools/exScholar/app/pipeline/search.py) 执行召回、去重、导出
5. 如果第一次召回结果少于约 80 篇，[research_jobs.py](/home/ubuntu/tools/exScholar/app/site/core/research_jobs.py) 会自动补充一轮建议检索词后重试
6. `review_research_results(...)` 结合标题和摘要做相关性复核，并补 `relevance_label`、`relevance_score`、`autotags`、`review_reason`

当前导出的 `papers.csv`、`papers.json` 和结果页都能带这些复核字段。
搜索结果最终默认不超过 200 篇，但不是简单截断，而是按 `关键词 × venue` 组合做覆盖优先保留。
底层数据源方面，当前策略是：

- 每个 `关键词 × venue` 组合优先请求 DBLP
- DBLP 请求会做更稳的 direct/proxy 重试
- 单次 DBLP 请求失败时，只让当前组合回退到 OpenAlex
- `search.json` 中会保留 `fallback_events`，用于说明哪些组合发生了 DBLP -> OpenAlex 的回退

### 4.5 当前限流与抗 RPM 策略

自然语言 research、PDF metadata/analysis、`picsearch`、`textsearch` 共享同一套后台模型调用节流：

- [ingest.py](/home/ubuntu/tools/exScholar/app/openclaw/ingest.py) 中的 `MODEL_REQUEST_SEMAPHORE`
- provider 级最小请求间隔
- `review_research_results(...)` 使用较大的 chunk 和批间节流，降低模型 RPM 峰值

当前默认参数：

- `OPENCLAW_MODEL_CONCURRENCY=1`
- `REVIEW_RESEARCH_RESULTS_CHUNK_SIZE=12`
- `REVIEW_BATCH_THROTTLE_SECONDS=0.8`

网页端 research 还做了一个额外收口：

- 刚生成的方案如果没有手工修改，点击“开始搜索”会直接提交
- 只有手工改过方案字段时，才会再调用 `/api/research/plan/validate`
- 这样可以避免“第一次提交”额外多打一轮模型校验

### 4.6 模型链路默认不走代理

当前项目里与模型推理直接相关的链路，默认都按“直连上游 provider，不继承系统 HTTP 代理”处理。

覆盖范围：

- OpenClaw gateway 的 systemd 服务环境
- exScholar 站点服务的 systemd 服务环境
- [ingest.py](/home/ubuntu/tools/exScholar/app/openclaw/ingest.py) 中所有基于 OpenClaw provider 的模型调用
  - research 检索词建议
  - research plan 生成与 revise/validate
  - 结果复核与 autotag
  - PDF metadata / analysis
  - `picsearch` 图片识别
- [search.py](/home/ubuntu/tools/exScholar/app/pipeline/search.py) 中用于 DBLP/OpenAlex 的 no-proxy session
- [references.py](/home/ubuntu/tools/exScholar/app/site/core/references.py) 中的 AI4Scholar / Crossref / OpenAlex enrich 请求
- [paper_lookup.py](/home/ubuntu/tools/exScholar/app/openclaw/paper_lookup.py) 中的 Google Scholar profile 抓取与 DuckDuckGo web 搜索
  - 其中 DuckDuckGo web fallback 支持单独配置 `DUCKDUCKGO_HTTP_PROXY`
  - 这样可以在服务器直连 DuckDuckGo 不可达时，只给这条 web 搜索链挂代理，而不影响模型推理、DBLP、Crossref、OpenAlex 的 no-proxy 策略

当前做法：

- systemd 服务显式清空 `HTTP_PROXY`、`HTTPS_PROXY`、`ALL_PROXY`
- 保留 `NO_PROXY=127.0.0.1,localhost,::1`
- `app.openclaw.ingest` 内部使用 `requests.Session(trust_env=False)` 发起模型请求
- 共享 HTTP 工具现在统一提供 no-proxy session，避免后加的 `requests.get(...)` 又重新继承 shell 代理

当前诊断方式：

- research 规划结果里的 `diagnostics` 会包含：
  - `model_http: no_proxy`
- PDF metadata / analysis 的后台任务步骤提示会显示：
  - `model_http=no_proxy`
- `picsearch` / `textsearch` CLI 输出也会打印：
  - `model_http=no_proxy`

这几个标记的作用是帮助区分：

- 当前模型调用是否明确绕过了 shell / 系统代理
- 问题更可能来自上游 provider，还是来自本地代理链

这意味着：

- 即使当前 shell 里仍有代理环境变量，服务内和 `app.openclaw.ingest` 的模型请求也不会自动走代理
- 普通 CLI / `curl` / 非模型抓取链路如果直接在终端里运行，仍可能继承当前 shell 的代理环境
- 如果要对某条命令也显式禁用代理，建议这样运行：

```bash
env -u HTTP_PROXY -u HTTPS_PROXY -u ALL_PROXY -u http_proxy -u https_proxy -u all_proxy \
  <openclaw-python> -m app.openclaw.textsearch_cli --help
```
### 4.7 默认 OpenClaw 用户

非网页登录触发的 OpenClaw intake 和默认 CCF research 搜索，当前默认写入：

```text
data/users/qioyo/
```

对应逻辑可查看：

- [base.py](/home/ubuntu/tools/exScholar/app/site/core/base.py)
- [intake_cli.py](/home/ubuntu/tools/exScholar/app/openclaw/intake_cli.py)
- [handler.py](/home/ubuntu/tools/exScholar/app/site/http/handler.py)
- [run_search.sh](/home/ubuntu/tools/exScholar/run_search.sh)

## 5. 运行环境

项目现在统一使用：

```text
<openclaw-python>
```

已经对齐的入口包括：

- systemd 服务 `exscholar-site.service`
- `run_search.sh`
- research 子进程解释器
- CLI 入口脚本 shebang
- 主要 Python 脚本 shebang

安装环境：

```bash
conda env create -f environment.yml
conda activate openclaw-analytics
python -m playwright install chromium
```

## 6. 开发约定

- 不要把新业务逻辑重新写回根目录
- `data/` 只放运行产物，不放源码
- 不要把运行产物写到 `app/data/`
- 只是改网页文案或样式时，优先改 [pages.py](/home/ubuntu/tools/exScholar/app/site/ui/pages.py)
- 只是改模型策略时，优先改 [ingest.py](/home/ubuntu/tools/exScholar/app/openclaw/ingest.py)
- 只是改 API 行为时，优先改 [handler.py](/home/ubuntu/tools/exScholar/app/site/http/handler.py) 和 `app/site/core/`
- 修改数据结构时，优先考虑兼容已有用户目录和 SQLite 文件

## 7. 常用开发命令

编译检查：

```bash
<openclaw-python> -m py_compile \
  $(find app -name '*.py' | sort) set_site_password.py
```

同步 skills 到 OpenClaw：

```bash
/home/ubuntu/tools/exScholar/sync_openclaw_skills.sh
```

启动站点：

```bash
systemctl --user restart exscholar-site.service
systemctl --user status exscholar-site.service --no-pager
```

本地运行站点进程：

```bash
<openclaw-python> -u -m app.site.http.handler
```

运行一个小搜索：

```bash
./run_search.sh \
  --keywords "test keyword" \
  --venues "chi" \
  --slug "smoke-test" \
  --top 5
```

本地导入 PDF：

```bash
<openclaw-python> -m app.openclaw.intake_cli \
  --wait --json /absolute/path/to/paper.pdf
```

图片找论文：

```bash
<openclaw-python> -m app.openclaw.picsearch_cli \
  --wait --json /absolute/path/to/paper-screenshot.png
```

文本补链接：

```bash
<openclaw-python> -m app.openclaw.textsearch_cli \
  --wait --json "Paper Title A\nPaper Title B"
```

运行主爬虫：

```bash
<openclaw-python> -m app.pipeline.main \
  -ccf a -c conf -m 20 -p 10
```

## 8. 服务管理

当前站点推荐通过 user-level systemd 服务管理。

查看状态：

```bash
systemctl --user status exscholar-site.service --no-pager
```

重启：

```bash
systemctl --user restart exscholar-site.service
```

重载配置：

```bash
systemctl --user daemon-reload
```

查看日志：

```bash
journalctl --user -u exscholar-site.service -n 100 --no-pager
```

OpenClaw 网关：

```bash
systemctl --user restart openclaw-gateway.service
systemctl --user status openclaw-gateway.service --no-pager
journalctl --user -u openclaw-gateway.service -n 100 --no-pager
```

## 9. 最小验证清单

改动后优先做最小验证：

1. 编译检查
2. 如果改了站点，重启服务并访问首页
3. 如果改了 PDF 链路，跑一个 intake
4. 如果改了搜索链路，跑一个小搜索
5. 如果改了多用户逻辑，至少验证一个用户登录和一条用户隔离数据路径

## 10. 相关文档

- 项目总览：[README.md](/home/ubuntu/tools/exScholar/README.md)
- 用户说明：[README_USER.md](/home/ubuntu/tools/exScholar/README_USER.md)
- 架构说明：[ARCHITECTURE.md](/home/ubuntu/tools/exScholar/docs/ARCHITECTURE.md)
- OpenClaw 补充说明：[OPENCLAW_ADDON.md](/home/ubuntu/tools/exScholar/docs/OPENCLAW_ADDON.md)
- Skills 总览：[README.md](/home/ubuntu/tools/exScholar/skills/README.md)
- OpenClaw 图片找论文 skill：[picsearch/SKILL.md](/home/ubuntu/tools/exScholar/skills/picsearch/SKILL.md)
- OpenClaw 文本补链接 skill：[textsearch/SKILL.md](/home/ubuntu/tools/exScholar/skills/textsearch/SKILL.md)
- 微信 PDF intake：[WECHAT_PDF_INTAKE.md](/home/ubuntu/tools/exScholar/docs/WECHAT_PDF_INTAKE.md)
- 搜索 skill：[SKILL.md](/home/ubuntu/tools/exScholar/skills/ccf-research/SKILL.md)
