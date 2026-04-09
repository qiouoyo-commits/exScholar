# exScholar

`exScholar` 是一套面向本地部署的论文搜索、PDF intake、深度阅读与阅读库管理工具。项目当前统一运行在 `openclaw-analytics` conda 环境中，并通过 OpenClaw 处理 PDF 元数据抽取、结构化分析和问答链路。

## 主要功能

- 按关键词搜索 DBLP 论文并导出网页、CSV、JSON
- 在网页中发起自然语言 research 搜索
- 上传一个或多个 PDF，自动去重、建库、生成阅读工作区
- 从搜索结果页加入深度阅读时，先打开原文链接手动下载 PDF，再上传 PDF
- 在 OpenClaw 对话侧通过 `picsearch` 标准动作提交论文截图图片，按“图片识别 -> DBLP -> 官方 web 结果筛选 -> DOI fallback”定位论文并加入当天 `webreading` timeline，关键词统一为 `picsearch`
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
/home/ubuntu/miniconda3/envs/openclaw-analytics/bin/python set_site_password.py \
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

本地导入 PDF：

```bash
/home/ubuntu/miniconda3/envs/openclaw-analytics/bin/python -m app.openclaw.intake_cli \
  --wait --json /absolute/path/to/paper.pdf
```

图片找论文：

```bash
/home/ubuntu/miniconda3/envs/openclaw-analytics/bin/python -m app.openclaw.picsearch_cli \
  --wait --json /absolute/path/to/paper-screenshot.png
```

同步并重载 OpenClaw skills：

```bash
/home/ubuntu/tools/exScholar/sync_openclaw_skills.sh
systemctl --user restart openclaw-gateway.service
```

## 运行环境

当前项目中的主要入口都已统一到：

```text
/home/ubuntu/miniconda3/envs/openclaw-analytics/bin/python
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
