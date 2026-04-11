# 微信 PDF Intake 说明

这份文档说明微信或外部自动化场景下，如何把 PDF 送入 exScholar 当前的 OpenClaw intake 链路。

## 1. 相关代码位置

- [ingest.py](../app/openclaw/ingest.py)
- [intake_cli.py](../app/openclaw/intake_cli.py)
- [jobs.py](../app/site/core/jobs.py)

## 2. 当前推荐调用方式

如果外部系统可以直接在仓库目录执行命令，推荐直接调用：

```bash
<openclaw-python> -m app.openclaw.intake_cli \
  --wait --json /absolute/path/to/file.pdf
```

多个 PDF：

```bash
<openclaw-python> -m app.openclaw.intake_cli \
  --wait --json /path/a.pdf /path/b.pdf
```

如果你的自动化环境必须通过仓库执行器包装，也应保持内部 Python 指向 `openclaw-analytics`。

## 3. 实际处理流程

当前流程大致如下：

1. 读取 PDF 文件
2. 计算哈希并判断是否重复
3. 匹配已有 citation，必要时合并
4. 创建或刷新 reading workspace
5. 提取元数据
6. 生成结构化分析
7. 写回用户目录、SQLite 和网页可访问内容

## 4. 当前写入位置

项目现在是多用户模式，不再统一写入共享的 `data/library` 或 `data/reading`。

当前微信 / 外部自动化这类非网页登录触发的默认写入位置是：

```text
data/users/<default-openclaw-user>/
```

常见目标包括：

- `data/users/<default-openclaw-user>/library/`
- `data/users/<default-openclaw-user>/reading/`
- `data/users/<default-openclaw-user>/openclaw_jobs/`
- `data/users/<default-openclaw-user>/citation_library.sqlite3`

如果后续要支持按来源切换用户，需要在调用前显式设置用户上下文。

## 5. 当前行为说明

- `/reading` 页面上传和微信附件现在共用同一套 OpenClaw PDF intake 主链路
- 单个 PDF 和多个 PDF 使用同一条处理逻辑
- 如果识别到与已有记录相同，会自动复用或合并到现有 citation
- CLI 和相关入口当前统一运行在 `openclaw-analytics` 环境

## 6. 相关文档

- 项目总览：[README.md](../README.md)
- 用户说明：[README_USER.md](../README_USER.md)
- 开发说明：[README_DEV.md](../README_DEV.md)
- OpenClaw 链路说明：[OPENCLAW_ADDON.md](OPENCLAW_ADDON.md)
