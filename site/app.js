(() => {
  const $ = (id) => document.getElementById(id);
  const state = { items: [], tags: new Set(), q: "", days: 7, secOnly: false, shown: 60 };
  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const safeUrl = (u) => (/^https?:\/\//i.test(u) ? u : "#");
  const ago = (d) => {
    const s = (Date.now() - d) / 1000;
    if (s < 3600) return Math.max(1, Math.round(s / 60)) + "m ago";
    if (s < 86400) return Math.round(s / 3600) + "h ago";
    return Math.round(s / 86400) + "d ago";
  };
  const li = (it) => `<li class="${it.security ? "sec" : ""}"><a class="t" href="${esc(safeUrl(it.link))}" target="_blank" rel="noopener noreferrer">${esc(it.title)}</a>
    <div class="m">${esc(it.source)} · <time datetime="${esc(it.published)}" title="${new Date(it.published).toLocaleString()}">${ago(new Date(it.published))}</time>
    <span class="chips">${it.tags.map((t) => `<span>${esc(t)}</span>`).join("")}</span></div>
    ${it.summary ? `<div class="s">${esc(it.summary)}</div>` : ""}</li>`;

  function filtered() {
    const min = Date.now() - state.days * 864e5;
    const q = state.q.toLowerCase();
    return state.items.filter((it) =>
      new Date(it.published) >= min &&
      (!state.secOnly || it.security) &&
      [...state.tags].every((t) => it.tags.includes(t)) &&
      (!q || `${it.title} ${it.summary} ${it.source} ${it.tags.join(" ")}`.toLowerCase().includes(q)));
  }
  function render() {
    const f = filtered();
    $("count").textContent = `(${f.length})`;
    $("list").innerHTML = f.slice(0, state.shown).map(li).join("") || "<li>No matching items.</li>";
    $("more").hidden = f.length <= state.shown;
    const sec = f.filter((i) => i.security).slice(0, 12);
    $("secList").innerHTML = sec.map(li).join("") || "<li>No security items in this view.</li>";
    document.querySelectorAll(".tag").forEach((b) => b.classList.toggle("on", state.tags.has(b.dataset.t)));
  }
  function buildTags() {
    const counts = {};
    state.items.forEach((i) => i.tags.forEach((t) => (counts[t] = (counts[t] || 0) + 1)));
    $("tags").innerHTML = Object.keys(counts).sort().map((t) => `<button class="tag" data-t="${esc(t)}">${esc(t)} ${counts[t]}</button>`).join("");
    $("tags").onclick = (e) => {
      const t = e.target.closest(".tag")?.dataset.t; if (!t) return;
      state.tags.has(t) ? state.tags.delete(t) : state.tags.add(t); state.shown = 60; render();
    };
  }
  function buildX(accts) {
    $("xList").innerHTML = (accts || []).map((a) => {
      const h = String(a.handle).replace(/[^A-Za-z0-9_]/g, "");
      return `<li><a href="https://x.com/${h}" target="_blank" rel="noopener noreferrer">@${esc(h)}</a><div class="muted">${esc(a.note || "")}</div></li>`;
    }).join("");
  }
  $("q").oninput = (e) => { state.q = e.target.value; state.shown = 60; render(); };
  $("range").onchange = (e) => { state.days = +e.target.value; render(); };
  $("secOnly").onchange = (e) => { state.secOnly = e.target.checked; render(); };
  $("more").onclick = () => { state.shown += 60; render(); };

  fetch("data/items.json", { cache: "no-cache" }).then((r) => r.json()).then((d) => {
    state.items = d.items || [];
    const up = new Date(d.updated);
    $("updated").textContent = `Last updated ${up.toLocaleString()} (${ago(up)}) · ${d.count} items / ${d.window_days} days`;
    buildTags(); buildX(d.x_accounts); render();
  }).catch(() => { $("updated").textContent = "Failed to load data."; });
})();
