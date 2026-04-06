# exScholar

exScholar is a local-first paper search and deep-reading workspace.

It supports:

- keyword-based paper search
- abstract fetching
- static result pages for browsing
- citation expansion from an existing paper
- a deep-reading library that links papers, PDFs, reading groups, and structured reading pages
- PDF-based metadata extraction and single-call structured analysis via Moonshot/Kimi

## Core Features

- Search papers by keyword and save `CSV`, `JSON`, and static HTML
- Browse results through timeline, keyword pages, and expansion pages
- Add papers into the deep-reading library
- Upload a PDF when adding a paper, or upload a PDF later and bind it to an existing paper
- Match uploaded PDFs to existing papers by DOI, title/year, or title similarity
- Generate a deep-reading workspace with:
  - `paper.json`
  - `analysis.json`
  - extracted full text and sections
- Run one-call structured reading analysis with `kimi-k2.5`

## Requirements

- Python `3.11+`
- Conda recommended
- Playwright `chromium`
- network access for DBLP / OpenAlex / Moonshot / AI4Scholar

Recommended environment:

- conda env: `openclaw-analytics`
- use `oc-conda-run` when available

## Setup

```bash
conda env create -f environment.yml
conda activate openclaw-analytics
python -m playwright install chromium
```

Or:

```bash
oc-conda-run -- python -m playwright install chromium
```

## Configuration

Copy `.env.local.example` to `.env.local` and fill in the values you need.

Main site settings:

- `PUBLIC_SITE_HOST`
- `PUBLIC_SITE_PORT`
- `PUBLIC_SITE_BASE_URL`
- `SITE_SERVER_HOST`
- `SITE_PASSWORD_SALT`
- `SITE_PASSWORD_HASH`
- `SITE_SESSION_SECRET`

Search / expansion settings:

- `REFERENCE_EXPAND_LIMIT`
- `AI4SCHOLAR_API_KEY`

Deep-reading analysis settings:

- `MOONSHOT_API_KEY`
- `MOONSHOT_BASE_URL`
- `MOONSHOT_ANALYSIS_MODEL`

Proxy settings are optional:

- `PROXY_API_KEY`
- `PROXY_API_SIGN`
- `PROXY_USERNAME`
- `PROXY_PASSWORD`

## Common Commands

Run a keyword search:

```bash
./run_search.sh \
  --keywords "physiological notification;biosignal alert" \
  --venues "chi,uist,cscw" \
  --slug "physio-ui" \
  --top 50 \
  --year-from 2020
```

Run the Python entry directly:

```bash
oc-conda-run -- python search.py \
  --keywords "physiological notification;biosignal alert" \
  --venues "chi,uist,cscw" \
  --slug "physio-ui" \
  --top 50 \
  --year-from 2020
```

Run the original crawler:

```bash
oc-conda-run -- python main.py -ccf a -c conf -m 20 -p 10
```

Start the site:

```bash
oc-conda-run -- python serve_searches.py
```

Set or rotate the site password:

```bash
oc-conda-run -- python set_site_password.py --password 'your-password'
```

## Output Layout

Search results:

```text
data/searches/YYYY-MM-DD_<slug>/
```

Expansion results:

```text
data/expansions/YYYY-MM-DD_<slug>/
```

Deep-reading workspaces:

```text
data/reading/<paper_id>/
  ├── paper.json
  ├── analysis.json
  └── source/
      ├── <uploaded-pdf>.pdf
      ├── full_text.json
      └── sections.json
```

Library PDFs:

```text
data/library/
```

Typical search directory contents:

- `search.json`
- `papers.csv`
- `papers.json`
- `site/index.html`

## Deep Reading Flow

1. Add a paper from search results, optionally with a PDF.
2. Or upload a PDF from the deep-reading page.
3. The system extracts metadata from the PDF.
4. It tries to match an existing paper by:
   - DOI
   - exact title + year
   - exact title
   - title similarity >= 85%
5. The PDF is linked to the matched paper or used to create a new paper record.
6. A deep-reading workspace is created.
7. The PDF text is extracted via Moonshot Files API.
8. A single Kimi call generates structured `analysis.json`.

## Site Features

- Timeline for search and expansion history
- Keyword index and keyword detail pages
- Deep-reading library with:
  - tag filters
  - group filters
  - reading groups
  - PDF upload and binding
  - remove-from-reading action
- Per-paper reading page with Overview / Problem / Method / Results / Critique
- Password-protected access

## Development Notes

- `search.py` writes search results into `data/searches/`
- `serve_searches.py` serves search, expansion, and deep-reading pages
- `data/` runtime outputs are mostly not tracked in git
- `.env.local` is intentionally excluded from version control
- Moonshot requests are configured to bypass environment proxy settings inside the app

## Project Structure

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
