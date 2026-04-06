# exScholar

一个用于检索论文、抓取摘要、构建关键词导航、生成静态研究站点并支持延展搜索与综述导出的工具。

## 功能特性

- **论文元信息获取**：通过 DBLP API 获取指定 CCF 等级的会议/期刊论文元信息
- **多源摘要获取**：支持通过多种方式获取论文摘要
  - API 方式：Crossref、OpenAlex、Semantic Scholar
  - 网站爬取：针对 ACM、IEEE、arXiv、OpenReview... 等超多适配
- **代理池管理**：自动管理代理池，支持代理失效时降级到本机地址
- **搜索结果静态站点**：每次关键词搜索都会额外生成 `papers.json` 和可直接打开的 `site/index.html`
- **Citation 库**：可在网页中将单篇论文加入本地 Citation 库
- **引用扩展搜索**：可对单篇论文扩展采集其引用文献并生成新的结果页面
- **站点密码保护**：支持整站登录保护，避免未授权访问

## 环境要求

- 推荐使用 Conda 环境 `openclaw-analytics`
- Python `3.11`
- 需要安装 Playwright 的 `chromium` 浏览器
- 不建议直接使用系统默认 `python` 或 base 环境的 Python 3.13；当前摘要链路依赖 `aiohttp`，在该环境下可能无法正常安装

## 安装

```bash
# 方式 1：推荐，直接按仓库环境文件创建
conda env create -f environment.yml
conda activate openclaw-analytics

# 安装 Playwright 浏览器
python -m playwright install chromium

# 配置代理（可选）
# 复制 .env.local.example 为 .env.local，并填入代理 API 配置
```

如果本机已提供 `oc-conda-run`，也可以不手动激活环境，直接这样执行：

```bash
oc-conda-run -- python -m playwright install chromium
oc-conda-run -- python search.py --keywords "openclaw" --slug demo --top 5 --year-from 2020
oc-conda-run -- python serve_searches.py
oc-conda-run -- python set_site_password.py --password 'your-password'
```

仓库也提供了固定环境入口脚本：

```bash
./run_search.sh --keywords "openclaw" --slug demo --top 5 --year-from 2020
```

如需继续使用 `pip` 安装，请先进入 Python 3.11 环境再执行：

```bash
pip install -r requirements.txt
python -m playwright install chromium
```

### 代理配置

本项目使用**神龙代理**（api.shenlongip.com）作为代理服务。如需使用代理功能，请：

