# exScholar 开发与 Vibecoding 说明

这份文档面向继续开发这个仓库的人，也适合作为 coding model 的协作入口。

## 开发目标

当前仓库已经从“根目录堆脚本”整理成了 `app/` 主结构。后续开发时，最重要的是继续守住这条边界：

- 搜索链路改 `app/pipeline/`
- 站点与 API 改 `app/site/`
- PDF intake、模型调用、论文分析改 `app/openclaw/`
- 通用工具改 `app/common/`
- 根目录只保留文档、环境、启动脚本和少量管理脚本

如果继续按这个结构开发，这个项目适合持续 vibecoding；如果把新逻辑重新写回根目录，项目很快会再次变乱。

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

## 改动入口速查

最常见的问题可以直接按下面找文件：

- 搜索参数、抓取策略、导出结果：
  [app/pipeline/search.py](/home/ubuntu/tools/exScholar/app/pipeline/search.py)
  [app/pipeline/main.py](/home/ubuntu/tools/exScholar/app/pipeline/main.py)
  [app/pipeline/driver.py](/home/ubuntu/tools/exScholar/app/pipeline/driver.py)
- 会场规则、特殊过滤：
  [app/pipeline/config/venue.py](/home/ubuntu/tools/exScholar/app/pipeline/config/venue.py)
  [app/pipeline/config/special_rules.py](/home/ubuntu/tools/exScholar/app/pipeline/config/special_rules.py)
- PDF intake、模型提示词、元数据抽取、分析和问答：
  [app/openclaw/ingest.py](/home/ubuntu/tools/exScholar/app/openclaw/ingest.py)
  [app/openclaw/intake_cli.py](/home/ubuntu/tools/exScholar/app/openclaw/intake_cli.py)
- citation 库、reading workspace、批处理任务：
  [app/site/core/citations.py](/home/ubuntu/tools/exScholar/app/site/core/citations.py)
  [app/site/core/reading.py](/home/ubuntu/tools/exScholar/app/site/core/reading.py)
  [app/site/core/jobs.py](/home/ubuntu/tools/exScholar/app/site/core/jobs.py)
- 外部引用扩展：
  [app/site/core/references.py](/home/ubuntu/tools/exScholar/app/site/core/references.py)
- 站点 API：
  [app/site/http/handler.py](/home/ubuntu/tools/exScholar/app/site/http/handler.py)
- 网页渲染：
  [app/site/ui/pages.py](/home/ubuntu/tools/exScholar/app/site/ui/pages.py)
- 公共工具：
  [app/common/utils.py](/home/ubuntu/tools/exScholar/app/common/utils.py)

## 当前 OpenClaw 链路

网页端和微信端统一使用 `app.openclaw` 处理论文 PDF。

已接管的入口：

- `/reading` 页面唯一的 PDF 上传入口
- 阅读页手动识别元数据
- 阅读页开始分析 / 重新分析
- 阅读页问答
- `/reading` 页面一键补全未完成项
- 本地 CLI
- 微信附件触发

当前 `/reading` 不再保留独立的“普通上传并生成阅读页”接口，网页端只走 `/api/openclaw-intake/upload`。底层去重和合并逻辑在 [app/site/core/jobs.py](/home/ubuntu/tools/exScholar/app/site/core/jobs.py) 的 `start_openclaw_intake_job(...)`。

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

## 当前搜索并发模型

网页 Research 和 OpenClaw `ccf-research` skill 现在共享同一套并发槽位，不再是两条互相绕开的执行链路。

- 默认并发上限：`MAX_CONCURRENT_RESEARCH_JOBS=2`
- 控制入口：
  [app/pipeline/search.py](/home/ubuntu/tools/exScholar/app/pipeline/search.py)
- 网页 job 状态层：
  [app/site/core/research_jobs.py](/home/ubuntu/tools/exScholar/app/site/core/research_jobs.py)
- skill 说明：
  [skills/ccf-research/SKILL.md](/home/ubuntu/tools/exScholar/skills/ccf-research/SKILL.md)

实现方式是把“真实限流”下沉到 `run_topic_search(...)`，通过文件锁做跨进程槽位控制，所以：

- 网页按钮触发的 research 会受限流
- OpenClaw skill 直接调用 `run_search.sh` 也会受限流
- 同日同 `slug` 的搜索会自动避让成 `slug-2`、`slug-3`，避免覆盖已有目录

## 开发边界

建议默认遵守这些约定：

- 新业务逻辑不要回写到根目录
- `data/` 只放运行产物，不放源码
- 不要把运行产物写到 `app/data/`
- 站点数据结构变更时尽量兼容已有 SQLite、`data/library/`、`data/reading/`
- 只是改网页文案或样式时，优先只改 [pages.py](/home/ubuntu/tools/exScholar/app/site/ui/pages.py)
- 只是改模型策略时，优先只改 [ingest.py](/home/ubuntu/tools/exScholar/app/openclaw/ingest.py)
- 只是改 API 行为时，优先改 [handler.py](/home/ubuntu/tools/exScholar/app/site/http/handler.py) 和 `site/core`

## 环境

推荐环境：

- Python `3.11+`
- Conda 环境：`openclaw-analytics`

安装：

```bash
conda env create -f environment.yml
conda activate openclaw-analytics
python -m playwright install chromium
```

## 最小验证清单

改动后优先做最小验证，不要默认全量跑：

1. 编译检查

```bash
oc-conda-run -- python -m py_compile $(find app -name '*.py' | sort) set_site_password.py
```

2. 改了站点就起服务

```bash
oc-conda-run -- python -m app.site.http.handler
```

3. 改了 PDF 解析链路就跑一个 intake

```bash
cd /home/ubuntu/tools/exScholar
python -m app.openclaw.intake_cli --wait --json /absolute/path/to/paper.pdf
```

4. 改了搜索链路就跑一个小搜索

```bash
oc-conda-run -- python -m app.pipeline.search \
  --keywords "test keyword" \
  --venues "chi" \
  --slug "smoke-test" \
  --top 5
```

## 高频命令

启动站点：

```bash
oc-conda-run -- python -m app.site.http.handler
```

运行搜索：

```bash
oc-conda-run -- python -m app.pipeline.search \
  --keywords "example keyword" \
  --venues "chi" \
  --slug "example"
```

运行主爬虫：

```bash
oc-conda-run -- python -m app.pipeline.main -ccf a -c conf -m 20 -p 10
```

本地导入 PDF：

```bash
cd /home/ubuntu/tools/exScholar
python -m app.openclaw.intake_cli --wait --json /absolute/path/to/paper.pdf
```

## 相关文档

- [README_USER.md](/home/ubuntu/tools/exScholar/README_USER.md)
- [OPENCLAW_ADDON.md](/home/ubuntu/tools/exScholar/docs/OPENCLAW_ADDON.md)
- [WECHAT_PDF_INTAKE.md](/home/ubuntu/tools/exScholar/docs/WECHAT_PDF_INTAKE.md)
