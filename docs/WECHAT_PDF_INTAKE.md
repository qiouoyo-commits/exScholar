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
