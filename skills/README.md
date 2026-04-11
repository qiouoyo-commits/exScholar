# exScholar Skills

这个目录存放面向 OpenClaw / Codex 的技能定义。

当前已有技能：

- [ccf-research/SKILL.md](ccf-research/SKILL.md)
  - 用途：按研究主题搜索论文，生成搜索结果网页、CSV、JSON
  - 主要触发：普通“找论文 / 查论文 / 搜论文 / 读文献”
  - 当前链路：智能建议检索词 -> 生成 research plan -> 执行搜索 -> 低结果自动补扩 -> 标题/摘要相关性复核 -> autotag
  - 当前规划会对“影响因素 / 决定因素 / 预测因素 / 作用机制”这类中文需求优先改写成更像学术标题的名词短语
- [picsearch/SKILL.md](picsearch/SKILL.md)
  - 用途：从论文截图中识别论文并加入当天 `Picsearch` timeline，也支持 Google Scholar 页面截图批量补链接
  - 主口令：`picsearch`
  - 交互方式：先发 `picsearch` 开启收图模式，发完图片后再回复“开始”
  - 查找顺序：图片识别或 Scholar 页面识别 -> DBLP -> 官方 web 候选筛选 -> DOI fallback
  - 结果补全：补链接后尽量继续抓取摘要
  - 返回格式：汇总成功/失败数量、timeline 链接、逐张结果
- [textsearch/SKILL.md](textsearch/SKILL.md)
  - 用途：根据一个标题或多个标题批量补链接并加入当天 `Textsearch` timeline
  - 主口令：`textsearch`
  - 交互方式：先发 `textsearch` 开启文本收集模式，发完标题后再回复“开始”
  - 查找顺序：标题匹配 -> DBLP -> 官方 web 候选筛选 -> DOI fallback
  - 结果补全：补链接后尽量继续抓取摘要
  - 返回格式：汇总成功/失败数量、timeline 链接、逐条结果

边界约定：

- 普通论文搜索需求走 `ccf-research`
- 图片 / 截图找论文只走 `picsearch`
- 明确按标题批量补链接时走 `textsearch`
- `picsearch` 不应覆盖普通“找论文”请求
- 旧的 `titlesearch` 名称已经废弃，当前统一使用 `textsearch`
- `ccf-research` 的网页端 research 和 OpenClaw skill 现在共用同一条智能搜索链路
- `picsearch` / `textsearch` 的 timeline 名只表示来源；加入深度阅读时会按论文主题自动生成或复用更合适的 Reading Group 名
- 推荐记忆方式：普通搜论文用 `ccf-research` 语义触发；图片找论文用 `picsearch`；文本补链接用 `textsearch`

运行环境：

- Python 统一使用 `<openclaw-python>`
- 非网页登录触发默认写入 `data/users/<default-openclaw-user>/`

同步到 OpenClaw：

```bash
<repo-root>/sync_openclaw_skills.sh
systemctl --user restart openclaw-gateway.service
```

OpenClaw 实际加载的是 `~/.openclaw/skills/`，不是仓库里的 `skills/`。

相关文档：

- 项目总览：[README.md](../README.md)
- 开发说明：[README_DEV.md](../README_DEV.md)
- OpenClaw 链路说明：[OPENCLAW_ADDON.md](../docs/OPENCLAW_ADDON.md)
