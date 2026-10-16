# EUC News

Automated End-User Computing news aggregator: Citrix/NetScaler, Omnissa & VMware Horizon, Microsoft (AVD, Windows 365, FSLogix, M365, Azure Local, Windows patch known issues), ControlUp, IGEL, Nutanix, Unicon, plus a Security lane (CISA KEV, MSRC, BleepingComputer, Born's Tech, The Register).

**Site:** https://stevennoel.github.io/euc-news/

## How it works
- `sources.yml` – feeds, keyword searches, tag keyword map, KEV vendor filter, curated X accounts.
- `scripts/fetch.py` – fetches all feeds (failures are logged and skipped), tags items, de-duplicates by URL and normalized title, and keeps a 30-day rolling window in `data/items.json`.
- `site/` – static HTML/JS/CSS news layout: Top Stories (multi-publisher coverage ranking with "N sources"), topic sections (Security & Hardening, Virtualization & VDI, Microsoft & Windows, Cloud & M365, Endpoints & Management), right-rail panels for Top CVEs & breaches and Outages & status, vendor filter chips, search, light/dark.

### Security, CVEs & de-duplication
- **Security & Hardening** section: hardening/best-practice guidance and security-vendor product news (Microsoft Security, CrowdStrike, SentinelOne, Palo Alto, Zscaler, Netskope) plus hardening keyword searches.
- **Top CVEs & breaches** rail (newest 10 shown, 40 kept) holds individual CVE/patch/breach items: CISA KEV entries for tracked vendors added in the window, MSRC CVEs that are Critical, exploited or publicly disclosed (CVRF API), and news mentioning zero-day / exploited / critical / CVSS 9+ / ransomware / breach / RCE. 
- Duplicates are merged on normalized URL (no scheme, `www.`, tracking params or trailing slash; Google News/Bing redirects resolved) or fuzzy title match (Jaccard ≥ 0.8 after removing publisher suffix and stopwords). Merged copies become "related coverage".
- Stories are clustered by title similarity (≥ 0.5), shared CVE ID, or shared KB number; each cluster appears once on the page, in its lead story's section, and Top Stories are not repeated below.

### AI section & LinkedIn-derived sources
- **AI & Local LLMs** section: OpenAI, Google Gemini, Hugging Face and Ollama blogs; GitHub release feeds (Ollama, llama.cpp, OpenClaw, Hermes Agent); r/LocalLLaMA; news searches for Grok, Claude, OpenAI, Gemini, OpenClaw, Hermes, LM Studio and low-VRAM/quantized models. AI items get a ranking penalty (`AI_PENALTY`) so they don't dominate Top Stories.
- LinkedIn: no scraping or logins. Public Google News results for `site:linkedin.com/pulse` + EUC/IT keywords, plus the public blogs of EUC creators. Items are routed to the normal topic sections by tags.

### Ranking & thumbnails
`fetch.py` scores each story by recency (36h half-life), vendor relevance, multi-publisher coverage (title clustering), security severity (KEV/zero-day/critical), trusted sources, and a **Citrix/NetScaler boost**. Thumbnails come from feed media, Bing `News:Image`, or the article's `og:image` (capped per run). Google News redirect links are resolved to the publisher (best effort, capped), and Bing/publisher copies are preferred when de-duplicating.
- `.github/workflows/update.yml` – runs every 4 hours (and on demand), keeps `data/items.json` between runs via the Actions cache (not committed), and deploys to GitHub Pages.

## Add a feed
Append to `feeds:` in `sources.yml`:
```yaml
  - {name: My Blog, url: "https://example.com/feed/", type: rss, tags: [citrix]}
```
Optional keys: `require_regex` / `exclude_regex` (filter by title+summary), `title_prefix`, `security: true` (Security section), `outage: true` (Outages & status panel), `unreliable_dates: true`, `require_keywords: true` (only keep items matching a vendor tag – good for broad feeds), `max_items: 50`.
Check it first: `curl -sIL https://example.com/feed/`.

## Add a keyword search
Use Google News RSS or Bing News RSS with `type: search`:
```yaml
  - {name: "Google News: Foo", url: "https://news.google.com/rss/search?hl=en-US&gl=US&ceid=US:en&q=Foo+Bar", type: search, tags: [foo]}
  - {name: "Bing News: Foo", url: "https://www.bing.com/news/search?format=rss&q=Foo+Bar", type: search, tags: [foo]}
```
URL-encode the query (spaces → `+`, quotes → `%22`). Bing works best with simple queries.

## Tags, KEV and X accounts
- `tag_keywords`: tag → list of case-insensitive substrings matched against title + summary.
- `kev_vendors`: CISA KEV entries whose vendor/product contains one of these are included.

## Run locally
```bash
python -m venv .venv && . .venv/bin/activate
pip install -r requirements.txt
python scripts/fetch.py
mkdir -p site/data && cp data/items.json site/data/ && python -m http.server -d site 8000
```

### Top Stories
Ranked by multi-publisher coverage (distinct publishers in a story cluster), recency (24h half-life) and source authority, with at most 2 stories per vendor and 3 per section (2 for AI). Raw CVE/KEV/MSRC entries, GitHub releases, Microsoft Learn pages, Reddit, press releases and security-vendor blogs without multi-source coverage are excluded (`no_top: true` in sources.yml). If there aren't enough multi-source stories, recent quality articles fill the rest.
