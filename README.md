# exScholar

`exScholar` 是一套本地优先的论文搜索与深度阅读工作台。

它把三类能力放在同一套数据和页面里：

- 关键词论文搜索
- 引文/参考文献扩展搜索
- PDF 绑定、深度分析、问答与阅读笔记

## 当前能力

- 按关键词搜索 DBLP 论文，并生成 `CSV`、`JSON`、静态网页
- 抓取摘要并在结果页中浏览
- 从单篇论文继续做引文/参考文献扩展
- 将论文加入深度阅读库，并绑定 PDF
- 上传 PDF 时自动抽取元数据，匹配已有文献或创建新文献
- 同一份 PDF 按哈希去重，重复上传时直接复用数据库里的已有链接
- 已绑定 PDF 的文献会显示 `更新 PDF`，未绑定时显示 `上传 PDF`
- 上传过程带进度条反馈
- 为论文创建阅读工作区，生成：
  - `paper.json`
  - `analysis.json`
  - `qa_history.json`
  - `notes.json`
  - `source/full_text.json`
  - `source/sections.json`
- 使用 Moonshot / Kimi 对 PDF 做结构化深度分析
- 在阅读页里：
  - 重新分析
  - 提问并保存问答历史
  - 删除单条提问记录
  - 按模块手工填写 Notes 并保存
- 从深度阅读库删除文献时，会一并删除阅读工作区、分析、提问、Notes、group 关联，以及独占 PDF 文件
- 将分析得到的 `research_theme` 回写到 citation `tags`，并在 `/keywords` 页面中展示

## 环境要求

- Python `3.11+`
- Conda 环境，推荐 `openclaw-analytics`
- Playwright `chromium`
- 可访问 DBLP / OpenAlex / Moonshot / AI4Scholar

推荐安装：

```bash
conda env create -f environment.yml
conda activate openclaw-analytics
python -m playwright install chromium
```

或：

```bash
oc-conda-run -- python -m playwright install chromium
```

## 配置

复制 `.env.local.example` 为 `.env.local` 后再填写。

站点相关：

- `PUBLIC_SITE_BASE_URL`
- `PUBLIC_SITE_PORT`
- `SITE_SERVER_HOST`
- `SITE_PASSWORD_SALT`
- `SITE_PASSWORD_HASH`
- `SITE_SESSION_SECRET`

搜索/扩展：

- `REFERENCE_EXPAND_LIMIT`
- `AI4SCHOLAR_API_KEY`

深度分析：

- `MOONSHOT_API_KEY`
- `MOONSHOT_BASE_URL`
- `MOONSHOT_ANALYSIS_MODEL`

## 常用命令

关键词搜索：

```bash
./run_search.sh \
  --keywords "physiological notification;biosignal alert" \
  --venues "chi,uist,cscw" \
  --slug "physio-ui" \
  --top 50 \
  --year-from 2020
```

直接执行 Python 搜索入口：

```bash
oc-conda-run -- python search.py \
  --keywords "physiological notification;biosignal alert" \
  --venues "chi,uist,cscw" \
  --slug "physio-ui" \
  --top 50 \
  --year-from 2020
```

运行原始主爬虫：

```bash
oc-conda-run -- python main.py -ccf a -c conf -m 20 -p 10
```

启动站点：

```bash
oc-conda-run -- python serve_searches.py
```

设置站点密码：

```bash
oc-conda-run -- python set_site_password.py --password 'your-password'
```

## 数据目录

搜索结果：

```text
data/searches/YYYY-MM-DD_<slug>/
```

扩展搜索：

```text
data/expansions/YYYY-MM-DD_<slug>/
```

PDF 库：

```text
data/library/
```

深度阅读工作区：

```text
data/reading/<paper_id>/
  ├── paper.json
  ├── analysis.json
  ├── qa_history.json
  ├── notes.json
  └── source/
      ├── full_text.json
      └── sections.json
```

说明：

- PDF 文件本体只保存在 `data/library/`
- `data/reading/<paper_id>/` 不再重复存 PDF，只保存分析与阅读派生数据

## 深度阅读流程

1. 从搜索结果页加入论文，或在 `/reading` 直接上传 PDF
2. 上传 PDF 后，系统先按 `sha256` 去重
3. 如果数据库里已有同一份 PDF，则直接复用原链接
4. 系统用 Moonshot Files API 抽取 PDF 文本
5. 系统按 DOI / 标题+年份 / 标题相似度匹配已有文献
6. 创建或更新 citation 记录，并绑定 PDF
7. 创建阅读工作区
8. 在阅读页触发深度分析，生成中文结构化 `analysis.json`
9. 分析结束后，把 `research_theme` 合并进 citation `tags`
10. `/keywords` 页面会同时展示原始搜索关键词和深度阅读回写关键词
11. 如果从深度阅读库删除该文献，会清除该阅读条目的全部相关派生数据；共享 PDF 会保留给其他文献继续使用

## 页面结构

- `/`
  搜索时间线，展示原始搜索与扩展搜索
- `/keywords`
  所有关键词索引页
- `/keywords/<keyword>`
  某个关键词对应的论文列表
- `/reading`
  深度阅读库，支持分组、PDF 绑定、标签筛选、上传进度、更新 PDF、删除深度阅读数据
- `/reading/<paper_id>`
  单篇论文的阅读页

## 阅读页内容

阅读页当前包含：

- Overview / Problem / Method / Results / Critique
- 分析状态与进度条
- 提问模块
- 提问历史
- 单条提问删除
- 五个模块的手工 Notes：
  - Overview Notes
  - Problem Notes
  - Method Notes
  - Results Notes
  - Critique Notes

## 代码结构

```text
.
├── main.py
├── search.py
├── serve_searches.py
├── run_search.sh
├── set_site_password.py
├── driver.py
├── utils.py
├── crawler/
├── config/
├── skills/
└── data/
```

说明：

- `search.py` 负责搜索、去重、生成 `CSV/JSON/site`
- `serve_searches.py` 负责搜索站点、深度阅读页、数据库与 PDF 绑定逻辑
- `skills/ccf-research/SKILL.md` 是 OpenClaw/OpenCode 风格的研究搜索 skill 说明

## Ubuntu / OpenClaw Skill 联通性检查

已在当前 Ubuntu 环境核对以下条件：

- `oc-conda-run` 存在
- `python3` 存在
- `run_search.sh` 可执行
- `run_search.sh --help` 可正常调用 `search.py`
- `.env.local` 中的 `PUBLIC_SITE_BASE_URL`、`PUBLIC_SITE_PORT`、`SITE_SERVER_HOST` 已配置
- 站点服务可在 `38128` 端口启动

因此，`skills/ccf-research/SKILL.md` 里定义的这条 research skill 目前可以连到本仓库的搜索工具链。

需要注意：

- 这个 skill 依赖 `openclaw-analytics` 环境和 `oc-conda-run`
- skill 文档里写的是“先确认搜索方案，再执行搜索”，它更适合给代理/助手使用，而不是独立 CLI 程序
- 如果换机器部署，优先检查 `oc-conda-run`、Playwright、`.env.local` 和 `38128` 端口

## 开发说明

- `.env.local` 不纳入版本控制
- 运行产物大多位于 `data/`
- Moonshot 请求在应用内部显式禁用了环境代理继承
- 当前主逻辑集中在 `serve_searches.py`，后续仍建议继续拆分 `db / reading / analysis / handlers`
