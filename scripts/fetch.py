#!/usr/bin/env python3
"""Fetch EUC news feeds, tag, dedupe and keep a 30-day rolling window in data/items.json."""
import hashlib, html, json, re, sys, time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path
from urllib.parse import urlparse, urlunparse, parse_qsl, urlencode, parse_qs

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
OG_CAP = 150          # max og:image page fetches per run
OG_TIMEOUT = 6
OG_SKIP_HOSTS = ("news.google.com", "nvd.nist.gov", "msrc.microsoft.com", "learn.microsoft.com")
IMG_RE = re.compile(r'<img[^>]+src=["\']([^"\']+)', re.I)
OG_RE = re.compile(r'<meta[^>]+(?:property|name)=["\'](?:og:image|twitter:image)["\'][^>]*>', re.I)
CONTENT_RE = re.compile(r'content=["\']([^"\']+)', re.I)

GN_CAP = 150          # max Google News redirect resolutions per run
BROWSER_UA = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130 Safari/537.36"

# Topic sections, first match wins (security is decided separately).
SECTIONS = [
    ("virtualization", {"citrix", "netscaler", "vmware", "omnissa", "nutanix", "xenserver", "avd", "w365", "fslogix"}),
    ("endpoints", {"igel", "controlup", "unicon", "intune"}),
    ("cloud", {"m365", "azure-local"}),
    ("windows", {"windows", "patch", "microsoft"}),
]
VENDOR_TAGS = ["citrix", "netscaler", "xenserver", "vmware", "omnissa", "nutanix", "avd", "w365", "fslogix",
               "microsoft", "windows", "m365", "azure-local", "intune", "igel", "controlup", "unicon"]
TRUSTED = ("cisa", "msrc", "microsoft", "bleepingcomputer", "the register", "born", "citrix", "omnissa",
           "broadcom", "vmware", "nutanix", "igel", "controlup", "securityweek", "ars technica", "carl stalhood")
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


def http_img(u):
    return u if isinstance(u, str) and u.startswith(("http://", "https://")) else None


def entry_thumb(e):
    for k in ("media_thumbnail", "media_content"):
        for m in e.get(k) or []:
            u = http_img(m.get("url"))
            if u and (k == "media_thumbnail" or "image" in (m.get("type") or "image") or m.get("medium") == "image"):
                return u
    for l in (e.get("enclosures") or []) + (e.get("links") or []):
        if (l.get("type") or "").startswith("image") and http_img(l.get("href")):
            return l["href"]
    if http_img(e.get("news_image")):  # Bing News
        return e["news_image"].replace("http://", "https://", 1)
    for c in [e.get("summary", "")] + [x.get("value", "") for x in e.get("content") or []]:
        m = IMG_RE.search(c or "")
        if m and http_img(m.group(1)) and not m.group(1).endswith((".gif",)):
            return m.group(1)
    return None


def unwrap(link):
    p = urlparse(link)
    if p.netloc.endswith("bing.com") and "apiclick" in p.path:
        u = parse_qs(p.query).get("url", [None])[0]
        if u:
            return u
    return link


def og_image(url):
    try:
        r = requests.get(url, headers={"User-Agent": UA}, timeout=OG_TIMEOUT, stream=True)
        if r.status_code != 200 or "html" not in r.headers.get("content-type", ""):
            return None
        head = r.raw.read(200_000, decode_content=True).decode("utf-8", "ignore")
        r.close()
        for tag in OG_RE.findall(head):
            m = CONTENT_RE.search(tag)
            if m:
                u = html.unescape(m.group(1))
                if u.startswith("//"):
                    u = "https:" + u
                return http_img(u)
    except Exception:
        return None
    return None


def categorize(tags):
    for cat, keys in CATEGORIES:
        if keys & set(tags):
            return cat
    return "other"


