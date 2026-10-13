(() => {
  const $ = (s) => document.querySelector(s);
  const SECTIONS = [
    { id: "top", name: "Top Stories" },
    { id: "security", name: "Security & Vulnerabilities", short: "Security", c: "#c24a3f" },
    { id: "virtualization", name: "Virtualization & VDI", short: "Virtualization", c: "#2f66c4" },
    { id: "windows", name: "Microsoft & Windows", short: "Windows", c: "#2b8a9e" },
    { id: "cloud", name: "Cloud & Microsoft 365", short: "Cloud & M365", c: "#7b5cc4" },
    { id: "endpoints", name: "Endpoints & Management", short: "Endpoints", c: "#3d8a55" },
    { id: "community", name: "Community / X", short: "Community" },
  ];
  const VENDORS = { citrix: "Citrix", netscaler: "NetScaler", xenserver: "XenServer", vmware: "VMware", omnissa: "Omnissa",
    nutanix: "Nutanix", avd: "AVD", w365: "Windows 365", fslogix: "FSLogix", microsoft: "Microsoft", windows: "Windows",
    m365: "M365", "azure-local": "Azure Local", intune: "Intune", igel: "IGEL", controlup: "ControlUp", unicon: "Unicon" };
  const st = { all: [], outages: [], x: [], tab: "top", q: "", vendor: "" };
  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const safe = (u) => (/^https?:\/\//i.test(u || "") ? u : "");
  const ago = (iso) => { const s = (Date.now() - new Date(iso)) / 1e3;
    return s < 3600 ? Math.max(1, s / 60 | 0) + " min ago" : s < 86400 ? (s / 3600 | 0) + " hr ago" : s < 172800 ? "Yesterday" : (s / 86400 | 0) + " days ago"; };
  const secOf = (id) => SECTIONS.find((s) => s.id === id) || SECTIONS[1];
  const abbr = (it) => (VENDORS[it.vendors[0]] || secOf(it.section).short || "EUC").slice(0, 10);
  const link = (it, cls, inner) => `<a class="${cls}" href="${esc(safe(it.link) || "#")}" target="_blank" rel="noopener noreferrer" style="--sc:${secOf(it.section).c}">${inner}</a>`;
  const img = (it) => `<div class="img" aria-hidden="true"><span>${esc(abbr(it))}</span>${safe(it.image) ? `<img src="${esc(it.image)}" alt="" loading="lazy" decoding="async" referrerpolicy="no-referrer" onerror="this.remove()">` : ""}</div>`;
  const tags = (it) => it.vendors.slice(0, 3).map((v) => `<span class="vtag${v === "citrix" || v === "netscaler" ? "" : ""}">${esc(VENDORS[v] || v)}</span>`).join("") +
    (it.security && /critical|zero-day|0-day|exploit/i.test(it.title) ? `<span class="vtag hot">Exploited/critical</span>` : "");
  const meta = (it) => `<div class="meta"><span class="src">${esc(it.source)}</span><span>·</span><time datetime="${esc(it.published)}" title="${esc(new Date(it.published).toLocaleString())}">${ago(it.published)}</time>${tags(it)}</div>`;
  const big = (it, rel = []) => link(it, "big", `${img(it)}<h3>${esc(it.title)}</h3>${it.summary ? `<p>${esc(it.summary)}</p>` : ""}${meta(it)}`) + related(rel);
  const med = (it, rel = []) => `<div>${link(it, "med", `<div><h3>${esc(it.title)}</h3>${meta(it)}</div>${img(it)}`)}${related(rel)}</div>`;
  const row = (it) => link(it, "row", `<div><h4>${esc(it.title)}</h4>${meta(it)}</div>${img(it)}`);
  const related = (rel) => rel.length ? `<ul class="related" aria-label="Related coverage">${rel.slice(0, 3).map((r) =>
    `<li><a href="${esc(safe(r.link) || "#")}" target="_blank" rel="noopener noreferrer"><b>${esc(r.source)}</b>${esc(r.title)}</a></li>`).join("")}</ul>` : "";

  function visible() {
    const q = st.q.toLowerCase();
    return st.all.filter((it) => (!st.vendor || it.vendors.includes(st.vendor)) &&
      (!q || `${it.title} ${it.summary} ${it.source} ${it.vendors.join(" ")}`.toLowerCase().includes(q)));
  }
  // One entry per cluster: the best-scored item leads, the rest become related coverage.
  function clusters(items) {
    const m = new Map();
    for (const it of items) { const k = it.cluster || it.id; (m.get(k) || m.set(k, []).get(k)).push(it); }
    return [...m.values()].map((g) => { g.sort((a, b) => b.score - a.score || (b.image ? 1 : 0) - (a.image ? 1 : 0)); return { lead: g[0], rel: g.slice(1), score: g[0].score }; });
  }
  const byScore = (a, b) => b.score - a.score;
  const byDate = (a, b) => new Date(b.lead.published) - new Date(a.lead.published);
  const pickLead = (gs) => { const i = gs.findIndex((g) => g.lead.image); return i > 0 ? [gs[i], ...gs.slice(0, i), ...gs.slice(i + 1)] : gs; };

  function sectionBlock(s, items, full, used = new Set()) {
    let gs = clusters(items.filter((it) => it.section === s.id)).filter((g) => !used.has(g.lead.cluster || g.lead.id));
    const recent = gs.filter((g) => Date.now() - new Date(g.lead.published) < 7 * 864e5);
    gs = (recent.length >= 4 ? recent : gs).sort(byScore);
    if (!gs.length) return full ? `<section class="sect" style="--sc:${s.c}"><div class="sect-h"><h2>${s.name}</h2></div><p class="empty">No stories match.</p></section>` : "";
    gs = pickLead(gs);
    const nLead = full ? 2 : (gs.length > 7 ? 2 : 1), leads = gs.slice(0, nLead);
    const rest = gs.slice(nLead).sort(full ? byDate : byScore).slice(0, full ? 60 : 6);
    return `<section class="sect" id="s-${s.id}" style="--sc:${s.c}">
      <div class="sect-h"><h2>${s.name}</h2>${full ? "" : `<button class="more" data-tab="${s.id}">More ${esc(s.short)} ›</button>`}</div>
      <div class="sect-body"><div class="leads">${leads.map((g) => big(g.lead, g.rel)).join("")}</div>
      <div class="list">${rest.map((g) => row(g.lead)).join("")}</div></div></section>`;
  }
  function topStories(items) {
    let gs = clusters(items.filter((it) => Date.now() - new Date(it.published) < 4 * 864e5)).sort(byScore);
    if (gs.length < 6) gs = clusters(items).sort(byScore);
    if (!gs.length) return `<section class="sect"><p class="empty">No stories match.</p></section>`;
    // keep the hero varied: at most 2 lead stories from the same section
    const seen = {}, pick = [];
    for (const g of pickLead(gs)) { const s = g.lead.section; if ((seen[s] = (seen[s] || 0) + 1) <= 2 || pick.length >= 8) pick.push(g); if (pick.length >= 8) break; }
    const [hero, ...rest] = pick;
    pick.slice(0, 7).forEach((g) => st.used.add(g.lead.cluster || g.lead.id));
    return `<section class="sect" id="s-top"><div class="sect-h"><h2>Top stories</h2></div>
      <div class="hero"><div>${big(hero.lead, hero.rel)}</div><div class="meds">${rest.slice(0, 4).map((g) => med(g.lead, g.rel)).join("")}</div></div>
      ${rest.length > 4 ? `<div class="more-top">${rest.slice(4, 7).map((g) => big(g.lead, [])).join("")}</div>` : ""}</section>`;
  }
  function community(full) {
    const items = st.x.map((a) => { const h = String(a.handle).replace(/[^A-Za-z0-9_]/g, "");
      return `<li class="xacc"><a href="https://x.com/${h}" target="_blank" rel="noopener noreferrer"><b>@${esc(h)}</b><span>${esc(a.note || "")}</span></a></li>`; }).join("");
    return full ? `<section class="sect"><div class="sect-h"><h2>Community / X</h2></div><ul class="mini">${items}</ul><p class="updated">Curated EUC accounts. Opens on x.com, no embeds or tracking.</p></section>`
      : `<h2>Community on X</h2><ul class="mini">${items}</ul><p class="note">Curated accounts, opens x.com.</p>`;
  }
  function outages() {
    const q = st.q.toLowerCase();
    const list = st.outages.filter((it) => !q || it.title.toLowerCase().includes(q))
      .sort((a, b) => new Date(b.published) - new Date(a.published)).slice(0, 8);
    $("#outages").innerHTML = `<h2><span class="dot" aria-hidden="true"></span>Outages &amp; status</h2>` +
      (list.length ? `<ul class="mini">${list.map((it) => `<li><a href="${esc(safe(it.link) || "#")}" target="_blank" rel="noopener noreferrer"><span class="t">${esc(it.title)}</span><span class="m">${esc(it.source)} · ${ago(it.published)}</span></a></li>`).join("")}</ul>`
        : `<p class="empty">No recent outage reports.</p>`) +
      `<p class="note">Official status feeds (AWS, Azure, Google Cloud, Cloudflare, Microsoft 365) and outage news.</p>`;
  }
  function render() {
    const items = visible();
    let html;
    st.used = new Set();
    if (st.tab === "top") { const top = topStories(items); html = top + SECTIONS.slice(1, 6).map((s) => sectionBlock(s, items, false, st.used)).join(""); }
    else if (st.tab === "community") html = community(true);
    else html = sectionBlock(secOf(st.tab), items, true);
    $("#main").innerHTML = html;
    outages();
    $("#community").innerHTML = community(false);
    document.querySelectorAll(".tab").forEach((t) => t.setAttribute("aria-selected", t.dataset.tab === st.tab));
  }
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
    st.outages = items.filter((i) => i.section === "outages");
    st.all = items.filter((i) => i.section !== "outages");
    st.x = d.x_accounts || [];
    $("#updated").textContent = `Updated ${ago(d.updated)} · ${new Date(d.updated).toLocaleString([], { dateStyle: "medium", timeStyle: "short" })}`;
    chips();
    const h = location.hash.slice(1);
    setTab(SECTIONS.some((s) => s.id === h) ? h : "top", false);
  }).catch(() => { $("#updated").textContent = "Couldn't load the news data."; });
})();
