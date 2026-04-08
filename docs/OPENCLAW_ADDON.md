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

- `/reading` 页面唯一的 PDF 上传入口
- 阅读页元数据识别
- 阅读页开始分析 / 重新分析
- 阅读页问答
- `/reading` 页面一键补全未完成项
- 本地 CLI
- 微信附件触发

现在网页端只保留 `/api/openclaw-intake/upload` 这一个 PDF 上传接口。上传一个或多个 PDF 都走同一条链路，并保留：

- PDF 哈希去重
- citation 匹配
- 重复文献合并