def resolve_gn(link):
    """Best-effort: turn a news.google.com/rss/articles/... redirect into the publisher URL."""
    try:
        aid = urlparse(link).path.rsplit("/", 1)[-1]
        h = requests.get(f"https://news.google.com/rss/articles/{aid}", headers={"User-Agent": BROWSER_UA}, timeout=8).text
        sg = re.search(r'data-n-a-sg="([^"]+)"', h); ts = re.search(r'data-n-a-ts="([^"]+)"', h)
        if not (sg and ts):
            return None
        req = ["Fbv4je", '["garturlreq",[["X","X",["X","X"],null,null,1,1,"US:en",null,1,null,null,null,null,null,0,1],'
               f'"X","X",1,[1,1,1],1,1,null,0,0,null,0],"{aid}",{ts.group(1)},"{sg.group(1)}"]']
        r = requests.post("https://news.google.com/_/DotsSplashUi/data/batchexecute",
                          headers={"User-Agent": BROWSER_UA, "Content-Type": "application/x-www-form-urlencoded;charset=UTF-8"},
                          data={"f.req": json.dumps([[req]])}, timeout=8)
        u = json.loads(json.loads(r.text.split("\n\n")[1])[0][2])[1]
        return u if isinstance(u, str) and u.startswith("http") else None
    except Exception:
        return None


def is_gn(it):
    return "news.google.com" in it["link"]


def section_of(it):
    if it.get("outage"):
        return "outages"
    if it.get("security"):
        return "security"
    tags = set(it["tags"])
    for sec, keys in SECTIONS:
        if keys & tags:
            return sec
    return "windows"


def tokens(t):
    return {w for w in norm_title(t).split() if len(w) > 3}


GENERIC = {"security", "microsoft", "windows", "patch", "outage"}


