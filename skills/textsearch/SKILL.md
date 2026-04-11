---
name: textsearch
description: >
  Activate only when the user explicitly says "textsearch". Use it to resolve
  one paper title or multiple paper titles and add the resulting papers to
  today's Textsearch timeline.
metadata:
  openclaw:
    os: ["linux"]
    requires:
      bins: ["bash"]
---

# exScholar Textsearch Skill

只在用户明确说出 `textsearch` 时触发。
不要用于普通“找论文 / 查论文 / 搜论文”请求。

支持两种输入：

1. 一个论文标题
2. 多个论文标题

流程：

1. 用户发送 `textsearch`
2. 回复用户进入文本收集模式，请继续发送标题文本
3. 在用户发送 `开始` 前，只收集文本，不执行
4. 用户发送 `开始` 后，批量运行：

```bash
<openclaw-python> -m app.openclaw.textsearch_cli \
  --wait --json "Paper Title A\nPaper Title B"
```

输入规则：

- 一次消息里可以有一个或多个标题
- 多标题默认按换行拆分
- 如果用户用了 `1.`、`2.`、`-`、`*` 这类列表前缀，执行前会自动去掉

处理顺序固定为：

1. 标题匹配
2. `DBLP`
3. `websearch`（从前 20 条结果中优先筛官方论文链接）
4. `DOI fallback`

返回时保持简洁：

- 共处理多少条
- 成功多少条，失败多少条
- timeline 链接
- 明确说明这些结果会进入当天 `Textsearch` timeline
- 每条成功项的标题、链接、来源
- 每条失败项的原因

如果用户只发了 `textsearch` 但还没发文本，提醒先发标题。
如果用户发了文本但还没发 `开始`，继续等待，不提前执行。
