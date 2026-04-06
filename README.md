# exScholar

exScholar is a local-first paper discovery and review workspace. It can search papers by keyword, fetch abstracts, build a static website for browsing results, expand to related papers, maintain a citation library, and export review logs for downstream summarization.

## What It Does

- Search papers by keyword and save results as `CSV`, `JSON`, and static HTML
- Fetch abstracts from multiple sources
- Build a browsable site with timeline, keyword pages, and citation library
- Expand a paper into related papers using DOI / citation APIs
- Export local review bundles from existing keywords and abstracts

## Requirements

- Python `3.11`
- Conda environment recommended
- Playwright `chromium`
- `aiohttp`-based abstract pipeline

Recommended runtime:

- Conda env: `openclaw-analytics`
- Use `oc-conda-run` when available
- Avoid system/base Python `3.13` for the abstract-fetch workflow

## Setup

```bash
conda env create -f environment.yml
conda activate openclaw-analytics
python -m playwright install chromium
```

Or use the wrapper if `oc-conda-run` is available:

```bash
oc-conda-run -- python -m playwright install chromium
```

You can also run searches through the fixed wrapper:

```bash
./run_search.sh --keywords "openclaw" --slug demo --top 5 --year-from 2020
```

## Configuration

Copy `.env.local.example` to `.env.local` and fill in what you need.

Main settings:

- `PUBLIC_SITE_BASE_URL`
- `PUBLIC_SITE_HOST`
- `PUBLIC_SITE_PORT`
- `SITE_SERVER_HOST`
- `SITE_PASSWORD_SALT`
- `SITE_PASSWORD_HASH`
- `SITE_SESSION_SECRET`
- `REFERENCE_EXPAND_LIMIT`
- `AI4SCHOLAR_API_KEY`

Proxy settings are optional:

- `PROXY_API_KEY`
- `PROXY_API_SIGN`
- `PROXY_USERNAME`
- `PROXY_PASSWORD`

## Proxy Integration

The crawler supports Shenlong proxy IP rotation through:

- `PROXY_API_KEY`
- `PROXY_API_SIGN`
- `PROXY_USERNAME`
- `PROXY_PASSWORD`

Implementation notes for later development:

- proxy endpoint: `http://api.shenlongip.com/ip`
- protocol mode: `2`
- pattern: `json`
- count: `1`
- proxy pool falls back to local network if proxy config is unavailable
- proxy config is validated on startup before the pool is used

If proxy settings are missing or invalid, the project will continue with local requests instead of failing hard.

## Common Commands

Run a keyword search:

```bash
./run_search.sh \
  --keywords "openclaw" \
  --slug "demo" \
  --top 5 \
  --year-from 2020
```

Direct Python entry:

```bash
oc-conda-run -- python search.py \
  --keywords "openclaw" \
  --slug "demo" \
  --top 5 \
  --year-from 2020
```

Run the original crawler entry:

```bash
oc-conda-run -- python main.py -ccf a -c conf -m 20 -p 10
```

Start the static site:

```bash
oc-conda-run -- python serve_searches.py
```

Set or rotate the site password:

```bash
oc-conda-run -- python set_site_password.py --password 'your-password'
```

Export a local review bundle:

```bash
oc-conda-run -- python keyword_review.py \
  --query "stress coping UI research review" \
  --slug "stress-review"
```

## CLI Reference

Keyword search:

- `--keywords`: semicolon-separated keyword groups
- `--venues`: comma-separated venue abbreviations; empty means global search
- `--slug`: output directory slug
- `--top`: max papers per keyword × venue before dedupe
- `--year-from`: lower year bound
- `--no-abstract`: skip abstract fetching

Main crawler:

- `-ccf`: CCF level such as `a`, `b`, `c`
- `-c, --classification`: `conf` or `journal`
- `-m, --max-concurrent`: max abstract-fetch concurrency
- `-p, --proxy-pool-size`: proxy pool size

Review export:

- `--query`: natural-language review request
- `--keywords`: explicit semicolon-separated keywords
- `--top-keywords`: max auto-selected keywords
- `--max-papers`: cap exported papers
- `--slug`: output slug
- `--list-only`: only print available keywords

## Output Layout

Original search results:

```text
data/searches/YYYY-MM-DD_<slug>/
```

Expansion results:

```text
data/expansions/YYYY-MM-DD_<slug>/
```

Review logs:

```text
data/review_logs/YYYY-MM-DD_<slug>/
```

Each search result directory typically contains:

- `search.json`
- `papers.csv`
- `papers.json`
- `site/index.html`

## Site Features

- Timeline for original searches and expansion searches
- Keyword index page
- Keyword detail pages
- Citation library with tag filtering and JSON export
- Expansion pages for related papers
- Password-protected access

## Development Notes

- `search.py` writes original results into `data/searches/`
- `serve_searches.py` serves both `data/searches/` and `data/expansions/`
- `keyword_review.py` exports review bundles into `data/review_logs/`
- `.env.local` is intentionally excluded from version control
- `data/` is kept clean in the repository; runtime outputs are not committed
- If local data is insufficient for review generation, run a fresh search first

## Project Structure

```text
.
├── main.py
├── search.py
├── serve_searches.py
├── keyword_review.py
├── run_search.sh
├── set_site_password.py
├── driver.py
├── utils.py
├── crawler/
├── config/
├── skills/
└── data/
```

## Notes

- Static site URLs are derived from `PUBLIC_SITE_BASE_URL`
- Site password hashes are managed through `set_site_password.py`
- Expansion search prefers `AI4SCHOLAR_API_KEY` when available and falls back to DOI-based sources
