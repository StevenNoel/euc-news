(() => {
  const $ = (s) => document.querySelector(s);
  const CATS = [
    { id: "security", name: "Security", ab: "SEC", c: "#c0504d", wide: true },
    { id: "citrix", name: "Citrix & NetScaler", ab: "CTX", c: "#4a7fb5" },
    { id: "microsoft", name: "Microsoft & Windows", ab: "WIN", c: "#2f8fbf" },
    { id: "m365", name: "Microsoft 365", ab: "365", c: "#c76a3a" },
    { id: "avd-w365", name: "AVD & Windows 365", ab: "AVD", c: "#3d8f8a" },
    { id: "azure-local", name: "Azure Local", ab: "AZL", c: "#5a72c4" },
    { id: "fslogix", name: "FSLogix", ab: "FSL", c: "#7a62b8" },
    { id: "omnissa-vmware", name: "Omnissa & VMware", ab: "OMN", c: "#5b8c4a" },
    { id: "controlup", name: "ControlUp", ab: "CU", c: "#b0567f" },
    { id: "igel", name: "IGEL", ab: "IGL", c: "#3f8a9e" },
    { id: "nutanix", name: "Nutanix", ab: "NTX", c: "#6b8f2e" },
    { id: "unicon", name: "Unicon", ab: "UNI", c: "#a07a2c" },
    { id: "x", name: "X / Community", ab: "X", c: "#5f6b78" },
  ];
  const PER = 8, mobile = matchMedia("(max-width:640px)");
  const st = { items: [], x: [], q: "", days: 7, on: new Set(), shown: {}, open: {} };
  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const safe = (u) => (/^https?:\/\//i.test(u || "") ? u : "");
  const ago = (d) => { const s = (Date.now() - d) / 1e3; return s < 3600 ? Math.max(1, s / 60 | 0) + "m" : s < 86400 ? (s / 3600 | 0) + "h" : (s / 86400 | 0) + "d"; };

  const card = (it, cat) => {
    const img = safe(it.image);
    const th = img ? `<img src="${esc(img)}" alt="" loading="lazy" decoding="async" referrerpolicy="no-referrer" onerror="this.remove()">` : "";
    return `<li><a class="card${it.security ? " sec" : ""}" href="${esc(safe(it.link) || "#")}" target="_blank" rel="noopener noreferrer" style="--c:${cat.c}">
      <div class="th" aria-hidden="true"><span>${cat.ab}</span>${th}</div>
      <div class="body"><div class="title">${esc(it.title)}</div>
      <div class="meta">${esc(it.source)} · <time datetime="${esc(it.published)}" title="${esc(new Date(it.published).toLocaleString())}">${ago(new Date(it.published))} ago</time></div></div></a></li>`;
  };
  const inCat = (it, id) => id === "security" ? it.security : it.category === id;
  function filtered() {
    const min = Date.now() - st.days * 864e5, q = st.q.toLowerCase();
    return st.items.filter((it) => new Date(it.published) >= min &&
      (!q || `${it.title} ${it.summary} ${it.source} ${it.tags.join(" ")}`.toLowerCase().includes(q)));
  }
  // Desktop: all open. Phone: Security open, others collapsed until tapped (remembered).
  const isOpen = (id) => id in st.open ? st.open[id] : (!mobile.matches || id === "security" || st.on.size > 0 || !!st.q);
  function render() {
    const f = filtered();
    const cats = CATS.filter((c) => !st.on.size || st.on.has(c.id));
    $("#grid").innerHTML = cats.map((c) => {
      const head = (n) => `<summary><span class="dot">${c.ab}</span>${c.name}<span class="n">${n}</span></summary>`;
      if (c.id === "x") {
        return `<details class="col" style="--c:${c.c}" data-id="x" ${isOpen("x") ? "open" : ""}>${head(st.x.length)}
          <ul class="x">${st.x.map((a) => { const h = String(a.handle).replace(/[^A-Za-z0-9_]/g, "");
            return `<li><a href="https://x.com/${h}" target="_blank" rel="noopener noreferrer"><b>@${esc(h)}</b><span>${esc(a.note || "")}</span></a></li>`; }).join("")}</ul></details>`;
      }
      const list = f.filter((it) => inCat(it, c.id)), n = st.shown[c.id] || (c.wide ? PER * 2 : PER);
      return `<details class="col${c.wide ? " wide" : ""}" style="--c:${c.c}" data-id="${c.id}" ${isOpen(c.id) ? "open" : ""}>${head(list.length)}
        ${list.length ? `<ul class="cards${c.wide ? " two" : ""}">${list.slice(0, n).map((it) => card(it, c)).join("")}</ul>` : `<div class="empty">Nothing in this time range.</div>`}
        ${list.length > n ? `<button class="more" data-id="${c.id}">Show ${Math.min(PER * 2, list.length - n)} more</button>` : ""}</details>`;
    }).join("");
  }
  function chips() {
    $("#chips").innerHTML = `<button class="chip" data-id="" aria-pressed="${!st.on.size}" style="--c:var(--accent)">All</button>` +
      CATS.map((c) => `<button class="chip" data-id="${c.id}" aria-pressed="${st.on.has(c.id)}" style="--c:${c.c}"><i></i>${c.name}</button>`).join("");
  }
  $("#chips").addEventListener("click", (e) => {
    const b = e.target.closest(".chip"); if (!b) return;
    const id = b.dataset.id; if (!id) st.on.clear(); else st.on.has(id) ? st.on.delete(id) : st.on.add(id);
    st.open = {}; chips(); render();
  });
  $("#grid").addEventListener("toggle", (e) => { const d = e.target; if (d.dataset && d.dataset.id) st.open[d.dataset.id] = d.open; }, true);
  $("#grid").addEventListener("click", (e) => {
    const b = e.target.closest(".more"); if (!b) return;
    const c = CATS.find((x) => x.id === b.dataset.id);
    st.shown[c.id] = (st.shown[c.id] || (c.wide ? PER * 2 : PER)) + PER * 2; render();
  });
  let t; $("#q").addEventListener("input", (e) => { clearTimeout(t); t = setTimeout(() => { st.q = e.target.value; st.open = {}; render(); }, 150); });
  $("#range").addEventListener("change", (e) => { st.days = +e.target.value; render(); });
  $("#theme").addEventListener("click", () => {
    const root = document.documentElement, cur = root.dataset.theme === "auto"
      ? (matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light") : root.dataset.theme;
    root.dataset.theme = cur === "dark" ? "light" : "dark";
    try { localStorage.setItem("theme", root.dataset.theme); } catch (e) {}
  });
  chips();
  fetch("data/items.json", { cache: "no-cache" }).then((r) => r.json()).then((d) => {
    st.items = d.items || []; st.x = d.x_accounts || [];
    const up = new Date(d.updated);
    $("#updated").textContent = `Updated ${up.toLocaleString([], { dateStyle: "medium", timeStyle: "short" })} (${ago(up)} ago) · ${d.count} articles`;
    render();
  }).catch(() => { $("#updated").textContent = "Couldn't load news data."; });
})();
