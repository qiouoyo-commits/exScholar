# exScholar 用户使用说明

这份文档面向部署和日常使用 exScholar 的用户，覆盖安装、账号配置、网页使用、CLI 使用和服务管理。当前项目默认以云服务器部署为主：网站和 OpenClaw 网关都常驻运行在同一台云主机上。

为避免文档和单台机器路径绑定，下面统一使用两个占位写法：

- `<repo-root>`：仓库根目录
- `<openclaw-python>`：`openclaw-analytics` 环境中的 Python，可通过 `OPENCLAW_ANALYTICS_PYTHON` 指向

## 1. 项目能做什么

exScholar 目前支持以下工作流：

- 搜索 DBLP 论文并生成网页结果
- 在网页中用自然语言发起 research 搜索，并自动生成更贴合学术表达的检索词建议
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
<openclaw-python>
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
OPENCLAW_ANALYTICS_PYTHON=<openclaw-python>
```

## 4. 创建登录用户

站点现在使用用户名 + 密码登录，不再是单一全站密码。

创建用户：

```bash
<openclaw-python> <repo-root>/set_site_password.py \
  --username admin \
  --password 'your-password'
```

说明：

- 用户名会被规范化为小写
- 每个用户都有独立目录：`data/users/<username>/`
- 每个用户都有独立的 `searches/`、`reading/`、`library/`、`expansions/` 和 SQLite 数据库

## 5. 启动和管理服务

推荐使用 user-level systemd 服务。

当前默认服务形态：

- `exscholar-site.service`
  - 提供 exScholar 网站和 API
- `openclaw-gateway.service`
  - 提供 OpenClaw 对话网关
- 两者通常一起运行在同一台云服务器上

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
自然语言 research 当前会先生成建议检索词，再执行搜索；结果页会补充相关性等级和自动标签，方便快速筛选高相关论文。
如果你在 OpenClaw 对话里做普通主题搜论文，这条链也会被 `ccf-research` skill 复用。
对“影响因素 / 决定因素 / 预测因素 / 作用机制”这类中文需求，系统会优先改写成更像论文标题和摘要里常见的英文名词短语，减少“impact analysis / effects assessment”这类解释型检索表达。
为了控制噪声和模型开销，单次自然语言 research 最终默认最多保留 200 篇结果；如果第一次召回少于约 80 篇，系统会自动补充建议检索词再重试一轮。
搜索执行中，页面会实时显示当前正在检索的关键词 / venue，以及当前累计找到的论文数。
如果某个 `关键词 × venue` 组合的 DBLP 请求临时失败，系统只会对当前组合回退到 OpenAlex，不会让整轮搜索都放弃 DBLP；相关 fallback 记录会写入结果目录的 `search.json`。
搜索结果页和 Keywords 页中的“扩展搜索”现在也会走后台 job：

- 点击后会立即进入队列
- 页面会显示当前步骤提示
- 前端会轮询等待结果
- 如果单次请求或整体等待超时，会明确提示你稍后重试或回到时间线查看结果

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
<openclaw-python> -m app.openclaw.intake_cli \
  --wait --json /absolute/path/to/paper.pdf
```

### 7.3 本地导入多个 PDF

```bash
<openclaw-python> -m app.openclaw.intake_cli \
  --wait --json /path/a.pdf /path/b.pdf
```

### 7.4 图片找论文

```bash
<openclaw-python> -m app.openclaw.picsearch_cli \
  --wait --json /absolute/path/to/paper-screenshot.png
```

说明：

- OpenClaw 默认用户现在是 `qioyo`
- 非网页登录触发的 OpenClaw intake 和默认 CCF research 搜索会默认写入 `data/users/qioyo/`
- 图片找论文会把结果加入当天 `Picsearch` timeline
- 文本补链接会把结果加入当天 `Textsearch` timeline
- `picsearch` 除了单篇论文截图，也支持 Google Scholar 页面截图；如果识别到页面中有多篇论文标题，会自动逐条补链接
- `picsearch` 和 `textsearch` 现在都会在补链接后尽量继续抓取摘要，因此返回会比纯补链接稍慢一些
- 从 `Picsearch` / `Textsearch` 结果加入深度阅读时，如果你不手动选 Group，系统会优先按论文主题自动创建或复用一个更合适的 Reading Group

### 7.5 文本补链接

```bash
<openclaw-python> -m app.openclaw.textsearch_cli \
  --wait --json "Paper Title A\nPaper Title B"
```

说明：

- OpenClaw 对话里推荐主口令使用 `textsearch`
- 支持单个标题或多个标题
- 多个标题默认按换行拆分
- 旧的 `titlesearch` 名称已经废弃，当前统一使用 `textsearch`

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