1. 访问 [神龙代理官网](http://www.shenlongip.com/) 注册账号并购买代理服务
2. 在控制台获取 `API Key` 和 `API Sign`
3. 创建 `.env.local` 文件，配置以下环境变量：

```bash
PROXY_API_KEY=你的API_Key
PROXY_API_SIGN=你的API_Sign
```

**注意**：如果不配置代理，程序会自动降级使用本机地址进行请求，但可能受到访问频率限制。

## 使用方法

### 基本使用

```bash
# 使用短参数（推荐）
oc-conda-run -- python main.py -ccf a -c conf -m 20 -p 10
```

```bash
# 关键词搜索并抓取摘要
oc-conda-run -- python search.py \
  --keywords "openclaw" \
  --slug "openclaw-abstract-check" \
  --top 5 \
  --year-from 2020
```

```bash
# 等价的固定环境入口
./run_search.sh \
  --keywords "openclaw" \
  --slug "openclaw-abstract-check" \
  --top 5 \
  --year-from 2020
```

### 命令行参数

- `-ccf`: CCF 等级，可选值 `a`、`b`、`c`
- `-c, --classification`: 论文分类类型，可选值 `conf`（会议）或 `journal`（期刊）
- `-m, --max-concurrent`: 最大并发数
- `-p, --proxy-pool-size`: 代理池大小

### 输出说明

- 论文数据保存在 `data/paper/{classification}_{ccf}/` 目录
- 日志文件保存在 `data/logs/` 目录
- 每个会议/期刊的数据以 JSON 文件形式保存
- 原始关键词搜索结果保存在 `data/searches/YYYY-MM-DD_<slug>/`
- 延展搜索结果保存在 `data/expansions/YYYY-MM-DD_<slug>/`
- 搜索目录内默认包含 `search.json`、`papers.csv`、`papers.json`、`site/index.html`
- 启动静态站点服务后，可通过 `PUBLIC_SITE_BASE_URL/searches/YYYY-MM-DD_<slug>/site/` 或 `PUBLIC_SITE_BASE_URL/expansions/YYYY-MM-DD_<slug>/site/` 直接公网访问

### 静态站点服务

默认对外端口固定为 `38128`，服务根目录为 `data/`，其中原始搜索与延展搜索分别存放在 `searches/` 和 `expansions/` 下。

```bash
oc-conda-run -- python serve_searches.py
```

站点地址由 `.env.local` 控制，相关字段：

- `PUBLIC_SITE_BASE_URL`
- `PUBLIC_SITE_HOST`
- `PUBLIC_SITE_PORT`
- `SITE_SERVER_HOST`

### 站点密码

整站支持登录保护。推荐用下面的脚本生成密码哈希并写入 `.env.local`：

```bash
oc-conda-run -- python set_site_password.py --password 'your-password'
```

相关字段：

- `SITE_PASSWORD_SALT`
- `SITE_PASSWORD_HASH`
- `SITE_SESSION_SECRET`
- `REFERENCE_EXPAND_LIMIT`
- `AI4SCHOLAR_API_KEY`

启用后，时间线首页、搜索结果页、Citation 库以及 CSV/JSON 下载都需要先登录。

### Citation 库与引用扩展

搜索结果页中的每篇论文卡片现在支持：

- `加入 Citation 库`
- `扩展引用`

`加入 Citation 库` 会把论文保存到本地 SQLite 库中；  
默认会把该论文命中的 `matched_kw` 自动写入 citation 的 `tags` 字段；  
Citation 库页面支持按 tag 筛选、手动编辑 tag，以及导出所选 JSON；  
`延展搜索` 会优先通过 `ai4scholar` 的 citations API 拉取引文列表，并自动生成新的搜索结果网页；  
若未配置 `AI4SCHOLAR_API_KEY` 或该链路不可用，则会回退到基于 DOI 的 Crossref/OpenAlex 方案。

### 本地综述导出

如果你希望基于当前项目中已有的 keywords 和论文摘要来写综述，可以使用：

```bash
oc-conda-run -- python keyword_review.py --query "stress coping UI research review" --slug stress-review
```

脚本会自动：

- 读取当前项目已有的 keywords
- 匹配最相关的关键词
- 导出这些关键词下的论文与摘要
- 将引用日志写入 `data/review_logs/YYYY-MM-DD_<slug>/`

默认输出：

- `request.json`
- `papers.json`
- `citations.json`
- `review.md`

## 项目结构

```
.
├── main.py                 # 主程序入口
├── search.py               # 关键词搜索 + CSV/JSON/静态网页输出
├── run_search.sh           # 固定环境的搜索入口
├── serve_searches.py       # 对外提供搜索结果静态站点
├── set_site_password.py    # 生成站点密码哈希并写入 .env.local
├── driver.py               # Playwright 驱动和代理池管理
├── utils.py                # 工具函数
├── crawler/
│   ├── fetch_meta.py       # 论文元信息获取
│   └── fetch_abstract.py   # 论文摘要获取（异步版本）
└── config/
    ├── venue.py            # CCF 会议/期刊配置
    └── special_rules.py    # 特殊规则配置
```

## 声明

本项目仅用于学习和研究目的。本项目不存储任何论文的完整内容，仅获取公开的元信息和摘要信息。

如本项目涉及任何侵权行为，请及时通知，我们将立即删除相关内容。
