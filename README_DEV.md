# exScholar 开发说明

这份文档面向继续维护 exScholar 的开发者，也适合作为 coding agent 的项目入口。

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

### 4.4 当前自然语言搜索链路

自然语言 research 和 `ccf-research` skill 当前共用同一条链路：

1. [ingest.py](/home/ubuntu/tools/exScholar/app/openclaw/ingest.py) 中的 `suggest_research_queries(...)` 先把用户自然语言需求改写成更贴合学术表达的检索建议
2. `plan_research_request(...)` 基于建议词生成正式 research plan
3. [research_jobs.py](/home/ubuntu/tools/exScholar/app/site/core/research_jobs.py) 启动搜索子进程
4. [search.py](/home/ubuntu/tools/exScholar/app/pipeline/search.py) 执行召回、去重、导出
5. `review_research_results(...)` 结合标题和摘要做相关性复核，并补 `relevance_label`、`relevance_score`、`autotags`、`review_reason`

当前导出的 `papers.csv`、`papers.json` 和结果页都能带这些复核字段。

### 4.5 默认 OpenClaw 用户

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
