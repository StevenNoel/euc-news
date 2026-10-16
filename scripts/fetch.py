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
    ("endpoints", {"igel", "controlup", "unicon", "intune", "jamf", "patchmypc", "uem"}),
    ("cloud", {"m365", "azure-local"}),
    ("windows", {"windows", "patch", "microsoft"}),
]
VENDOR_SOURCES = ("cisa", "msrc", "microsoft", "citrix", "omnissa", "broadcom", "vmware", "nutanix", "igel", "controlup", "carl stalhood")


def trusted_outlet(src):
    """Independent news outlets only (vendor blogs don't count) - used for Top Stories filler."""
    s = src.lower()
    return any(t in s for t in TRUSTED) and not any(v in s for v in VENDOR_SOURCES)


AI_TAGS = ["grok", "claude", "openai", "gemini", "huggingface", "local-llm"]
EUC_TAGS = {"citrix", "netscaler", "xenserver", "vmware", "omnissa", "nutanix", "avd", "w365", "fslogix",
            "m365", "azure-local", "intune", "igel", "controlup", "unicon", "jamf", "patchmypc", "uem"}
AI_PENALTY = 4  # keep AI moderate in Top Stories; Citrix boost stays highest
VENDOR_TAGS = AI_TAGS + ["citrix", "netscaler", "xenserver", "vmware", "omnissa", "nutanix", "avd", "w365", "fslogix",
               "microsoft", "windows", "m365", "azure-local", "intune", "jamf", "patchmypc", "uem", "igel", "controlup", "unicon"]
TRUSTED = ("bleeping", "neowin", "windows central", "the verge", "zdnet", "techcrunch", "infoworld", "computerworld",
           "windows latest", "gizmodo", "9to5google", "thurrott", "petri", "itpro", "techradar", "theregister", "cyberscoop", "dark reading",
           "the hacker news", "reuters", "bloomberg", "cnbc", "wired", "help net security", "securityweek", "cybersecurity dive", "techtarget", "crn",
           "cisa", "msrc", "microsoft", "bleepingcomputer", "the register", "born", "citrix", "omnissa",
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


WIRE_RE = re.compile(r"^\s*(?:\[(?:pr ?newswire|business ?wire|globe ?newswire|accesswire|newsfile|eqs)[^\]]*\]|(?:eqs-news|eqs-adhoc|pr newswire|business wire)\s*[:\-–])\s*", re.I)


def clean_title(t):
    return WIRE_RE.sub("", t or "").strip()


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
    if it.get("kind") == "cve":
        return "cves"
    if it.get("kind") == "hardening":
        return "security"
    if ("ai" in it["tags"] or set(AI_TAGS) & set(it["tags"])) and not (EUC_TAGS & set(it["tags"])):
        return "ai"
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


SEC_RE = re.compile(r"zero[- ]day|0-day|actively exploited|exploited|in the wild|critical|cvss(?: score)?(?: of)?:? ?(?:9(?:\.\d)?|10(?:\.0)?)\b|ransomware|breach|\brce\b|remote code execution|emergency (?:fix|patch)", re.I)
CVE_RE = re.compile(r"CVE-\d{4}-\d{4,7}", re.I)
KB_RE = re.compile(r"\bKB\d{6,8}\b", re.I)
STOP = set("a an the and or of for to in on at by with from as is are was were be been it its this that these those how why what who new now after over into about via vs your you our we their his her not can will has have had more than out up off".split())
CVE_CAP = 40         # CVE & breach items kept for the rail
TOP_N, TOP_PER_VENDOR, TOP_PER_SECTION = 20, 3, 5  # 8 cards + compact list
CVE_KIND_RE = re.compile(r"\bcve-\d{4}-\d+|zero[- ]day|0-day|actively exploited|exploited in the wild|\bkev\b|data breach|breach(?:ed)?\b|ransomware|"
                         r"\bvulnerabilit(?:y|ies)\b|security flaw|critical flaw|reason to patch|urged? to patch|patch now|\bflaws\b|new flaw|\brce\b|remote code execution|security (?:update|patch|bulletin|advisory)", re.I)
HARDEN_RE = re.compile(r"harden|best practice|zero[- ]trust|\bmfa\b|multi-factor|phishing-resistant|passkey|conditional access|least privilege|"
                       r"security baseline|cis benchmark|attack surface|secure (?:your|the|citrix|netscaler|vdi|avd|microsoft 365|m365|windows|endpoints?|access)|"
                       r"identity (?:protection|security)|privileged access|defender|entra|\bxdr\b|\bedr\b|\bsase\b|\bztna\b", re.I)


def title_tokens(t):
    t = re.sub(r"\s+[-|–]\s+[^-|–]{2,60}$", "", t or "")
    return {w for w in re.sub(r"[^a-z0-9]+", " ", t.lower()).split() if w not in STOP and len(w) > 1}


def proper_nouns(t):
    """Capitalized words/product names after the first word (lowercased), e.g. 'NetScaler', 'Gemini'."""
    t = re.sub(r"\s+[-|–]\s+[^-|–]{2,60}$", "", t or "")
    words = re.findall(r"[A-Za-z][A-Za-z0-9.+]*", t)[1:]
    return {w.lower() for w in words if (w[0].isupper() or any(c.isdigit() for c in w)) and w.lower() not in STOP and len(w) > 2}


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
        text = f"{title} {summary}"
        if feed.get("exclude_regex") and re.search(feed["exclude_regex"], text, re.I):
            continue
        if feed.get("require_regex") and not re.search(feed["require_regex"], text, re.I):
            continue
        if feed.get("title_prefix") and not title.lower().startswith(feed["title_prefix"].lower()):
            title = f"{feed['title_prefix']} {title}"
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
                        outage=bool(feed.get("outage")), image=entry_thumb(e), feed=feed["name"]))
    return out


