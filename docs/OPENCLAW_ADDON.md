# OpenClaw Addon

这份文档说明 `exScholar` 当前使用的 OpenClaw 论文处理链路。

真实代码位置：

- [app/openclaw/ingest.py](/home/ubuntu/tools/exScholar/app/openclaw/ingest.py)
- [app/openclaw/intake_cli.py](/home/ubuntu/tools/exScholar/app/openclaw/intake_cli.py)
- [app/site](/home/ubuntu/tools/exScholar/app/site)

推荐命令：

```bash
cd /home/ubuntu/tools/exScholar
python -m app.openclaw.intake_cli --wait --json /absolute/path/to/paper.pdf
```

这条链路已经接管：

- `/reading` 页面单篇上传 PDF
- `/reading` 页面批量上传 PDF
- 阅读页元数据识别
- 阅读页开始分析 / 重新分析
- 阅读页问答
- `/reading` 页面一键补全未完成项
- 本地 CLI
- 微信附件触发
