# OpenClaw 微信 PDF 导入说明

当前真实代码已经迁到：

- [app/openclaw/ingest.py](/home/ubuntu/tools/exScholar/app/openclaw/ingest.py)
- [app/openclaw/intake_cli.py](/home/ubuntu/tools/exScholar/app/openclaw/intake_cli.py)
- [app/site](/home/ubuntu/tools/exScholar/app/site)

推荐调用方式：

```bash
oc-repo-exec --repo /home/ubuntu/tools/exScholar -- python -m app.openclaw.intake_cli --wait --json /absolute/path/to/file.pdf
```

多个 PDF：

```bash
oc-repo-exec --repo /home/ubuntu/tools/exScholar -- python -m app.openclaw.intake_cli --wait --json /path/a.pdf /path/b.pdf
```

主要流程：

1. PDF 入库与去重
2. citation 匹配与合并
3. reading workspace 创建或刷新
4. OpenClaw 模型提取元数据
5. OpenClaw 模型生成结构化分析
6. 写回 `data/library`、`data/reading`、SQLite 和网页

说明：

- `/reading` 页面和微信附件现在共用同一套 OpenClaw PDF intake 主链路
- 单个 PDF 和多个 PDF 都使用同一个上传接口
- 如果识别到与已有记录相同，会自动复用或合并到现有 citation