def fetch_aem_index(feed, tag_map):
    """AEM Edge Delivery sites (citrix.com) have no RSS; read the public query-index.json,
    take the newest paths under path_prefix, and read published-time / og:image from each page."""
    data = get(feed["url"]).json().get("data", [])
    base = "{0.scheme}://{0.netloc}".format(urlparse(feed["url"]))
    rows = sorted((d for d in data if d.get("path", "").startswith(feed.get("path_prefix", "/"))
                   and re.search(feed.get("path_regex", ""), d.get("path", ""))),
                  key=lambda d: int(d.get("lastModified") or 0), reverse=True)[: int(feed.get("max_pages", 15))]
    out = []
    for d in rows:
        link = base + d["path"]
        try:
            html = requests.get(link, headers={"User-Agent": BROWSER_UA}, timeout=10).text
        except Exception:
            continue
        m = re.search(r'<meta name="published-time" content="([^"]+)"', html)
        img = re.search(r'<meta property="og:image" content="([^"]+)"', html)
        try:
            dt = datetime.fromisoformat(m.group(1).replace("Z", "+00:00")) if m else None
        except Exception:
            dt = None
        if not dt or dt < CUTOFF:
            continue
        title = (d.get("title") or "").strip()
        summary = strip_html(d.get("description") or "")
        matched = tag_text(f"{title} {summary}", tag_map)
        out.append(dict(title=title, link=link, summary=summary, source=feed.get("source", urlparse(base).netloc.removeprefix("www.")),
                        published=dt, undated=False, tags=set(feed.get("tags", [])) | matched, security=False, outage=False,
                        image=img.group(1).replace("&#x26;", "&") if img else None, feed=feed["name"]))
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


NS_BUILD = re.compile(r"(\d+\.\d+)(?:\s+Build\s+|-)(\d+\.\d+)", re.I)
NS_FMT = re.compile(r"\b(VPX|CPX|BLX|SDX|MPX|FIPS|NDcPP)\b", re.I)


