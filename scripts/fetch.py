#!/usr/bin/env python3
"""Fetch EUC news feeds, tag, dedupe and keep a 30-day rolling window in data/items.json."""
import hashlib, html, json, re, sys, time
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path
from urllib.parse import urlparse, urlunparse, parse_qsl, urlencode

import feedparser
import requests
import yaml

ROOT = Path(__file__).resolve().parent.parent
SOURCES = ROOT / "sources.yml"
DATA = ROOT / "data" / "items.json"
SITE_DATA = ROOT / "site" / "data"
WINDOW_DAYS = 30
UA = "euc-news-aggregator/1.0 (+https://github.com/StevenNoel/euc-news)"
TIMEOUT = 30
NOW = datetime.now(timezone.utc)
CUTOFF = NOW - timedelta(days=WINDOW_DAYS)
TRACKING = re.compile(r"^(utm_|fbclid|gclid|mc_|ocid|cmpid)", re.I)


def clean_url(u):
    try:
        p = urlparse(u)
        q = [(k, v) for k, v in parse_qsl(p.query) if not TRACKING.match(k)]
        return urlunparse((p.scheme, p.netloc.lower(), p.path.rstrip("/"), "", urlencode(q), ""))
    except Exception:
        return u


def strip_html(s, n=400):
    s = re.sub(r"<[^>]+>", " ", s or "")
    s = re.sub(r"\s+", " ", html.unescape(s)).strip()
    return s[:n] + ("…" if len(s) > n else "")


def norm_title(t):
    t = re.sub(r"\s+-\s+[^-]{2,60}$", "", t or "")  # drop " - Publisher" suffix from search feeds
    return re.sub(r"[^a-z0-9]+", " ", t.lower()).strip()


def to_dt(entry):
    for k in ("published_parsed", "updated_parsed"):
        v = entry.get(k)
        if v:
            return datetime.fromtimestamp(time.mktime(v), tz=timezone.utc)
    for k in ("published", "updated"):
        v = entry.get(k)
        if v:
            try:
                return parsedate_to_datetime(v).astimezone(timezone.utc)
            except Exception:
                pass
    return None


def tag_text(text, tag_map):
    t = f" {text.lower()} "
    return {tag for tag, kws in tag_map.items() if any(k.lower() in t for k in kws)}


def get(url):
    r = requests.get(url, headers={"User-Agent": UA}, timeout=TIMEOUT)
    r.raise_for_status()
    return r


def fetch_rss(feed, tag_map):
    parsed = feedparser.parse(get(feed["url"]).content)
    if parsed.bozo and not parsed.entries:
        raise ValueError(f"unparseable feed: {parsed.bozo_exception}")
    out = []
    for e in parsed.entries[: feed.get("max_items", 100)]:
        title = strip_html(e.get("title", ""), 300)
        link = e.get("link", "")
        if not title or not link:
            continue
        summary = strip_html(e.get("summary", ""))
        source = feed["name"]
        if feed["type"] == "search":
            src = e.get("source")
            if isinstance(src, dict) and src.get("title"):
                source = f"{src['title']} (via {feed['name'].split(':')[0]})"
        matched = tag_text(f"{title} {summary}", tag_map)
        if feed.get("require_keywords") and not (matched - {"security", "microsoft", "windows", "patch"}):
            # generic feeds: keep only items that hit a specific EUC vendor/product tag
            continue
        out.append(dict(title=title, link=link, summary=summary, source=source,
                        published=to_dt(e), tags=set(feed.get("tags", [])) | matched,
                        security=bool(feed.get("security"))))
    return out


def fetch_kev(feed, vendors):
    data = get(feed["url"]).json()
    out = []
    for v in data.get("vulnerabilities", []):
        hay = f"{v.get('vendorProject','')} {v.get('product','')}".lower()
        if not any(x in hay for x in vendors):
            continue
        try:
            dt = datetime.strptime(v["dateAdded"], "%Y-%m-%d").replace(tzinfo=timezone.utc)
        except Exception:
            continue
        cve = v.get("cveID", "")
        vendor_tags = {x for x in vendors if x in hay}
        out.append(dict(
            title=f"[KEV] {cve}: {v.get('vendorProject','')} {v.get('product','')} – {v.get('vulnerabilityName','')}",
            link=f"https://nvd.nist.gov/vuln/detail/{cve}",
            summary=strip_html(f"{v.get('shortDescription','')} Action: {v.get('requiredAction','')} Due: {v.get('dueDate','')}"),
            source="CISA KEV", published=dt,
            tags=set(feed.get("tags", [])) | vendor_tags, security=True))
    return out


def main():
    cfg = yaml.safe_load(SOURCES.read_text())
    tag_map = cfg.get("tag_keywords", {})
    vendors = [v.lower() for v in cfg.get("kev_vendors", [])]

    existing = []
    if DATA.exists():
        try:
            existing = json.loads(DATA.read_text()).get("items", [])
        except Exception:
            existing = []

    status, new = [], []
    for feed in cfg.get("feeds", []):
        try:
            items = fetch_kev(feed, vendors) if feed["type"] == "kev" else fetch_rss(feed, tag_map)
            status.append({"name": feed["name"], "ok": True, "count": len(items)})
            new.extend(items)
            print(f"OK   {len(items):4d}  {feed['name']}")
        except Exception as ex:  # tolerate failing feeds
            status.append({"name": feed["name"], "ok": False, "error": str(ex)[:200]})
            print(f"FAIL       {feed['name']}: {str(ex)[:150]}", file=sys.stderr)

    by_key, seen_titles = {}, {}
    def add(it):
        key = hashlib.sha1(clean_url(it["link"]).encode()).hexdigest()[:16]
        tkey = norm_title(it["title"])
        dup = by_key.get(key) or (by_key.get(seen_titles[tkey]) if tkey in seen_titles else None)
        if dup:  # merge tags/security; keep first-seen
            dup["tags"] = sorted(set(dup["tags"]) | set(it["tags"]))
            dup["security"] = dup["security"] or it["security"]
            return
        it["id"] = key
        by_key[key] = it
        if tkey:
            seen_titles[tkey] = key

    for it in existing:
        add(dict(it))
    for it in new:
        pub = it["published"] or NOW
        if pub > NOW + timedelta(days=1):
            pub = NOW
        it["published"] = pub.strftime("%Y-%m-%dT%H:%M:%SZ")
        it["tags"] = sorted(it["tags"])
        if "security" in it["tags"]:
            it["security"] = True
        add(it)

    cutoff = CUTOFF.strftime("%Y-%m-%dT%H:%M:%SZ")
    items = sorted((i for i in by_key.values() if i["published"] >= cutoff),
                   key=lambda i: i["published"], reverse=True)
    out = {"updated": NOW.strftime("%Y-%m-%dT%H:%M:%SZ"), "window_days": WINDOW_DAYS,
           "count": len(items), "feeds": status, "x_accounts": cfg.get("x_accounts", []),
           "items": items}
    DATA.parent.mkdir(parents=True, exist_ok=True)
    DATA.write_text(json.dumps(out, indent=1, ensure_ascii=False))
    ok = sum(s["ok"] for s in status)
    print(f"\n{len(items)} items in window; feeds ok {ok}/{len(status)}")
    if ok == 0:
        sys.exit(1)


if __name__ == "__main__":
    main()
