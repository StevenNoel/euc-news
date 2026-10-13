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
        host = p.netloc.lower().removeprefix("www.").removeprefix("m.")
        return urlunparse(("", host, p.path.rstrip("/"), "", urlencode(sorted(q)), "")).lstrip("/")
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


SEC_RE = re.compile(r"zero[- ]day|0-day|actively exploited|exploited|in the wild|critical|cvss(?: score)?(?: of)?:? ?(?:9(?:\.\d)?|10(?:\.0)?)\b|ransomware|breach|\\brce\\b|remote code execution|emergency (?:fix|patch)", re.I)
CVE_RE = re.compile(r"CVE-\d{4}-\d{4,7}", re.I)
KB_RE = re.compile(r"\bKB\d{6,8}\b", re.I)
STOP = set("a an the and or of for to in on at by with from as is are was were be been it its this that these those how why what who new now after over into about via vs your you our we their his her not can will has have had more than out up off".split())
SEC_CAP = 40  # max Security clusters kept


def title_tokens(t):
    t = re.sub(r"\s+[-|–]\s+[^-|–]{2,60}$", "", t or "")
    return {w for w in re.sub(r"[^a-z0-9]+", " ", t.lower()).split() if w not in STOP and len(w) > 1}


def jacc(a, b):
    return len(a & b) / len(a | b) if a and b else 0.0


class UF:
    def __init__(self, n): self.p = list(range(n))
    def find(self, x):
        while self.p[x] != x:
            self.p[x] = self.p[self.p[x]]; x = self.p[x]
        return x
    def union(self, a, b):
        a, b = self.find(a), self.find(b)
        if a != b: self.p[max(a, b)] = min(a, b)


