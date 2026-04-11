# exScholar 架构说明

这份文档面向需要快速建立全局理解的开发者，重点说明 exScholar 当前的模块划分、运行方式、用户上下文和核心数据流。当前仓库默认对应一套云服务器常驻部署：`exscholar-site.service` 和 `openclaw-gateway.service` 一起运行，对外提供网页与 OpenClaw 能力。

## 1. 系统目标

exScholar 当前是一套以云服务器常驻运行为主的论文工作台，核心职责包括：

- 搜索论文并导出结构化结果
- 上传和处理 PDF
- 建立 citation 库和阅读工作区
- 生成结构化分析与问答内容
- 在多用户场景下隔离搜索、阅读和任务数据

## 2. 顶层模块

```text
app/
├── common/
├── openclaw/
├── pipeline/
└── site/
```

模块职责：

- `app/common`
  通用工具和共享辅助函数
- `app/openclaw`
  PDF intake、元数据提取、结构化分析、问答相关模型调用
- `app/pipeline`
  关键词搜索、主爬虫、摘要抓取、CSV/JSON/静态网页导出
- `app/site`
  HTTP 服务、网页渲染、用户数据、阅读库、任务编排和站点逻辑

## 3. 运行时结构

### 3.1 HTTP 服务

当前站点通过 user-level systemd 服务运行：

```text
exscholar-site.service
```

启动命令：

```text
<openclaw-python> -u -m app.site.http.handler
```

这意味着：

- 站点本身运行在 `openclaw-analytics` 环境
- 站点不再依赖临时终端 session
- 服务异常退出后会由 systemd 自动拉起

### 3.2 统一 Python 环境

项目当前统一使用：

```text
<openclaw-python>
```

主要覆盖：

- systemd HTTP 服务
- `run_search.sh`
- OpenClaw CLI
- research 子进程
- 主要入口脚本 shebang

## 4. 多用户架构

### 4.1 用户数据根目录

多用户模式下，每个用户的数据位于：

```text
data/users/<username>/
```

典型结构：

```text
data/users/<username>/
├── searches/
├── expansions/
├── library/
├── reading/
├── openclaw_jobs/
├── research_jobs/
├── reference_jobs/
└── citation_library.sqlite3
```

### 4.2 用户上下文机制

用户上下文由 [base.py](../app/site/core/base.py) 管理，核心机制包括：

- `sanitize_username(...)`
- `user_context(...)`
- `current_username()`
- `DynamicPath`

路径常量如 `DATA_DIR`、`SEARCHES_DIR`、`READING_DIR`、`DB_PATH` 不是静态 `Path`，而是基于当前线程上下文动态解析。

这意味着：

- 同一套业务逻辑可以在不同用户下复用
- HTTP 请求进入后，通过 `user_context(...)` 切到当前会话用户
- 后台线程会显式携带用户名，避免任务在错误用户目录中落盘

## 5. 认证与会话

认证逻辑位于：

- [auth.py](../app/site/core/auth.py)
- [handler.py](../app/site/http/handler.py)

当前特征：

- 使用用户名 + 密码登录
- 用户注册信息保存在 `data/users/users.json`
- HTTP 会话通过 cookie 维持
- 会话带 TTL，超时后会被 `prune_expired_sessions(...)` 清理
- 登录后，页面和 API 仅访问当前用户自己的搜索时间线和阅读库

## 6. 搜索链路

### 6.1 关键词搜索

关键词搜索主入口位于：

- [search.py](../app/pipeline/search.py)
- [run_search.sh](../run_search.sh)

输出产物包括：

- `search.json`
- `papers.csv`
- `papers.json`
- `site/index.html`

### 6.2 Research 后台任务

自然语言 research 任务位于：

- [research_jobs.py](../app/site/core/research_jobs.py)

其流程大致为：

1. 前端提交 research prompt
2. 后端先生成一版更贴合学术表达的检索词建议
   - 对“影响因素 / 决定因素 / 预测因素 / 作用机制”这类需求，会额外收敛成更像标题检索的名词短语
3. 基于建议词生成或校验搜索方案
4. 在后台线程中创建 job
5. 使用 `OPENCLAW_ANALYTICS_PYTHON` 启动 `app.pipeline.search`
6. 如果第一次召回过少，自动补充一轮建议检索词后重试
7. 搜索完成后结合标题和摘要做相关性复核与自动标签
8. 解析 stdout，实时回写 job 状态，包括当前关键词 / venue 和累计找到的论文数
9. 将结果路径映射为站点可访问 URL

底层检索阶段当前采用：

- DBLP 优先
- 单次 DBLP 请求失败时，仅当前 `关键词 × venue` 组合回退到 OpenAlex
- DBLP 请求本身带 direct/proxy 重试
- 回退记录写入 `search.json.fallback_events`

### 6.3 并发控制

搜索共享同一套并发槽位：

- 网页 research
- OpenClaw `ccf-research` skill
- 直接搜索入口

限流逻辑在 [search.py](../app/pipeline/search.py) 中完成。

