# exScholar Skills

这个目录存放面向 OpenClaw / Codex 的技能定义。

当前已有技能：

- [ccf-research/SKILL.md](/home/ubuntu/tools/exScholar/skills/ccf-research/SKILL.md)
  - 用途：按研究主题搜索论文，生成搜索结果网页、CSV、JSON
  - 典型触发：普通“找论文 / 查论文 / 搜论文 / 读文献”
- [picsearch/SKILL.md](/home/ubuntu/tools/exScholar/skills/picsearch/SKILL.md)
  - 用途：从论文截图中识别论文并加入当天 `webreading` timeline
  - 典型触发：用户明确说出魔法词 `picsearch`
  - 交互方式：先发 `picsearch` 开启收图模式，发完图片后再回复“开始”
  - 查找顺序：图片识别 -> DBLP -> 官方 web 候选筛选 -> DOI fallback
  - 返回格式：汇总成功/失败数量、timeline 链接、逐张结果

边界约定：

- 普通论文搜索需求走 `ccf-research`
- 图片 / 截图找论文只走 `picsearch`
- `picsearch` 不应覆盖普通“找论文”请求

运行环境：

- Python 统一使用 `/home/ubuntu/miniconda3/envs/openclaw-analytics/bin/python`
- 非网页登录触发默认写入 `data/users/qioyo/`

同步到 OpenClaw：

```bash
/home/ubuntu/tools/exScholar/sync_openclaw_skills.sh
systemctl --user restart openclaw-gateway.service
```

OpenClaw 实际加载的是 `~/.openclaw/skills/`，不是仓库里的 `skills/`。

相关文档：

- 项目总览：[README.md](/home/ubuntu/tools/exScholar/README.md)
- 开发说明：[README_DEV.md](/home/ubuntu/tools/exScholar/README_DEV.md)
- OpenClaw 链路说明：[OPENCLAW_ADDON.md](/home/ubuntu/tools/exScholar/docs/OPENCLAW_ADDON.md)
