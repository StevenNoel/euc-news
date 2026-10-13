# EUC News

Automated End-User Computing news aggregator: Citrix/NetScaler, Omnissa & VMware Horizon, Microsoft (AVD, Windows 365, FSLogix, M365, Azure Local, Windows patch known issues), ControlUp, IGEL, Nutanix, Unicon, plus a Security lane (CISA KEV, MSRC, BleepingComputer, Born's Tech, The Register).

**Site:** https://stevennoel.github.io/euc-news/

## How it works
- `sources.yml` – feeds, keyword searches, tag keyword map, KEV vendor filter, curated X accounts.
- `scripts/fetch.py` – fetches all feeds (failures are logged and skipped), tags items, de-duplicates by URL and normalized title, and keeps a 30-day rolling window in `data/items.json`.
- `site/` – static HTML/JS/CSS (filters, search, Security lane, X / Community).
- `.github/workflows/update.yml` – runs every 4 hours (and on demand), commits `data/items.json` if it changed, and deploys to GitHub Pages.

## Add a feed
Append to `feeds:` in `sources.yml`:
```yaml
  - {name: My Blog, url: "https://example.com/feed/", type: rss, tags: [citrix]}
```
Optional keys: `security: true` (always in Security lane), `require_keywords: true` (only keep items matching a vendor tag – good for broad feeds), `max_items: 50`.
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
- `x_accounts`: `{handle: name, note: label}` rendered as profile links (no X API key required).

## Run locally
```bash
python -m venv .venv && . .venv/bin/activate
pip install -r requirements.txt
python scripts/fetch.py
mkdir -p site/data && cp data/items.json site/data/ && python -m http.server -d site 8000
```