后台模型调用的节流逻辑位于：

- [ingest.py](../app/openclaw/ingest.py)

当前做法包括：

- 全局模型调用并发闸门
- provider 级最小请求间隔
- research 结果复核按较大 chunk 分批执行，并在批间轻量节流

### 6.4 引用扩展后台任务

引用扩展当前已经不再同步阻塞 HTTP 请求线程，而是改成后台 job：

1. 前端点击“扩展搜索”
2. 后端创建 `reference expansion job`
3. 前端轮询 `GET /api/papers/expand-references/jobs/<id>`
4. 后端串行执行：
   - AI4Scholar / Crossref 引文获取
   - OpenAlex enrich
   - 结果目录和 `site/index.html` 生成
5. 完成后前端打开相应的扩展结果页

当前这条链和 research job 一样，具备：

- 后台状态持久化
- 搜索结果静态页中的步骤提示，以及 Keywords 页中的等待状态反馈
- 单次请求超时保护
- 后台 stale 检测与失败回收
- 结果页相对 URL 输出，避免不同访问入口下的绝对 URL 失效

## 7. OpenClaw PDF 链路

OpenClaw 相关主逻辑位于：

- [ingest.py](../app/openclaw/ingest.py)
- [intake_cli.py](../app/openclaw/intake_cli.py)
- [jobs.py](../app/site/core/jobs.py)

当前统一接管：

- `/reading` 页面 PDF 上传
- 阅读页元数据识别
- 阅读页重新分析
- 本地 CLI
- 微信 / 外部自动化入口
- OpenClaw 对话侧 `picsearch`

核心流程：

1. 上传或读取 PDF
2. 计算哈希并去重
3. 匹配 citation
4. 创建或刷新 reading workspace
5. 抽取元数据
6. 生成结构化分析
7. 写回阅读库、SQLite、任务状态和页面数据

## 8. 默认 OpenClaw 用户

非网页登录触发的 OpenClaw 入口默认使用：

```text
<default-openclaw-user>
```

也就是默认写入：

```text
data/users/<default-openclaw-user>/
```

这一行为主要由以下位置控制：

- [base.py](../app/site/core/base.py)
- [intake_cli.py](../app/openclaw/intake_cli.py)
- [handler.py](../app/site/http/handler.py)
- [run_search.sh](../run_search.sh)

## 9. 阅读库与阅读工作区

阅读库相关逻辑主要在：

- [citations.py](../app/site/core/citations.py)
- [reading.py](../app/site/core/reading.py)

当前模型：

- citation 库保存在每个用户自己的 SQLite 中
- 每篇进入深度阅读的文章会有独立 `paper_id`
- 对应阅读工作区保存在 `reading/<paper_id>/`
- 工作区中通常包含 `paper.json`、`notes.json`、`qa_history.json` 和 `source/`
- `paper.json` 当前是主要状态文件，里面会汇总元数据、分析状态、进度和页面展示所需字段

## 10. HTTP 层与页面层

站点分成两层：

- HTTP 层：
  [handler.py](../app/site/http/handler.py)
- 页面层：
  [pages.py](../app/site/ui/pages.py)

职责分工：

- `handler.py`
  路由、鉴权、请求解析、API 调度、静态文件映射
- `pages.py`
  HTML 输出和页面内交互脚本

## 11. 典型数据流

### 11.1 网页登录后搜索

1. 用户登录
2. `handler.py` 建立用户上下文
3. 发起 research
4. `research_jobs.py` 在该用户目录下创建 job
5. 搜索结果落入该用户的 `searches/`

### 11.2 网页上传 PDF

1. 用户进入 `/reading`
2. 上传 PDF 到 `/api/openclaw-intake/upload`
3. 后端在当前登录用户上下文中创建 OpenClaw job
4. PDF、citation、reading workspace 均写入该用户目录

### 11.3 CLI / 微信上传 PDF

1. 调用 `app.openclaw.intake_cli`
2. 进入默认 OpenClaw 自动化用户上下文
3. 在 `data/users/<default-openclaw-user>/` 下写入 library、reading、job 和 SQLite

## 12. 服务与排障入口

常用命令：

查看服务状态：

```bash
systemctl --user status exscholar-site.service --no-pager
```

重启服务：

```bash
systemctl --user restart exscholar-site.service
```

查看服务日志：

```bash
journalctl --user -u exscholar-site.service -n 100 --no-pager
```

编译检查：

```bash
<openclaw-python> -m py_compile \
  $(find app -name '*.py' | sort) set_site_password.py
```

## 13. 相关文档

- 项目总览：[README.md](../README.md)
- 用户说明：[README_USER.md](../README_USER.md)
- 开发说明：[README_DEV.md](../README_DEV.md)
- OpenClaw 链路说明：[OPENCLAW_ADDON.md](OPENCLAW_ADDON.md)
- 微信 PDF intake：[WECHAT_PDF_INTAKE.md](WECHAT_PDF_INTAKE.md)
- 搜索 skill：[SKILL.md](../skills/ccf-research/SKILL.md)
