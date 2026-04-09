---
name: picsearch
description: >
  Activate only when the user explicitly says "picsearch". Use it to identify
  papers from uploaded screenshots, including Google Scholar list screenshots,
  and add them to today's Picsearch timeline.
metadata:
  openclaw:
    os: ["linux"]
    requires:
      bins: ["bash"]
---

# exScholar Picsearch Skill

只在用户明确说出 `picsearch` 时触发。
不要用于普通“找论文 / 查论文 / 搜论文”请求。

流程：

1. 用户发送 `picsearch`
2. 回复用户进入收图模式，请继续发图
3. 在用户发送 `开始` 前，只收集图片，不执行
4. 用户发送 `开始` 后，批量运行：

```bash
/home/ubuntu/miniconda3/envs/openclaw-analytics/bin/python -m app.openclaw.picsearch_cli \
  --wait --json /absolute/path/to/paper-a.png /absolute/path/to/paper-b.png
```

处理顺序固定为：

1. 图片识别
2. 如果是单篇论文截图：
   `DBLP` -> `websearch`（前 20 条中优先筛官方论文链接）-> `DOI fallback`
3. 如果是 Google Scholar 论文列表截图：
   先提取页面中的多条论文标题，再逐条执行
   `DBLP` -> `websearch`（前 20 条中优先筛官方论文链接）-> `DOI fallback`

返回时保持简洁：

- 共处理多少张
- 成功多少张，失败多少张
- timeline 链接
- 明确说明这些结果会进入当天 `Picsearch` timeline
- 如果检测到 Google Scholar 页面截图，要明确说明“检测到 Scholar 页面，已批量补链 N 篇”
- 每张成功项的标题、链接、来源
- 每张失败项的原因

如果用户只发了 `picsearch` 但还没发图，提醒先发图。
如果用户发了图但还没发 `开始`，继续等待，不提前执行。
