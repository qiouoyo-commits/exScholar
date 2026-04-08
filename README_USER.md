# exScholar 用户使用说明

`exScholar` 可以用来搜索论文、扩展引用、上传 PDF、生成论文解析，并在网页中继续问答和整理阅读笔记。

## 你可以做什么

- 按关键词搜索 DBLP 论文
- 浏览摘要和导出结果
- 基于已有论文继续做参考文献扩展
- 上传一个或多个 PDF
- 自动识别元数据并创建阅读工作区
- 自动生成论文结构化分析
- 在阅读页继续提问并保存问答历史

## 环境要求

- Python `3.11+`
- 推荐 Conda 环境：`openclaw-analytics`
- Playwright `chromium`

安装方式：

```bash
conda env create -f environment.yml
conda activate openclaw-analytics
python -m playwright install chromium
```

如果通过 OpenClaw 的 conda 包装器执行：

```bash
oc-conda-run -- python -m playwright install chromium
```

## 基础配置

复制 `.env.local.example` 为 `.env.local` 后填写。

最常用的配置包括：

- 站点地址与端口：
  `PUBLIC_SITE_BASE_URL`
  `PUBLIC_SITE_PORT`
  `SITE_SERVER_HOST`
- 站点登录：
  `SITE_PASSWORD_SALT`
  `SITE_PASSWORD_HASH`
  `SITE_SESSION_SECRET`
- OpenClaw PDF 处理：
  `OPENCLAW_INGEST_MODEL`
  `OPENCLAW_INGEST_CHECK_MODEL`
  `OPENCLAW_INGEST_FALLBACK_MODEL`
  `OPENCLAW_CONFIG_PATH`

默认 OpenClaw 模型链路是：

- 主读取：`joybuilder-plan/DeepSeek-V3.2`
- 检查：`joybuilder-plan/GLM-5`
- 回退：`joybuilder-plan/Kimi-K2.5`

## 常用命令

启动站点：

```bash
oc-conda-run -- python -m app.site.http.handler
```

设置站点密码：

```bash
oc-conda-run -- python set_site_password.py --password 'your-password'
```

跑一个关键词搜索：

```bash
./run_search.sh \
  --keywords "physiological notification;biosignal alert" \
  --venues "chi,uist,cscw" \
  --slug "physio-ui" \
  --top 50 \
  --year-from 2020
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

## 网页使用

主要页面：

- `/`
  搜索时间线
- `/keywords`
  所有关键词索引
- `/reading`
  阅读库，支持通过 OpenClaw 上传一个或多个 PDF、批量处理、筛选和分组
- `/reading/<paper_id>`
  单篇论文阅读页，可识别元数据、重新分析和问答

## OpenClaw PDF 链路

当前这些入口都统一走 `app.openclaw`：

- `/reading` 页面唯一的 PDF 上传入口
- 阅读页手动识别元数据
- 阅读页开始分析 / 重新分析
- 阅读页问答
- `/reading` 一键补全未完成项
- 本地 CLI
- 微信附件触发

这条链路会自动：

- 按 PDF 哈希去重
- 尝试匹配已有文献
- 将识别出的重复文献合并到现有 citation

## 搜索并发

网页 Research 和 OpenClaw `ccf-research` skill 共用同一套搜索并发控制：

- 默认最多同时运行 `2` 个搜索任务
- 超出的任务会自动排队
- 同一天重复使用相同 `slug` 时，会自动生成 `-2`、`-3` 后缀，避免覆盖已有搜索结果

## 数据目录

运行产物主要位于 [data](/home/ubuntu/tools/exScholar/data)：

- [searches](/home/ubuntu/tools/exScholar/data/searches)
- [expansions](/home/ubuntu/tools/exScholar/data/expansions)
- [library](/home/ubuntu/tools/exScholar/data/library)
- [reading](/home/ubuntu/tools/exScholar/data/reading)
- [citation_library.sqlite3](/home/ubuntu/tools/exScholar/data/citation_library.sqlite3)

## 相关文档

- [OPENCLAW_ADDON.md](/home/ubuntu/tools/exScholar/docs/OPENCLAW_ADDON.md)
- [WECHAT_PDF_INTAKE.md](/home/ubuntu/tools/exScholar/docs/WECHAT_PDF_INTAKE.md)
