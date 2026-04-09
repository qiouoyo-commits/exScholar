# exScholar

`exScholar` 是一套面向云端长期运行的论文搜索、PDF intake、深度阅读与阅读库管理工具。当前这份仓库默认对应一台云服务器部署：`exscholar-site.service` 提供网站服务，`openclaw-gateway.service` 提供 OpenClaw 对话网关，项目统一运行在 `openclaw-analytics` conda 环境中，并通过 OpenClaw 处理 PDF 元数据抽取、结构化分析和问答链路。

为避免文档和本机路径强耦合，下面统一使用两个占位写法：

- `<repo-root>`：仓库根目录
- `<openclaw-python>`：`openclaw-analytics` 环境中的 Python，可通过 `OPENCLAW_ANALYTICS_PYTHON` 指向

## 主要功能

- 按关键词搜索 DBLP 论文并导出网页、CSV、JSON
- 在网页中发起自然语言 research 搜索，自动生成更贴合学术表达的检索词建议，并在结果阶段做相关性复核与自动标签
- 对“影响因素 / 决定因素 / 预测因素 / 作用机制”这类需求，搜索规划会优先改写成更像论文标题的英文名词短语，而不是解释型短语
- 自然语言 research 结果默认不超过 200 篇；如果第一次召回过少，系统会自动补充建议检索词再重试一轮
- 上传一个或多个 PDF，自动去重、建库、生成阅读工作区
- 从搜索结果页加入深度阅读时，先打开原文链接手动下载 PDF，再上传 PDF
- 在 OpenClaw 对话侧通过 `picsearch` 标准动作提交论文截图图片；除单篇论文截图外，也支持 Google Scholar 页面截图，并会把识别出的多篇论文统一加入当天 `Picsearch` timeline，同时尽量补抓摘要
- 在 OpenClaw 对话侧通过 `textsearch` 标准动作提交一个或多个论文标题，把补链接结果加入当天 `Textsearch` timeline，并尽量补抓摘要
- 基于论文做引用扩展搜索
- 在阅读页中查看结构化分析、继续提问、保存笔记
- 支持多用户数据隔离，每个用户拥有独立的 `searches/`、`reading/`、`library/` 和 SQLite 数据库

## 文档结构

- 用户使用说明：[README_USER.md](/home/ubuntu/tools/exScholar/README_USER.md)
- 开发说明：[README_DEV.md](/home/ubuntu/tools/exScholar/README_DEV.md)
- 架构说明：[ARCHITECTURE.md](/home/ubuntu/tools/exScholar/docs/ARCHITECTURE.md)
- OpenClaw PDF 链路补充：[OPENCLAW_ADDON.md](/home/ubuntu/tools/exScholar/docs/OPENCLAW_ADDON.md)
- 微信 PDF intake 说明：[WECHAT_PDF_INTAKE.md](/home/ubuntu/tools/exScholar/docs/WECHAT_PDF_INTAKE.md)
- Skills 总览：[README.md](/home/ubuntu/tools/exScholar/skills/README.md)
- OpenClaw 图片找论文 skill：[picsearch/SKILL.md](/home/ubuntu/tools/exScholar/skills/picsearch/SKILL.md)
- OpenClaw 文本补链接 skill：[textsearch/SKILL.md](/home/ubuntu/tools/exScholar/skills/textsearch/SKILL.md)

## 快速开始

1. 创建并激活环境

```bash
conda env create -f environment.yml
conda activate openclaw-analytics
python -m playwright install chromium
```

2. 配置 `.env.local`

建议从 [.env.local.example](/home/ubuntu/tools/exScholar/.env.local.example) 复制：

```bash
cp .env.local.example .env.local
```

至少确认以下项目：

- `PUBLIC_SITE_BASE_URL`
- `PUBLIC_SITE_PORT`
- `SITE_SERVER_HOST`
- `OPENCLAW_CONFIG_PATH`
- `OPENCLAW_ANALYTICS_PYTHON`

3. 创建登录账号

```bash
<openclaw-python> <repo-root>/set_site_password.py \
  --username admin \
  --password 'your-password'
```

4. 启动服务

```bash
systemctl --user restart exscholar-site.service
systemctl --user status exscholar-site.service --no-pager
```

默认访问地址示例：

```text
http://<your-host>:38128/
```

## 常用入口

启动站点：

```bash
systemctl --user restart exscholar-site.service
```

运行关键词搜索：

```bash
./run_search.sh \
  --keywords "example keyword" \
  --venues "chi" \
  --slug "example"
```

自然语言 research 链路：

- 先生成学术化检索词建议
- 再生成正式搜索方案
- 如果第一次召回结果过少，会自动补充一轮建议检索词并重试
- DBLP 请求会按更稳的 direct/proxy 路径重试；单次 DBLP 失败只会让当前 `关键词 × venue` 组合回退到 OpenAlex，不会拖累整轮搜索
- 搜索完成后结合标题和摘要做相关性复核
- 为结果补充 `relevance_label`、`relevance_score`、`autotags`
- 搜索阶段会实时显示当前关键词 / venue，以及已累计找到的论文数
- `search.json` 会记录本次是否发生过 DBLP -> OpenAlex fallback，便于排查“结果变少”到底是检索词问题还是数据源波动

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

说明：

- `textsearch` 是当前有效口令；旧的 `titlesearch` 名称已经废弃，不再作为对外入口保留
- `picsearch` 支持单篇论文截图和 Google Scholar 页面截图
- `picsearch`、`textsearch` 都会在补链接后尽量继续抓取摘要，因此比纯补链接稍慢
- 从 `Picsearch` / `Textsearch` 结果加入深度阅读时，系统会优先按论文主题自动生成或复用 Reading Group，而不是直接使用 timeline 名称

同步并重载 OpenClaw skills：

```bash
/home/ubuntu/tools/exScholar/sync_openclaw_skills.sh
systemctl --user restart openclaw-gateway.service
```

## 运行环境

当前项目中的主要入口都已统一到：

```text
<openclaw-python>
```

包括：

- systemd 服务 `exscholar-site.service`
- `run_search.sh`
- 本地 Python 入口脚本 shebang
- research 子进程解释器 `OPENCLAW_ANALYTICS_PYTHON`

## 当前代码结构

- `app/pipeline`：搜索、主爬虫、摘要抓取、导出站点
- `app/site`：网页、HTTP 接口、用户数据、阅读库、任务管理
- `app/openclaw`：PDF intake、元数据抽取、分析、问答、图片找论文
- `app/common`：共享工具
- `skills/`：面向 Codex / OpenClaw 的技能定义

## 服务管理

查看服务状态：

```bash
systemctl --user status exscholar-site.service --no-pager
```

重启服务：

```bash
systemctl --user restart exscholar-site.service
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

## 建议阅读顺序

- 想直接使用：先看 [README_USER.md](/home/ubuntu/tools/exScholar/README_USER.md)
- 想修改代码或继续开发：看 [README_DEV.md](/home/ubuntu/tools/exScholar/README_DEV.md)
