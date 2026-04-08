# exScholar

`exScholar` 是一套本地优先的论文搜索、扩展检索与深度阅读工作台。

当前仓库已经整理为以 `app/` 为主的结构：

- `app/pipeline`：关键词搜索、主爬虫、摘要抓取、导出静态站点
- `app/site`：阅读站点、SQLite 文献库、阅读工作区、HTTP 接口
- `app/openclaw`：PDF intake、元数据提取、论文结构化分析、问答链路
- `app/common`：共享工具

## 当前能力

- 按关键词搜索 DBLP 论文，并生成 `CSV`、`JSON` 和静态网页
- 抓取摘要并在结果页中浏览
- 从单篇论文继续做引文/参考文献扩展搜索
- 将论文加入深度阅读库，并绑定 PDF
- 通过 PDF `sha256` 去重，重复上传时直接复用已有文献记录
- 为论文创建阅读工作区，生成：
  - `paper.json`
  - `analysis.json`
  - `qa_history.json`
  - `notes.json`
  - `source/full_text.json`
  - `source/sections.json`
- 在阅读页里执行：
  - 元数据识别
  - 重新分析
  - 提问并保存问答历史
  - 删除单条提问记录
  - 按模块保存手工 Notes
- 将分析得到的 `research_theme` 回写到 citation `tags`，并在 `/keywords` 页面展示

## OpenClaw 链路

当前网页端和微信端统一使用 `app.openclaw` 处理论文 PDF。

已接管的入口：

- `/reading` 页面单篇上传 PDF
- `/reading` 页面批量上传 PDF
- 阅读页手动识别元数据
- 阅读页开始分析 / 重新分析
- 阅读页问答
- `/reading` 页面一键补全未完成项
- 本地 CLI
- 微信附件触发

默认模型策略：

- 主读取：`joybuilder-plan/DeepSeek-V3.2`
- 检查：`joybuilder-plan/GLM-5`
- 回退：`joybuilder-plan/Kimi-K2.5`

配置来源：

- `OPENCLAW_INGEST_MODEL`
- `OPENCLAW_INGEST_CHECK_MODEL`
- `OPENCLAW_INGEST_FALLBACK_MODEL`
- `OPENCLAW_CONFIG_PATH`
- `~/.openclaw/openclaw.json`

## 环境要求

- Python `3.11+`
- Conda 环境：`openclaw-analytics`
- Playwright `chromium`
- 可访问 DBLP / OpenAlex / AI4Scholar / 京东 Coding Plan 对应模型接口

推荐安装：

```bash
conda env create -f environment.yml
conda activate openclaw-analytics
python -m playwright install chromium
```

如果通过 OpenClaw 的 conda 包装器执行：

```bash
oc-conda-run -- python -m playwright install chromium
```

## 依赖

[requirements.txt](/home/ubuntu/tools/exScholar/requirements.txt) 与 [environment.yml](/home/ubuntu/tools/exScholar/environment.yml) 当前覆盖：

- `requests`
- `aiohttp`
- `tqdm`
- `prettytable`
- `python-dotenv`
- `playwright`
- `fake-useragent`
- `beautifulsoup4`
- `pypdf`

## 配置

复制 `.env.local.example` 为 `.env.local` 后再填写。

站点相关：

- `PUBLIC_SITE_BASE_URL`
- `PUBLIC_SITE_PORT`
- `SITE_SERVER_HOST`
- `SITE_PASSWORD_SALT`
- `SITE_PASSWORD_HASH`
- `SITE_SESSION_SECRET`

搜索 / 扩展相关：

- `REFERENCE_EXPAND_LIMIT`
- `AI4SCHOLAR_API_KEY`

OpenClaw 论文处理相关：

- `OPENCLAW_INGEST_MODEL`
- `OPENCLAW_INGEST_CHECK_MODEL`
- `OPENCLAW_INGEST_FALLBACK_MODEL`
- `OPENCLAW_CONFIG_PATH`

代理相关：

- `PROXY_API_KEY`
- `PROXY_API_SIGN`
- `PROXY_USERNAME`
- `PROXY_PASSWORD`

