(() => {
  const $ = (s) => document.querySelector(s);
  const SECTIONS = [
    { id: "top", name: "Top Stories" },
    { id: "security", name: "Security & Hardening", short: "Security", c: "#c24a3f" },
    { id: "virtualization", name: "Virtualization & VDI", short: "Virtualization", c: "#2f66c4" },
    { id: "windows", name: "Microsoft & Windows", short: "Windows", c: "#2b8a9e" },
    { id: "cloud", name: "Cloud & Microsoft 365", short: "Cloud & M365", c: "#7b5cc4" },
    { id: "endpoints", name: "Endpoints & Management", short: "Endpoints", c: "#3d8a55" },
    { id: "ai", name: "AI & Local LLMs", short: "AI", c: "#b0567f" },
  ];
  const VENDORS = { citrix: "Citrix", netscaler: "NetScaler", xenserver: "XenServer", vmware: "VMware", omnissa: "Omnissa",
    nutanix: "Nutanix", avd: "AVD", w365: "Windows 365", fslogix: "FSLogix", microsoft: "Microsoft", windows: "Windows",
    m365: "M365", "azure-local": "Azure Local", intune: "Intune", igel: "IGEL", controlup: "ControlUp", unicon: "Unicon",
    grok: "Grok", claude: "Claude", openai: "OpenAI", gemini: "Gemini", huggingface: "Hugging Face", "local-llm": "Local LLM" };
  const st = { all: [], rail: { cves: [], outages: [] }, top: [], tab: "top", q: "", vendor: "" };
  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const safe = (u) => (/^https?:\/\//i.test(u || "") ? u : "");
  const ago = (iso) => { const s = (Date.now() - new Date(iso)) / 1e3;
    return s < 3600 ? Math.max(1, s / 60 | 0) + " min ago" : s < 86400 ? (s / 3600 | 0) + " hr ago" : s < 172800 ? "Yesterday" : (s / 86400 | 0) + " days ago"; };
  const secOf = (id) => SECTIONS.find((s) => s.id === id) || SECTIONS[1];
  const abbr = (it) => (VENDORS[it.vendors[0]] || secOf(it.section).short || "EUC").slice(0, 10);
  const href = (u) => esc(safe(u) || "#");
  const link = (it, cls, inner) => `<a class="${cls}" href="${href(it.link)}" target="_blank" rel="noopener noreferrer" style="--sc:${secOf(it.section).c}">${inner}</a>`;
  const img = (it) => `<div class="img" aria-hidden="true"><span>${esc(abbr(it))}</span>${safe(it.image) ? `<img src="${esc(it.image)}" alt="" loading="lazy" decoding="async" referrerpolicy="no-referrer" onerror="this.remove()">` : ""}</div>`;
  const tags = (it) => it.vendors.slice(0, 3).map((v) => `<span class="vtag">${esc(VENDORS[v] || v)}</span>`).join("");
  const nsrc = (it) => (it.n_sources > 1 ? `<span class="nsrc">${it.n_sources} sources</span>` : "");
  const meta = (it) => `<div class="meta"><span class="src">${esc(it.source)}</span><span>·</span><time datetime="${esc(it.published)}" title="${esc(new Date(it.published).toLocaleString())}">${ago(it.published)}</time>${nsrc(it)}${tags(it)}</div>`;
  const related = (rel) => rel.length ? `<ul class="related" aria-label="Related coverage">${rel.slice(0, 3).map((r) =>
    `<li><a href="${href(r.link)}" target="_blank" rel="noopener noreferrer"><b>${esc(r.source)}</b>${esc(r.title)}</a></li>`).join("")}</ul>` : "";
  const relOf = (g) => [...g.rel, ...(g.lead.related || [])];
  const big = (g) => link(g.lead, "big", `${img(g.lead)}<h3>${esc(g.lead.title)}</h3>${g.lead.summary ? `<p>${esc(g.lead.summary)}</p>` : ""}${meta(g.lead)}`) + related(relOf(g));
  const med = (g) => `<div>${link(g.lead, "med", `<div><h3>${esc(g.lead.title)}</h3>${meta(g.lead)}</div>${img(g.lead)}`)}${related(relOf(g))}</div>`;
  const crow = (g) => `<li><a href="${href(g.lead.link)}" target="_blank" rel="noopener noreferrer"><span class="ct">${esc(g.lead.title)}</span>
    <span class="cm">${esc(g.lead.source)} · ${ago(g.lead.published)}${g.lead.n_sources > 1 ? ` · ${g.lead.n_sources} sources` : ""}${g.lead.vendors[0] ? ` <span class="vtag">${esc(VENDORS[g.lead.vendors[0]] || "")}</span>` : ""}</span></a></li>`;
  const compact = (gs) => gs.length ? `<ul class="compact" aria-label="More headlines">${gs.map(crow).join("")}</ul>` : "";
  const row = (g) => link(g.lead, "row", `<div><h4>${esc(g.lead.title)}</h4>${meta(g.lead)}</div>${img(g.lead)}`);

  const match = (it) => { const q = st.q.toLowerCase();
    return (!st.vendor || it.vendors.includes(st.vendor)) && (!q || `${it.title} ${it.summary} ${it.source} ${it.vendors.join(" ")}`.toLowerCase().includes(q)); };
  // group items by cluster; the cluster lead is the item whose id == cluster
  function clusters(items) {
    const m = new Map();
    for (const it of items) { const k = it.cluster || it.id; (m.get(k) || m.set(k, []).get(k)).push(it); }
    return [...m.values()].map((g) => { g.sort((a, b) => (b.id === b.cluster) - (a.id === a.cluster) || b.score - a.score); return { lead: g[0], rel: g.slice(1) }; });
  }
  const pickLead = (gs) => { const i = gs.findIndex((g) => g.lead.image); return i > 0 ? [gs[i], ...gs.slice(0, i), ...gs.slice(i + 1)] : gs; };

  function topStories(items, used) {
    const byCluster = new Map(clusters(items).map((g) => [g.lead.cluster || g.lead.id, g]));
    const ranked = clusters(items).filter((g) => !["cves", "outages"].includes(g.lead.section)).sort((a, b) => b.lead.score - a.lead.score);
    let gs = st.top.map((id) => byCluster.get(id)).filter(Boolean);
    if (st.q || st.vendor) gs = ranked.slice(0, 20);
    if (!gs.length) return `<section class="sect"><p class="empty">No stories match.</p></section>`;
    gs = pickLead(gs);
    gs.forEach((g) => used.add(g.lead.cluster || g.lead.id));
    const [hero, ...rest] = gs;
    const meds = rest.slice(0, 4), row3 = rest.slice(4, 7);
    // compact list under the hero: remaining top picks, then next-ranked stories not shown anywhere yet
    const shownIds = new Set([hero, ...meds, ...row3].map((g) => g.lead.cluster || g.lead.id));
    const pool = [...rest.slice(7), ...ranked].filter((g) => { const k = g.lead.cluster || g.lead.id; if (shownIds.has(k)) return false; shownIds.add(k); return true; }).slice(0, 12);
    pool.forEach((g) => used.add(g.lead.cluster || g.lead.id));
    return `<section class="sect" id="s-top"><div class="sect-h"><h2>Top stories</h2></div>
      <div class="hero fit"><div class="leads">${big(hero)}${compact(pool)}</div><div class="meds">${meds.map(med).join("")}</div></div>
      ${row3.length ? `<div class="more-top">${row3.map((g) => link(g.lead, "big", `${img(g.lead)}<h3>${esc(g.lead.title)}</h3>${meta(g.lead)}`)).join("")}</div>` : ""}</section>`;
  }
  function sectionBlock(s, items, full, used = new Set()) {
    let gs = clusters(items.filter((it) => it.section === s.id)).filter((g) => !used.has(g.lead.cluster || g.lead.id));
    const recent = gs.filter((g) => Date.now() - new Date(g.lead.published) < 7 * 864e5);
    gs = (recent.length >= 4 ? recent : gs).sort((a, b) => b.lead.score - a.lead.score);
    if (!gs.length) return full ? `<section class="sect" style="--sc:${s.c}"><div class="sect-h"><h2>${s.name}</h2></div><p class="empty">No stories match.</p></section>` : "";
    gs = pickLead(gs);
    const nLead = full ? 2 : 1, leads = gs.slice(0, nLead);
    const nRows = full ? 30 : 6;
    const rest = gs.slice(nLead, nLead + nRows), pool = gs.slice(nLead + nRows, nLead + nRows + (full ? 30 : 10));
    if (full) rest.sort((a, b) => new Date(b.lead.published) - new Date(a.lead.published));
    [...leads, ...rest, ...pool].forEach((g) => used.add(g.lead.cluster || g.lead.id));
    return `<section class="sect" id="s-${s.id}" style="--sc:${s.c}">
      <div class="sect-h"><h2>${s.name}</h2>${full ? "" : `<button class="more" data-tab="${s.id}">More ${esc(s.short)} ›</button>`}</div>
      <div class="sect-body fit"><div class="leads">${leads.map(big).join("")}${compact(pool)}</div><div class="list">${rest.map(row).join("")}</div></div></section>`;
  }
  function rail(id, title, dotCls, note, n, badge) {
    const list = st.rail[id].filter(match).sort((a, b) => new Date(b.published) - new Date(a.published)).slice(0, n);
    $("#" + id).innerHTML = `<h2><span class="dot ${dotCls}" aria-hidden="true"></span>${title}</h2>` +
      (list.length ? `<ul class="mini">${list.map((it) => `<li><a href="${href(it.link)}" target="_blank" rel="noopener noreferrer">${badge && it.badge ? `<span class="badge b-${esc(it.badge.toLowerCase())}">${esc(it.badge)}</span>` : ""}<span class="t">${esc(it.title)}</span><span class="m">${esc(it.source)} · ${ago(it.published)}</span></a></li>`).join("")}</ul>`
        : `<p class="empty">Nothing recent.</p>`) + `<p class="note">${note}</p>`;
  }
  function render() {
    const items = st.all.filter(match), used = new Set();
    let html;
    if (st.tab === "top") { const top = topStories(items, used); html = top + SECTIONS.slice(1).map((s) => sectionBlock(s, items, false, used)).join(""); }
    else html = sectionBlock(secOf(st.tab), items, true);
    $("#main").innerHTML = html;
    rail("cves", "Top CVEs &amp; breaches", "red", "CISA KEV (tracked vendors), critical/exploited MSRC CVEs, zero-days and major breaches.", 10, true);
    rail("outages", "Outages &amp; status", "", "Official status feeds (AWS, Azure, Google Cloud, Cloudflare, Microsoft 365) and outage news.", 8, false);
    document.querySelectorAll(".tab").forEach((t) => t.setAttribute("aria-selected", t.dataset.tab === st.tab));
    fit();
  }
  // Balance columns: show compact headlines under the featured card until the left column
  // reaches the right column's height. When columns stack (mobile), show a short list.
  function fit() {
    document.querySelectorAll(".fit").forEach((box) => {
      const left = box.querySelector(".leads"), right = left.nextElementSibling, lis = [...left.querySelectorAll(".compact li")];
      if (!lis.length) return;
      lis.forEach((li) => (li.hidden = false));
      const stacked = right.getBoundingClientRect().top >= left.getBoundingClientRect().bottom - 1;
      if (stacked) { lis.forEach((li, n) => (li.hidden = n >= 4)); }
      else { for (let n = lis.length - 1; n >= 0 && left.offsetHeight > right.offsetHeight + 4; n--) lis[n].hidden = true; }
      const ul = left.querySelector(".compact"); ul.hidden = lis.every((li) => li.hidden);
    });
  }
  let rt; addEventListener("resize", () => { clearTimeout(rt); rt = setTimeout(fit, 120); });
  addEventListener("load", fit);
  function setTab(t, scroll = true) {
    st.tab = t; render();
    if (scroll) window.scrollTo({ top: 0, behavior: "smooth" });
    history.replaceState(null, "", t === "top" ? location.pathname : "#" + t);
  }
  $("#tabs").innerHTML = SECTIONS.map((s) => `<button class="tab" role="tab" data-tab="${s.id}">${esc(s.short || s.name)}</button>`).join("");
  document.addEventListener("click", (e) => { const b = e.target.closest("[data-tab]"); if (b) { e.preventDefault(); setTab(b.dataset.tab); } });
  $("#chips").addEventListener("click", (e) => { const b = e.target.closest(".chip"); if (!b) return;
    st.vendor = st.vendor === b.dataset.v ? "" : b.dataset.v; chips(); render(); });
  function chips() {
    const n = {}; st.all.forEach((it) => it.vendors.forEach((v) => (n[v] = (n[v] || 0) + 1)));
    $("#chips").innerHTML = `<button class="chip" data-v="" aria-pressed="${!st.vendor}">All vendors</button>` +
      Object.keys(VENDORS).filter((v) => n[v]).map((v) => `<button class="chip" data-v="${v}" aria-pressed="${st.vendor === v}">${VENDORS[v]}</button>`).join("");
  }
  let t; $("#q").addEventListener("input", (e) => { clearTimeout(t); t = setTimeout(() => { st.q = e.target.value.trim(); render(); }, 160); });
  $("#theme").addEventListener("click", () => {
    const r = document.documentElement, cur = r.dataset.theme === "auto" ? (matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light") : r.dataset.theme;
    r.dataset.theme = cur === "dark" ? "light" : "dark"; try { localStorage.setItem("theme", r.dataset.theme); } catch (e) {}
  });
  fetch("data/items.json", { cache: "no-cache" }).then((r) => r.json()).then((d) => {
    const items = d.items || [];
    st.rail.cves = items.filter((i) => i.section === "cves");
    st.rail.outages = items.filter((i) => i.section === "outages");
    st.all = items.filter((i) => i.section !== "cves" && i.section !== "outages");
    st.top = d.top || [];
    $("#updated").textContent = `Updated ${ago(d.updated)} · ${new Date(d.updated).toLocaleString([], { dateStyle: "medium", timeStyle: "short" })}`;
    chips();
    const h = location.hash.slice(1);
    setTab(SECTIONS.some((s) => s.id === h) ? h : "top", false);
  }).catch(() => { $("#updated").textContent = "Couldn't load the news data."; });
})();