def fetch_releases(rc):
    """Citrix download RSS -> merged, newest first; NetScaler per-format duplicates collapsed."""
    rows = {}
    for feed in rc.get("feeds", []):
        try:
            r = requests.get(feed["url"], headers={"User-Agent": BROWSER_UA}, timeout=20)
            r.raise_for_status()
            entries = feedparser.parse(r.content).entries
        except Exception as ex:
            print(f"  releases FAIL {feed['name']}: {ex}")
            continue
        print(f"  releases ok {feed['name']}: {len(entries)}")
        for e in entries:
            title = re.sub(r"^\s*New\s*-\s*", "", e.get("title", "")).strip()
            dt = to_dt(e)
            if not title or not dt:
                continue
            link = e.get("link", "").replace("http://", "https://", 1)
            fmt, key = None, title.lower()
            m = NS_BUILD.search(title)
            if feed["name"] == "NetScaler" and m:
                fm = NS_FMT.search(title)
                fmt = fm.group(1).upper() if fm else "Firmware"
                phase = re.search(r"\(([^)]*)\)", title)
                key = "ns:" + m.group(1) + ":" + m.group(2) + (":fips" if fmt in ("FIPS", "NDCPP") else "")
                title = f"NetScaler {m.group(1)} Build {m.group(2)}" + (" FIPS" if fmt in ("FIPS", "NDCPP") else "") + (f" ({phase.group(1)})" if phase else "")
            row = rows.get(key)
            if not row:
                row = rows[key] = {"title": title, "product": feed["name"], "link": link,
                                   "published": dt.strftime("%Y-%m-%dT%H:%M:%SZ"), "formats": []}
            if fmt and fmt not in row["formats"]:
                row["formats"].append(fmt)
                if fmt == "Firmware":
                    row["link"] = link
    out = sorted(rows.values(), key=lambda x: x["published"], reverse=True)
    return out[: int(rc.get("keep", 30))]


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
                     fetch_msrc(feed, CUTOFF) if feed["type"] == "msrc" else
                     fetch_aem_index(feed, tag_map) if feed["type"] == "aem_index" else fetch_rss(feed, tag_map))
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
    # Citrix download pages live only in the Citrix Releases panel
    items = [i for i in items if "citrix.com/downloads/" not in i.get("link", "")]
    stats["in_window_before_dedupe"] = len(items)

    # --- Classify: CVE/breach rail vs. security hardening vs. topic sections ---
    feeds_cfg = {f["name"]: f for f in cfg.get("feeds", [])}
    kept = []
    for i in items:
        i.pop("category", None); i.pop("score", None)
        for k in ("image",):
            i.setdefault(k, None)
        i.setdefault("sources", []); i.setdefault("related", [])
        i["tags"] = sorted(set(i["tags"]) - {"security"})
        i["title"] = clean_title(i["title"])
        if i["source"] == "MSRC Security Update Guide":  # legacy RSS items from cache
            continue
        f = feeds_cfg.get(i.get("feed"), {})
        txt = f"{i['title']} {i['summary']}"
        i["notop"] = (bool(f.get("no_top")) or i["source"] in ("CISA KEV", "MSRC")
                      or bool(re.search(r"newswire|press release|marketscreener|globenewswire|business wire|\bpr\b", i["source"], re.I))
                      or bool(re.match(r"\[(pr ?newswire|business ?wire|globe ?newswire)\]", i["title"], re.I))
                      or not re.fullmatch(r"[\x00-\u024f\u2000-\u206f\u2122\u00ae]*", i["title"]))
        i["kind"] = ""
        i["vblog"] = bool(f.get("security_vendor"))
        if i.get("outage"):
            pass
        elif i["source"] in ("CISA KEV", "MSRC") or CVE_KIND_RE.search(txt):
            i["kind"] = "cve"
        elif not f.get("no_top") and not f.get("product_blog") and (f.get("security_vendor") or f.get("hardening") or HARDEN_RE.search(i["title"])):
            i["kind"] = "hardening"
        i["security"] = bool(i["kind"])
        i["vendors"] = [t for t in VENDOR_TAGS if t in i["tags"]]
        if not (i.get("outage") or i["kind"] or i["vendors"]):
            continue  # nothing EUC-relevant
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
    ents = [proper_nouns(i["title"]) | (set(i["vendors"]) - {"microsoft", "windows"}) for i in items]
    uf = UF(len(items))
    for x in range(len(items)):
        for y in range(x + 1, len(items)):
            cx, cy = {k for k in keys[x] if k.startswith("CVE")}, {k for k in keys[y] if k.startswith("CVE")}
            if cx and cy and not (cx & cy):
                continue  # different CVEs are different stories even if titles look alike
            j = jacc(toks[x], toks[y])
            # 0.5 overlap, or 0.4 overlap plus a shared entity (product/vendor/proper noun)
            if (keys[x] & keys[y]) or j >= 0.5 or (j >= 0.4 and ents[x] & ents[y]):
                uf.union(x, y)
    members = {}
    for n, i in enumerate(items):
        members.setdefault(uf.find(n), []).append(i)

    for i in items:
        i["section"] = section_of(i)
    for root, g in members.items():
        cov = {s for i in g for s in [i["source"], *i["sources"]]}
        for i in g:
            i["_age"] = 48 if i.get("undated") else (NOW - datetime.strptime(i["published"], "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)).total_seconds() / 3600
            trust = 1.5 if any(t in i["source"].lower() for t in TRUSTED) else 0
            citrix = 1 if {"citrix", "netscaler"} & set(i["vendors"]) else 0  # small EUC tie-breaker only
            # section ranking: recency + coverage + authority (no big vendor boosts)
            i["score"] = round(10 * 0.5 ** (i["_age"] / 36) + 2 * min(len(cov) - 1, 5) + trust + citrix
                               + 0.8 * len(i["vendors"][:2]) + (1 if i["image"] else 0)
                               - (AI_PENALTY if i["section"] == "ai" else 0), 2)
        g.sort(key=lambda i: (i["score"], bool(i["image"]), not i["notop"]), reverse=True)
        lead = g[0]
        for i in g:  # one cluster -> one section (the lead's); cluster coverage on every member
            i["cluster"] = lead["id"]
            i["section"] = lead["section"]
            i["n_sources"] = len(cov)

    # --- Per-feed window caps (AI feeds default 12; any feed can set window_cap) ---
    caps = {f["name"]: f.get("window_cap", f.get("max_items", 12)) for f in cfg.get("feeds", [])
            if "ai" in f.get("tags", []) or "window_cap" in f}
    per_feed, kept = {}, []
    for i in sorted(items, key=lambda i: i["published"], reverse=True):
        f = i.get("feed")
        if f in caps:
            per_feed[f] = per_feed.get(f, 0) + 1
            if per_feed[f] > caps[f]:
                continue
        kept.append(i)
    stats["feed_capped"] = len(items) - len(kept)
    items = kept

    # --- CVE & breach rail: newest high-impact, capped ---
    cves = sorted([i for i in items if i["section"] == "cves"], key=lambda i: i["published"], reverse=True)
    drop = {i["id"] for i in cves[CVE_CAP:]}
    items = [i for i in items if i["id"] not in drop]
    for i in items:
        if i["section"] == "cves":
            t = f"{i['title']} {i['summary']}".lower()
            i["badge"] = ("KEV" if i["source"] == "CISA KEV" else
                          "Zero-day" if re.search(r"zero[- ]day|0-day", t) else
                          "Exploited" if "exploited" in t or "in the wild" in t else
                          "Breach" if re.search(r"breach|ransomware|leak", t) else
                          "Critical" if "critical" in t or re.search(r"cvss:? ?(9|10)", t) else "High")

    # --- Top Stories: multi-publisher coverage first, recency decay, authority; diversity caps ---
    leads = {}
    for i in items:
        if i["cluster"] == i["id"]:
            leads[i["id"]] = i
    def top_score(i):
        trust = 2 if any(t in i["source"].lower() for t in TRUSTED) else 0
        return 4 * min(i["n_sources"] - 1, 6) + 8 * 0.5 ** (i["_age"] / 24) + trust + (0.5 if i["image"] else 0)
    elig = [i for i in leads.values() if not i["notop"] and i["section"] not in ("cves", "outages")
            and i["_age"] <= 168 and not i.get("undated")]
    for i in elig:
        i["top_score"] = round(top_score(i), 2)
    multi = sorted([i for i in elig if i["n_sources"] >= 2], key=lambda i: -i["top_score"])
    fallback = sorted([i for i in elig if i["n_sources"] < 2 and not i.get("vblog") and trusted_outlet(i["source"])],
                      key=lambda i: i["published"], reverse=True)
    top, per_v, per_s, filler_src = [], {}, {}, set()
    for i in multi + fallback:
        if i["n_sources"] < 2:  # at most 1 filler story per source
            if i["source"] in filler_src:
                continue
        v = (i["vendors"] or ["_none"])[0]
        if per_v.get(v, 0) >= TOP_PER_VENDOR or per_s.get(i["section"], 0) >= (2 if i["section"] == "ai" else TOP_PER_SECTION):
            continue
        per_v[v] = per_v.get(v, 0) + 1; per_s[i["section"]] = per_s.get(i["section"], 0) + 1
        top.append(i["id"])
        if i["n_sources"] < 2:
            filler_src.add(i["source"])
        if len(top) >= TOP_N:
            break
    for i in items:
        i.pop("_age", None)
    stats["top_multi_source"] = sum(1 for t in top if leads[t]["n_sources"] >= 2)
    stats["total"] = len(items)
    stats["security_items"] = sum(1 for i in items if i["section"] == "security")
    stats["cve_items"] = sum(1 for i in items if i["section"] == "cves")
    stats["clusters"] = len({i["cluster"] for i in items})
    print("stats:", stats)

    releases = fetch_releases(cfg.get("citrix_releases", {}))
    print(f"releases: {len(releases)}")
    out = {"updated": NOW.strftime("%Y-%m-%dT%H:%M:%SZ"), "window_days": WINDOW_DAYS,
           "count": len(items), "stats": stats, "feeds": status, "top": top,
           "items": items, "releases": releases,
           "releases_show": int(cfg.get("citrix_releases", {}).get("show", 10))}
    DATA.parent.mkdir(parents=True, exist_ok=True)
    DATA.write_text(json.dumps(out, indent=1, ensure_ascii=False))
    ok = sum(s["ok"] for s in status)
    print(f"\n{len(items)} items in window; feeds ok {ok}/{len(status)}")
    if ok == 0:
        sys.exit(1)


if __name__ == "__main__":
    main()