def search_tags(feed, matched):
    """Keyword-search results: trust the text match over the query's tags when the text names a product."""
    ftags = set(feed.get("tags", []))
    if feed["type"] == "search" and matched - GENERIC:
        return matched | (ftags & GENERIC)
    return ftags | matched


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
        link = unwrap(e.get("link", ""))
        if not title or not link:
            continue
        summary = strip_html(e.get("summary", ""))
        source = feed["name"]
        if feed["type"] == "search":
            src = e.get("source")
            pub = (src.get("title") if isinstance(src, dict) else None) or e.get("news_source")
            if pub:
                source = pub
                if title.endswith(f" - {pub}"):
                    title = title[: -len(pub) - 3].strip()
            else:
                title = re.sub(r"\s+-\s+[^-]{2,60}$", "", title)
        if feed.get("outage"):
            # status feeds: skip scheduled maintenance and undated entries; news: require outage wording
            if not to_dt(e) or re.search(r"maintenance|\([^)]+\) on \d{4}-\d{2}-\d{2}", title, re.I):
                continue
            if feed["type"] == "search" and not re.search(r"outage|(goes|went|is|was|are) down\b|disruption|incident|degraded", title, re.I):
                continue
        if source.startswith("http"):
            source = urlparse(source).netloc.removeprefix("www.") or source
        if norm_title(summary)[:40] == norm_title(title)[:40]:  # search feeds echo the title as summary
            summary = ""
        matched = tag_text(f"{title} {summary}", tag_map)
        if feed.get("require_keywords") and not (matched - {"security", "microsoft", "windows", "patch"}):
            # generic feeds: keep only items that hit a specific EUC vendor/product tag
            continue
        out.append(dict(title=title, link=link, summary=summary, source=source,
                        published=to_dt(e), undated=to_dt(e) is None or bool(feed.get("unreliable_dates")), tags=search_tags(feed, matched),
                        security=bool(feed.get("security")) and not feed.get("outage"),
                        outage=bool(feed.get("outage")), image=entry_thumb(e)))
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
            tags=set(feed.get("tags", [])) | vendor_tags, security=True, image=None))
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
            dup["outage"] = dup.get("outage") or it.get("outage", False)
            if is_gn(dup) and not is_gn(it):  # prefer publisher/Bing copy over Google News
                for k in ("link", "source", "image", "title"):
                    if it.get(k):
                        dup[k] = it[k]
            elif not dup.get("image") and it.get("image"):
                dup["image"] = it["image"]
            dup.setdefault("sources", [])
            if it["source"] not in dup["sources"] and it["source"] != dup["source"]:
                dup["sources"].append(it["source"])
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
        if "security" in it["tags"] and not it.get("outage"):
            it["security"] = True
        add(it)

    # Resolve Google News redirects (capped) so we can link to and fetch images from publishers
    gn = [i for i in by_key.values() if is_gn(i) and not i.get("gn_tried")]
    gn.sort(key=lambda i: i["published"], reverse=True)
    gn = gn[:GN_CAP]
    with ThreadPoolExecutor(max_workers=8) as ex:
        for it, u in zip(gn, ex.map(lambda i: resolve_gn(i["link"]), gn)):
            it["gn_tried"] = True
            if u:
                it["link"] = u
    print(f"google news: resolved {sum(1 for i in gn if not is_gn(i))}/{len(gn)}")

    # og:image only for items lacking a thumbnail, capped and parallel
    need = [i for i in by_key.values() if not i.get("image") and not i.get("og_tried")
            and urlparse(i["link"]).netloc not in OG_SKIP_HOSTS
            and i["published"] >= CUTOFF.strftime("%Y-%m-%dT%H:%M:%SZ")]
    need.sort(key=lambda i: i["published"], reverse=True)
    need = need[:OG_CAP]
    with ThreadPoolExecutor(max_workers=12) as ex:
        for it, img in zip(need, ex.map(lambda i: og_image(i["link"]), need)):
            it["og_tried"] = True
            if img:
                it["image"] = img
    print(f"og:image: tried {len(need)}, found {sum(1 for i in need if i.get('image'))}")
    for i in by_key.values():
        i.pop("category", None)
        i.setdefault("image", None)
        i.setdefault("sources", [])
        i["section"] = section_of(i)
        i["vendors"] = [t for t in VENDOR_TAGS if t in i["tags"]]

    cutoff = CUTOFF.strftime("%Y-%m-%dT%H:%M:%SZ")
    items = sorted((i for i in by_key.values() if i["published"] >= cutoff),
                   key=lambda i: i["published"], reverse=True)
    # Cluster near-duplicate stories (token Jaccard) and score for Top Stories
    toks = {i["id"]: tokens(i["title"]) for i in items}
    for i in items:
        i["cluster"] = i["id"]
    for a_idx, a in enumerate(items):
        if a["cluster"] != a["id"]:
            continue
        ta = toks[a["id"]]
        for b in items[a_idx + 1:]:
            tb = toks[b["id"]]
            if b["cluster"] == b["id"] and ta and tb and len(ta & tb) / len(ta | tb) >= 0.45:
                b["cluster"] = a["id"]
    srcs = {}
    for i in items:  # coverage = distinct publishers in a cluster
        srcs.setdefault(i["cluster"], set()).update([i["source"], *i["sources"]])
    size = {k: len(v) for k, v in srcs.items()}
    for i in items:
        age_h = 48 if i.get("undated") else (NOW - datetime.strptime(i["published"], "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)).total_seconds() / 3600
        txt = f"{i['title']} {i['summary']}".lower()
        sev = 3 if i["source"] == "CISA KEV" or re.search(r"zero-day|0-day|actively exploited|critical", txt) else (1.5 if i["security"] else 0)
        citrix = 5 if {"citrix", "netscaler"} & set(i["vendors"]) else 0  # Steve: boost Citrix highest
        trust = 1.5 if any(t in i["source"].lower() for t in TRUSTED) else 0
        i["score"] = round(10 * 0.5 ** (age_h / 36) + 1.2 * len(i["vendors"][:3]) + 2 * min(size[i["cluster"]] - 1, 4)
                           + sev + trust + citrix + (1 if i["image"] else 0), 2)

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