## 常用命令

关键词搜索：

```bash
./run_search.sh \
  --keywords "physiological notification;biosignal alert" \
  --venues "chi,uist,cscw" \
  --slug "physio-ui" \
  --top 50 \
  --year-from 2020
```

直接执行搜索入口：

```bash
oc-conda-run -- python -m app.pipeline.search \
  --keywords "physiological notification;biosignal alert" \
  --venues "chi,uist,cscw" \
  --slug "physio-ui" \
  --top 50 \
  --year-from 2020
```

运行主爬虫：

```bash
oc-conda-run -- python -m app.pipeline.main -ccf a -c conf -m 20 -p 10
```

启动站点：

```bash
oc-conda-run -- python -m app.site.http.handler
```

设置站点密码：

```bash
oc-conda-run -- python set_site_password.py --password 'your-password'
```

本地导入单个 PDF：

```bash
cd /home/ubuntu/tools/exScholar
python -m app.openclaw.intake_cli --wait --json /absolute/path/to/paper.pdf
```

一次导入多个 PDF：

```bash
cd /home/ubuntu/tools/exScholar
python -m app.openclaw.intake_cli --wait --json /path/a.pdf /path/b.pdf
```

## 数据目录

搜索结果：

```text
data/searches/YYYY-MM-DD_<slug>/
```

扩展搜索：

```text
data/expansions/YYYY-MM-DD_<slug>/
```

PDF 库：

```text
data/library/
```

OpenClaw intake 任务：

```text
data/openclaw_jobs/
```

深度阅读工作区：

```text
data/reading/<paper_id>/
  ├── paper.json
  ├── analysis.json
  ├── qa_history.json
  ├── notes.json
  └── source/
      ├── full_text.json
      └── sections.json
```

说明：

- PDF 文件本体只保存在 `data/library/`
- `data/reading/<paper_id>/` 不再重复保存 PDF
- 运行产物主要都在 `data/`

## 深度阅读流程

1. 从搜索结果页加入论文，或在 `/reading` 直接上传 PDF。
2. 系统对 PDF 计算 `sha256` 并去重。
3. `app.openclaw` 提取 PDF 文本。
4. 使用 OpenClaw 当前配置中的京东 Coding Plan 模型做元数据识别。
5. 按 DOI / 标题+年份 / 标题相似度匹配已有 citation。
6. 创建或更新 citation，并绑定 PDF。
7. 创建或刷新阅读工作区。
8. 在需要时生成结构化论文分析。
9. 阅读页问答也复用同一条模型链路。
10. 分析结束后把主题词写回 citation `tags`。

## 页面结构

- `/`
  搜索时间线，展示原始搜索与扩展搜索
- `/keywords`
  所有关键词索引页
- `/keywords/<keyword>`
  某个关键词对应的论文列表
- `/reading`
  深度阅读库，支持分组、PDF 绑定、标签筛选、上传进度、批量处理
- `/reading/<paper_id>`
  单篇论文阅读页，元数据识别、分析、问答都走 `app.openclaw`

## 目录结构

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
├── run_search.sh
├── set_site_password.py
├── requirements.txt
├── environment.yml
└── skills/
```

说明：

- `app/pipeline/` 是搜索、抓取、导出的真实代码位置
- `app/site/` 是站点、阅读页、数据库与 HTTP handler 的真实代码位置
- `app/openclaw/` 是 OpenClaw intake 与论文解析的真实代码位置
- `docs/` 放 OpenClaw 论文导入相关说明
- `skills/ccf-research/SKILL.md` 是研究搜索技能说明

## 文档

- [OPENCLAW_ADDON.md](/home/ubuntu/tools/exScholar/docs/OPENCLAW_ADDON.md)
- [WECHAT_PDF_INTAKE.md](/home/ubuntu/tools/exScholar/docs/WECHAT_PDF_INTAKE.md)

## 开发说明

- `.env.local` 不纳入版本控制
- 运行产物大多位于 `data/`
- 当前主逻辑已经迁到 `app/site`、`app/pipeline`、`app/openclaw`
- `openclaw-analytics` 是当前推荐运行环境
