# exScholar 用户使用说明

这份文档面向部署和日常使用 exScholar 的用户，覆盖安装、账号配置、网页使用、CLI 使用和服务管理。

## 1. 项目能做什么

exScholar 目前支持以下工作流：

- 搜索 DBLP 论文并生成网页结果
- 在网页中用自然语言发起 research 搜索
- 上传单篇或多篇 PDF
- 自动识别元数据、去重、建 citation 库
- 自动生成结构化论文分析
- 在阅读页可视化查看当前正在解析的任务
- 在阅读页继续问答和记笔记
- 创建和使用 Reading Group
- 按用户隔离数据目录和阅读库

## 2. 环境要求

- Conda
- Python `3.11`
- Playwright `chromium`
- OpenClaw 配置文件

项目当前统一使用：

```text
/home/ubuntu/miniconda3/envs/openclaw-analytics/bin/python
```

安装：

```bash
conda env create -f environment.yml
conda activate openclaw-analytics
python -m playwright install chromium
```

## 3. 基础配置

在仓库根目录准备 `.env.local`。

建议直接从 [.env.local.example](/home/ubuntu/tools/exScholar/.env.local.example) 复制：

```bash
cp .env.local.example .env.local
```

最常用配置：

- `PUBLIC_SITE_BASE_URL`
- `PUBLIC_SITE_PORT`
- `SITE_SERVER_HOST`
- `OPENCLAW_CONFIG_PATH`
- `OPENCLAW_INGEST_MODEL`
- `OPENCLAW_INGEST_CHECK_MODEL`
- `OPENCLAW_INGEST_FALLBACK_MODEL`
- `OPENCLAW_ANALYTICS_PYTHON`

推荐确认：

```text
OPENCLAW_ANALYTICS_PYTHON=/home/ubuntu/miniconda3/envs/openclaw-analytics/bin/python
```

## 4. 创建登录用户

站点现在使用用户名 + 密码登录，不再是单一全站密码。

创建用户：

```bash
/home/ubuntu/miniconda3/envs/openclaw-analytics/bin/python set_site_password.py \
  --username admin \
  --password 'your-password'
```

说明：

- 用户名会被规范化为小写
- 每个用户都有独立目录：`data/users/<username>/`
- 每个用户都有独立的 `searches/`、`reading/`、`library/`、`expansions/` 和 SQLite 数据库

## 5. 启动和管理服务

推荐使用 user-level systemd 服务。

启动或重启：

```bash
systemctl --user restart exscholar-site.service
```

查看状态：

```bash
systemctl --user status exscholar-site.service --no-pager
```

查看日志：

```bash
journalctl --user -u exscholar-site.service -n 100 --no-pager
```

OpenClaw 网关：

```bash
systemctl --user status openclaw-gateway.service --no-pager
journalctl --user -u openclaw-gateway.service -n 100 --no-pager
```

站点默认入口示例：

```text
http://<your-host>:38128/
```

## 6. 网页使用

主要页面：

- `/`
  搜索时间线，显示当前登录用户自己的搜索结果
- `/keywords`
  当前用户的关键词索引
- `/reading`
  当前用户的阅读库、PDF 上传入口、Reading Group 管理
- `/reading/<paper_id>`
  单篇论文阅读页，可查看分析、继续提问、保存笔记

### 6.1 搜索

你可以通过两种方式搜索：

- 在首页直接使用自然语言 research
- 在命令行运行关键词搜索

Research 搜索结果会进入当前用户自己的 `searches/` 目录。

### 6.2 PDF 上传

`/reading` 页面支持：

- 上传单篇 PDF
- 批量上传多个 PDF
- 自动去重
- 自动匹配已有 citation
- 自动创建阅读工作区

当前网页 PDF 上传统一走 OpenClaw 链路。

从搜索结果页点击“加入深度阅读”时，当前流程是：

- 先在弹窗里打开原文链接
- 手动下载 PDF
- 上传 PDF 后再加入深度阅读

系统当前不再自动抓取论文 PDF。

### 6.3 Reading Group

在 `/reading` 页面可以：

- 创建 Reading Group
- 把文章加入 Group
- 从 Group 中移除文章
- 删除 Group

### 6.4 阅读页

在单篇阅读页中可以：

- 查看论文元数据
- 重新识别元数据
- 重新生成分析
- 查看当前正在解析的任务和进度
- 基于全文问答
- 保存 Notes
- 删除问答历史

## 7. 常用命令

### 7.1 关键词搜索

```bash
./run_search.sh \
  --keywords "physiological notification;biosignal alert" \
  --venues "chi,uist,cscw" \
  --slug "physio-ui" \
  --top 50 \
  --year-from 2020
```

说明：

- `run_search.sh` 已固定走 `openclaw-analytics`
- 同一天相同 `slug` 会自动避让为 `slug-2`、`slug-3`
- 搜索结果会导出 CSV、JSON 和静态网页

### 7.2 本地导入单个 PDF

```bash
/home/ubuntu/miniconda3/envs/openclaw-analytics/bin/python -m app.openclaw.intake_cli \
  --wait --json /absolute/path/to/paper.pdf
```

### 7.3 本地导入多个 PDF

```bash
/home/ubuntu/miniconda3/envs/openclaw-analytics/bin/python -m app.openclaw.intake_cli \
  --wait --json /path/a.pdf /path/b.pdf
```

### 7.4 图片找论文

```bash
/home/ubuntu/miniconda3/envs/openclaw-analytics/bin/python -m app.openclaw.picsearch_cli \
  --wait --json /absolute/path/to/paper-screenshot.png
```

说明：

- OpenClaw 默认用户现在是 `qioyo`
- 非网页登录触发的 OpenClaw intake 和默认 CCF research 搜索会默认写入 `data/users/qioyo/`
- 图片找论文会把结果加入当天 `webreading` timeline，关键词固定为 `picsearch`
- `picsearch` 当前顺序是：图片识别 -> DBLP -> 从前 20 条 web 结果里优先筛官方论文链接 -> DOI fallback

## 8. 数据目录

多用户模式下，运行产物主要位于：

```text
data/users/<username>/
```

每个用户目录中常见内容：

- `searches/`
- `expansions/`
- `library/`
- `reading/`
- `openclaw_jobs/`
- `research_jobs/`
- `citation_library.sqlite3`

## 9. 相关文档

- 项目总览：[README.md](/home/ubuntu/tools/exScholar/README.md)
- 开发说明：[README_DEV.md](/home/ubuntu/tools/exScholar/README_DEV.md)
- OpenClaw 补充说明：[OPENCLAW_ADDON.md](/home/ubuntu/tools/exScholar/docs/OPENCLAW_ADDON.md)
- 微信 PDF intake：[WECHAT_PDF_INTAKE.md](/home/ubuntu/tools/exScholar/docs/WECHAT_PDF_INTAKE.md)
- Skills 总览：[README.md](/home/ubuntu/tools/exScholar/skills/README.md)
- OpenClaw 图片找论文 skill：[picsearch/SKILL.md](/home/ubuntu/tools/exScholar/skills/picsearch/SKILL.md)