def fetch_msrc(feed, window_start):
    """MSRC CVRF API: keep only Critical, exploited, or publicly disclosed CVEs released in the window."""
    ups = get(feed["url"]).json().get("value", [])
    docs = [u for u in ups if re.match(r"^\d{4}-[A-Z][a-z]{2}$", u.get("ID", ""))
            and (u.get("CurrentReleaseDate") or "") >= window_start.strftime("%Y-%m-%d")]
    out = []
    for d in sorted(docs, key=lambda u: u["CurrentReleaseDate"], reverse=True)[:2]:
        r = requests.get(d["CvrfUrl"], headers={"User-Agent": UA, "Accept": "application/json"}, timeout=90)
        r.raise_for_status()
        for v in r.json().get("Vulnerability", []):
            cve = v.get("CVE") or ""
            if not cve.startswith("CVE-"):
                continue
            sev = {(t.get("Description") or {}).get("Value") for t in v.get("Threats", []) if t.get("Type") == 3}
            ex = " ".join((t.get("Description") or {}).get("Value") or "" for t in v.get("Threats", []) if t.get("Type") == 1)
            exploited, disclosed = "Exploited:Yes" in ex, "Publicly Disclosed:Yes" in ex
            if not ("Critical" in sev or exploited or disclosed):
                continue
            rel = None
            for h in v.get("RevisionHistory") or []:
                try:
                    rel = min(rel or "9", h["Date"][:19])
                except Exception:
                    pass
            try:
                dt = datetime.strptime(rel, "%Y-%m-%dT%H:%M:%S").replace(tzinfo=timezone.utc)
            except Exception:
                continue
            if dt < window_start:
                continue
            score = max([s.get("BaseScore") or 0 for s in v.get("CVSSScoreSets") or []] or [0])
            flags = [x for x, on in (("Exploited", exploited), ("Publicly disclosed", disclosed), ("Critical", "Critical" in sev)) if on]
            title = (v.get("Title") or {}).get("Value") or cve
            out.append(dict(title=f"{cve}: {title}", link=f"https://msrc.microsoft.com/update-guide/vulnerability/{cve}",
                            summary=f"{', '.join(flags)} · CVSS {score}", source="MSRC", published=dt, undated=False,
                            tags=set(feed.get("tags", [])) | {"windows"} if "Windows" in title else set(feed.get("tags", [])),
                            security=True, sev_hint=("exploited" if exploited else "critical"), image=None))
    return out


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
            items = (fetch_kev(feed, vendors) if feed["type"] == "kev" else
                     fetch_msrc(feed, CUTOFF) if feed["type"] == "msrc" else fetch_rss(feed, tag_map))
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
    stats = {"collected": len(new) + len(existing), "after_first_pass": len(by_key)}
    cutoff = CUTOFF.strftime("%Y-%m-%dT%H:%M:%SZ")
    items = [i for i in by_key.values() if i["published"] >= cutoff]
    stats["in_window_before_dedupe"] = len(items)

    # --- Security: only critical / exploited / high-impact ---
    kept = []
    for i in items:
        i.pop("category", None)
        i.setdefault("image", None); i.setdefault("sources", []); i.setdefault("related", [])
        i["tags"] = sorted(set(i["tags"]) - {"security"})
        if i["source"] in ("CISA KEV", "MSRC"):
            i["security"] = True
        elif not i.get("outage"):
            i["security"] = bool(SEC_RE.search(f"{i['title']} {i['summary']}"))
        i["vendors"] = [t for t in VENDOR_TAGS if t in i["tags"]]
        if i["security"]:
            i["tags"] = sorted(set(i["tags"]) | {"security"})
        elif not i.get("outage") and not i["vendors"]:  # demoted from Security and no EUC vendor -> drop
            continue
        if i["source"] == "MSRC Security Update Guide":  # legacy RSS items from cache
            continue
        kept.append(i)
    items = kept

    # --- Strong dedupe: normalized URL or fuzzy title (Jaccard >= 0.8) -> merge into one item ---
    def quality(i):
        return (not is_gn(i), bool(i["image"]), any(t in i["source"].lower() for t in TRUSTED), -len(i["title"]))
    toks = [title_tokens(i["title"]) for i in items]
    uf = UF(len(items))
    by_url = {}
    for n, i in enumerate(items):
        k = clean_url(i["link"])
        if k in by_url:
            uf.union(by_url[k], n)
        else:
            by_url[k] = n
    for x in range(len(items)):
        for y in range(x + 1, len(items)):
            ca = {c.upper() for c in CVE_RE.findall(items[x]["title"])}
            cb = {c.upper() for c in CVE_RE.findall(items[y]["title"])}
            if jacc(toks[x], toks[y]) >= 0.8 and not (ca and cb and ca != cb):
                uf.union(x, y)
    groups = {}
    for n in range(len(items)):
        groups.setdefault(uf.find(n), []).append(items[n])
    merged = []
    for g in groups.values():
        g.sort(key=quality, reverse=True)
        keep = g[0]
        for o in g[1:]:
            keep["tags"] = sorted(set(keep["tags"]) | set(o["tags"]))
            keep["security"] = keep["security"] or o["security"]
            keep["outage"] = keep.get("outage") or o.get("outage", False)
            keep["image"] = keep["image"] or o["image"]
            keep["published"] = min(keep["published"], o["published"]) if not keep.get("undated") else o["published"]
            for r in [o] + o.get("related", []):
                if r["source"] != keep["source"] and all(r["link"] != x["link"] for x in keep["related"]):
                    keep["related"].append({"title": r["title"], "link": r["link"], "source": r["source"]})
            for src in [o["source"], *o["sources"]]:
                if src != keep["source"] and src not in keep["sources"]:
                    keep["sources"].append(src)
        keep["vendors"] = [t for t in VENDOR_TAGS if t in keep["tags"]]
        merged.append(keep)
    stats["duplicates_removed"] = len(items) - len(merged)
    items = sorted(merged, key=lambda i: i["published"], reverse=True)

    # --- Clusters (related coverage): looser title match, same CVE, or same KB ---
    toks = [title_tokens(i["title"]) for i in items]
    keys = [set(m.upper() for m in CVE_RE.findall(i["title"]) + KB_RE.findall(i["title"])) for i in items]
    uf = UF(len(items))
    for x in range(len(items)):
        for y in range(x + 1, len(items)):
            cx, cy = {k for k in keys[x] if k.startswith("CVE")}, {k for k in keys[y] if k.startswith("CVE")}
            if cx and cy and not (cx & cy):
                continue  # different CVEs are different stories even if titles look alike
            if (keys[x] & keys[y]) or jacc(toks[x], toks[y]) >= 0.5:
                uf.union(x, y)
    members = {}
    for n, i in enumerate(items):
        members.setdefault(uf.find(n), []).append(i)

    for i in items:
        i["section"] = section_of(i)
    for root, g in members.items():
        cov = {s for i in g for s in [i["source"], *i["sources"]]}
        for i in g:
            age_h = 48 if i.get("undated") else (NOW - datetime.strptime(i["published"], "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)).total_seconds() / 3600
            txt = f"{i['title']} {i['summary']}".lower()
            sev = 4 if i["source"] == "CISA KEV" or re.search(r"zero[- ]day|0-day|exploited", txt) else (2 if i["security"] else 0)
            citrix = 5 if {"citrix", "netscaler"} & set(i["vendors"]) else 0  # Steve: boost Citrix highest
            trust = 1.5 if any(t in i["source"].lower() for t in TRUSTED) else 0
            i["score"] = round(10 * 0.5 ** (age_h / 36) + 1.2 * len(i["vendors"][:3]) + 2 * min(len(cov) - 1, 4)
                               + sev + trust + citrix + (1 if i["image"] else 0), 2)
        g.sort(key=lambda i: (i["score"], bool(i["image"])), reverse=True)
        lead = g[0]
        for i in g:  # one cluster -> one section (the lead's)
            i["cluster"] = lead["id"]
            i["section"] = lead["section"]

    # --- Cap Security ---
    sec_clusters = sorted({i["cluster"] for i in items if i["section"] == "security"},
                          key=lambda c: -max(x["score"] for x in items if x["cluster"] == c))
    drop = set(sec_clusters[SEC_CAP:])
    stats["security_clusters_capped"] = len(drop)
    items = [i for i in items if i["cluster"] not in drop]
    stats["total"] = len(items)
    stats["security_items"] = sum(1 for i in items if i["section"] == "security")
    stats["clusters"] = len({i["cluster"] for i in items})
    print("stats:", stats)

    out = {"updated": NOW.strftime("%Y-%m-%dT%H:%M:%SZ"), "window_days": WINDOW_DAYS,
           "count": len(items), "stats": stats, "feeds": status, "x_accounts": cfg.get("x_accounts", []),
           "items": items}
    DATA.parent.mkdir(parents=True, exist_ok=True)
    DATA.write_text(json.dumps(out, indent=1, ensure_ascii=False))
    ok = sum(s["ok"] for s in status)
    print(f"\n{len(items)} items in window; feeds ok {ok}/{len(status)}")
    if ok == 0:
        sys.exit(1)


if __name__ == "__main__":
    main()
