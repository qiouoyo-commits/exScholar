# exScholar

`exScholar` 是一套本地优先的论文搜索、扩展检索与深度阅读工作台。当前代码和文档已经统一到 `app/` 主结构，并默认通过 OpenClaw 接管 PDF 解析链路。

仓库说明分成两份：

- 用户使用版：[README_USER.md](/home/ubuntu/tools/exScholar/README_USER.md)
- 开发与 Vibecoding 版：[README_DEV.md](/home/ubuntu/tools/exScholar/README_DEV.md)

如果你只是想启动网页、上传 PDF、做 search 或 reading，请先看用户版。  
如果你准备继续开发、让 coding model 接手、修改 OpenClaw 链路、改网页或改搜索逻辑，请看开发版。

## 当前状态

- `/reading` 页面现在只保留一个 OpenClaw PDF 上传入口，单篇和多篇 PDF 都走同一条 `app.openclaw` 链路
- 上传后的 PDF 会自动做哈希去重，并尝试匹配或合并到已有 citation
- 网页 research 和 OpenClaw `ccf-research` skill 现在共用同一套搜索并发槽位
- 默认最多同时运行 `2` 个搜索任务，超出的任务会排队

## 当前代码结构

- `app/pipeline`：关键词搜索、主爬虫、摘要抓取、导出静态站点
- `app/site`：阅读站点、SQLite 文献库、阅读工作区、HTTP 接口
- `app/openclaw`：PDF intake、元数据提取、论文结构化分析、问答链路
- `app/common`：共享工具

补充文档：

- [OPENCLAW_ADDON.md](/home/ubuntu/tools/exScholar/docs/OPENCLAW_ADDON.md)
- [WECHAT_PDF_INTAKE.md](/home/ubuntu/tools/exScholar/docs/WECHAT_PDF_INTAKE.md)

## 高频入口

启动站点：

```bash
oc-conda-run -- python -m app.site.http.handler
```

本地导入 PDF：

```bash
cd /home/ubuntu/tools/exScholar
python -m app.openclaw.intake_cli --wait --json /absolute/path/to/paper.pdf
```

运行搜索：

```bash
./run_search.sh --keywords "example keyword" --venues "chi" --slug "example"
```
