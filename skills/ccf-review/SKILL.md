---
name: ccf-review
description: >
  ALWAYS activate this skill when the user asks to 解读论文、写综述、总结某个研究方向、对现有论文做综述分析,
  especially when the request should be grounded in the local exScholar dataset rather than a fresh search.
metadata:
  openclaw:
    os: ["linux"]
    requires:
      bins: ["bash", "python3", "oc-conda-run"]
---

# exScholar Review Skill

当用户要求“解读论文 / 写综述 / 总结某个方向”时：

1. 读取当前项目里已有的 keywords
2. 根据用户问题自动匹配最相关的关键词
3. 导出这些关键词下的论文与摘要
4. 生成综述
5. 把引用日志和综述文件保存到 `data/review_logs/`

---

## 运行入口

统一使用：

```bash
oc-conda-run -- python /home/ubuntu/tools/exScholar/keyword_review.py --query "用户问题" --slug "review-slug"
```

可选参数：

- `--top-keywords 5`
- `--max-papers 40`
- `--keywords "stress UI;emotion interface"`  ← 当你需要人工指定关键词时

只查看当前有哪些关键词时：

```bash
oc-conda-run -- python /home/ubuntu/tools/exScholar/keyword_review.py --list-only
```

---

## 产物目录

每次综述任务保存到：

```text
/home/ubuntu/tools/exScholar/data/review_logs/YYYY-MM-DD_<slug>/
```

默认包含：

- `request.json`：用户问题、匹配到的关键词、筛选参数
- `papers.json`：关键词下导出的论文与摘要
- `citations.json`：引用日志，供综述引用
- `review.md`：综述正文

---

## 你的工作流

### Phase 1：读取关键词并匹配问题

先运行 `keyword_review.py`。

如果脚本已经自动选出了关键词，先向用户简短反馈：

```text
我先用现有关键词做了匹配，当前最相关的是：
1. stress UI
2. emotion interface

我会基于这些关键词下的论文和摘要撰写综述。
```

如果自动匹配明显不可靠，再人工指定 `--keywords` 重跑一次。

### Phase 2：读取论文与摘要

打开本次输出目录中的：

- `request.json`
- `papers.json`
- `citations.json`

只基于这些文件写综述，不要凭空补文献。

### Phase 3：撰写综述

默认输出中文综述，结构建议：

1. 问题概括
2. 研究主题与主线
3. 代表性论文与主要发现
4. 方法趋势 / 系统趋势
5. 局限与空白
6. 结论

要求：

- 必须引用 `citations.json` 中的论文
- 使用 `[1] [2] [3]` 这种编号引用
- 引用编号顺序与 `citations.json` 里的 `id` 一致
- 不要引用日志之外的论文

### Phase 4：保存综述

把最终综述写回：

```bash
/home/ubuntu/tools/exScholar/data/review_logs/YYYY-MM-DD_<slug>/review.md
```

然后再回复用户：

1. 综述正文摘要
2. 实际使用的关键词
3. 综述文件路径
4. 引用日志路径

---

## 注意事项

- 这个 skill 不是 fresh search skill，它优先复用当前项目中已有的 keywords 和论文数据
- 若用户问题对应的关键词很少或没有命中，明确告诉用户当前本地数据不足，并建议先用 `ccf-research` 搜索补库
- 若多个关键词都相关，优先覆盖更贴近问题的 2-5 个关键词，不要无节制扩大范围
