# OpenClaw 链路说明

这份文档只说明 OpenClaw 侧的专项链路和边界。当前默认部署方式是云服务器常驻运行：`openclaw-gateway.service` 与 `exscholar-site.service` 一起提供对话网关和网页服务。安装、账号、站点使用方式以 [README_USER.md](/home/ubuntu/tools/exScholar/README_USER.md) 为准，避免重复维护两份用户手册。

## 1. 相关代码位置

- [ingest.py](/home/ubuntu/tools/exScholar/app/openclaw/ingest.py)
- [intake_cli.py](/home/ubuntu/tools/exScholar/app/openclaw/intake_cli.py)
- [paper_lookup.py](/home/ubuntu/tools/exScholar/app/openclaw/paper_lookup.py)
- [picsearch_cli.py](/home/ubuntu/tools/exScholar/app/openclaw/picsearch_cli.py)
- [textsearch_cli.py](/home/ubuntu/tools/exScholar/app/openclaw/textsearch_cli.py)
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
- 支持 Google Scholar 页面截图；如果识别到页面中列了多篇论文，会自动逐条补链接
- 优先尝试 DBLP 匹配
- DBLP 失败时，在前 20 条 web 结果中优先筛选 ACM、ACL Anthology、arXiv、CVF、PMLR、IEEE、Springer、ScienceDirect、Nature 等官方链接
- 当 DBLP 和可信 web 候选都不足时，再退回 DOI fallback
- 将结果统一写入当天 `Picsearch` timeline，并尽量继续抓取摘要
- timeline 名仅表示来源；后续加入深度阅读时会按论文主题自动生成或复用更合适的 Reading Group
- OpenClaw 对话侧可通过 [picsearch/SKILL.md](/home/ubuntu/tools/exScholar/skills/picsearch/SKILL.md) 作为标准动作调用
- 后台模型调用会经过统一节流，因此 Scholar 截图批量补链或摘要补抓时会更稳，但速度会比最早版本更保守

文本补链接：

```bash
/home/ubuntu/miniconda3/envs/openclaw-analytics/bin/python -m app.openclaw.textsearch_cli \
  --wait --json "Paper Title A\nPaper Title B"
```

这条链路会：

- 接收一个或多个论文标题
- 逐条执行 `DBLP -> 官方 web 候选筛选 -> DOI fallback`
- 将结果写入当天 `Textsearch` timeline
- 尽量继续抓取摘要
- 同样受后台统一模型节流控制，避免在批量补链接时过快撞到 provider RPM

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

## 7. Research 链路与模型节流

虽然自然语言 research 的检索执行最终由 [search.py](/home/ubuntu/tools/exScholar/app/pipeline/search.py) 完成，但其前后两端仍属于 OpenClaw 模型链路：

- 前置阶段会先生成更贴合学术表达的检索词建议
- 再基于建议词生成正式 research plan
- 搜索完成后，会结合标题和摘要做相关性复核与 autotag

这些模型调用当前统一由 [ingest.py](/home/ubuntu/tools/exScholar/app/openclaw/ingest.py) 管理，并共享同一套后台节流策略：

- 全局模型请求并发闸门
- provider 级最小请求间隔
- research 结果复核按较大 chunk 分批执行
- 每批之间加轻量节流，降低 provider RPM 峰值

当前这样做的目的有两点：

- 减少 `picsearch`、`textsearch`、PDF metadata/analysis 与 natural-language research 互相挤占模型额度
- 降低 OpenClaw provider 的 `rpmlimit` / `rate_limit_error` 概率

此外，当前项目里与模型推理直接相关的链路默认不走系统 HTTP 代理：

- `openclaw-gateway.service` 和 `exscholar-site.service` 的 systemd 环境会显式清空 `HTTP_PROXY` / `HTTPS_PROXY` / `ALL_PROXY`
- [ingest.py](/home/ubuntu/tools/exScholar/app/openclaw/ingest.py) 内部的模型 HTTP 客户端使用 `requests.Session(trust_env=False)`

因此，research 规划、结果复核、PDF metadata/analysis、`picsearch` 图片识别这几条模型链，在服务内和直接调用 `app.openclaw.ingest` 时都会优先直连上游 provider，不会自动继承 shell 里的代理变量。

为了便于排查，这几条链路现在也会显式暴露一个轻量诊断标记：

- research 规划结果中的 `diagnostics.model_http`
- PDF metadata / analysis 任务步骤中的 `model_http=...`
- `picsearch` / `textsearch` CLI 输出中的 `model_http=...`

当前正常值应为：

```text
no_proxy
```

如果后面再遇到 provider 报错，这个标记可以帮助快速判断：当前失败是否仍发生在“明确不走代理”的模型链上。

在自然语言 research 中，如果某次搜索第一次召回结果过少，后台还会自动补充一轮建议检索词再重试。这条补扩逻辑位于：

- [research_jobs.py](/home/ubuntu/tools/exScholar/app/site/core/research_jobs.py)

对于底层论文召回，当前策略是：

- 每个 `关键词 × venue` 组合优先尝试 DBLP
- 单次 DBLP 请求失败时，仅当前组合回退到 OpenAlex
- 不会因为某一次 DBLP 波动，让整轮搜索后续都放弃 DBLP
- 发生过回退的组合会记录在结果目录的 `search.json.fallback_events`

## 8. 相关文档

- 项目总览：[README.md](/home/ubuntu/tools/exScholar/README.md)
- 用户说明：[README_USER.md](/home/ubuntu/tools/exScholar/README_USER.md)
- 开发说明：[README_DEV.md](/home/ubuntu/tools/exScholar/README_DEV.md)
- 微信 PDF intake：[WECHAT_PDF_INTAKE.md](/home/ubuntu/tools/exScholar/docs/WECHAT_PDF_INTAKE.md)
- Skills 总览：[README.md](/home/ubuntu/tools/exScholar/skills/README.md)
- OpenClaw 图片找论文 skill：[picsearch/SKILL.md](/home/ubuntu/tools/exScholar/skills/picsearch/SKILL.md)
