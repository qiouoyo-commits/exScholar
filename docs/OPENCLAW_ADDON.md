# OpenClaw 链路说明

这份文档说明 exScholar 当前使用的 OpenClaw PDF 处理链路，以及它和网页、CLI、阅读页之间的关系。

## 1. 相关代码位置

- [ingest.py](/home/ubuntu/tools/exScholar/app/openclaw/ingest.py)
- [intake_cli.py](/home/ubuntu/tools/exScholar/app/openclaw/intake_cli.py)
- [paper_lookup.py](/home/ubuntu/tools/exScholar/app/openclaw/paper_lookup.py)
- [picsearch_cli.py](/home/ubuntu/tools/exScholar/app/openclaw/picsearch_cli.py)
- [jobs.py](/home/ubuntu/tools/exScholar/app/site/core/jobs.py)
- [handler.py](/home/ubuntu/tools/exScholar/app/site/http/handler.py)
- [reading.py](/home/ubuntu/tools/exScholar/app/site/core/reading.py)

## 2. 当前链路覆盖范围

OpenClaw 当前统一接管以下入口：

- `/reading` 页面 PDF 上传
- 阅读页元数据识别
- 阅读页重新分析
- 阅读页问答相关全文准备
- 本地 CLI
- 微信附件触发
- 图片论文识别入口

当前网页端只保留一个 PDF 上传接口：

```text
/api/openclaw-intake/upload
```

单篇和多篇 PDF 都走同一条链路。

图片找论文能力不在网页中暴露，当前只保留给 OpenClaw / CLI 侧使用：

```text
/api/openclaw-image-intake/upload
```

## 3. 核心行为

OpenClaw intake 链路会自动完成：

- PDF 哈希去重
- citation 匹配
- 重复文献合并
- reading workspace 创建或刷新
- 元数据抽取
- 结构化分析生成

## 4. 本地 CLI 用法

项目当前统一使用 `openclaw-analytics` 环境。

单个 PDF：

```bash
/home/ubuntu/miniconda3/envs/openclaw-analytics/bin/python -m app.openclaw.intake_cli \
  --wait --json /absolute/path/to/paper.pdf
```

多个 PDF：

```bash
/home/ubuntu/miniconda3/envs/openclaw-analytics/bin/python -m app.openclaw.intake_cli \
  --wait --json /path/a.pdf /path/b.pdf
```

图片论文识别：

```bash
/home/ubuntu/miniconda3/envs/openclaw-analytics/bin/python -m app.openclaw.picsearch_cli \
  --wait --json /absolute/path/to/paper-screenshot.png
```

这条链路会：

- 先让 OpenClaw 用 `joybuilder-plan/Kimi-K2.5` 识别图片里的论文标题 / DOI / 作者等线索
- 支持一次提交一张或多张图片，进入同一个后台队列
- 优先尝试 DBLP 匹配
- DBLP 失败时，在前 20 条 web 结果中优先筛选 ACM、ACL Anthology、arXiv、CVF、PMLR、IEEE、Springer、ScienceDirect、Nature 等官方链接
- 当 DBLP 和可信 web 候选都不足时，再退回 DOI fallback
- 将结果统一写入当天 `webreading` timeline
- 统一使用关键词 `picsearch`
- OpenClaw 对话侧可通过 [picsearch/SKILL.md](/home/ubuntu/tools/exScholar/skills/picsearch/SKILL.md) 作为标准动作调用

## 5. 多用户模式下的数据写入

exScholar 当前是多用户模式。

网页登录场景下：

- PDF 上传会写入当前登录用户自己的数据目录

非网页登录触发的 OpenClaw 默认入口当前会写入：

```text
data/users/qioyo/
```

常见输出位置：

- `data/users/<username>/library/`
- `data/users/<username>/reading/`
- `data/users/<username>/openclaw_jobs/`
- `data/users/<username>/citation_library.sqlite3`

## 6. 模型与环境

OpenClaw 相关入口当前统一运行在：

```text
/home/ubuntu/miniconda3/envs/openclaw-analytics/bin/python
```

常见配置来源：

- `OPENCLAW_INGEST_MODEL`
- `OPENCLAW_INGEST_CHECK_MODEL`
- `OPENCLAW_INGEST_FALLBACK_MODEL`
- `OPENCLAW_CONFIG_PATH`
- `OPENCLAW_ANALYTICS_PYTHON`

## 7. 相关文档

- 项目总览：[README.md](/home/ubuntu/tools/exScholar/README.md)
- 用户说明：[README_USER.md](/home/ubuntu/tools/exScholar/README_USER.md)
- 开发说明：[README_DEV.md](/home/ubuntu/tools/exScholar/README_DEV.md)
- 微信 PDF intake：[WECHAT_PDF_INTAKE.md](/home/ubuntu/tools/exScholar/docs/WECHAT_PDF_INTAKE.md)
- Skills 总览：[README.md](/home/ubuntu/tools/exScholar/skills/README.md)
- OpenClaw 图片找论文 skill：[picsearch/SKILL.md](/home/ubuntu/tools/exScholar/skills/picsearch/SKILL.md)
