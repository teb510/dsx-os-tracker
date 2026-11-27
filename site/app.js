"use strict";

const $ = (s, el = document) => el.querySelector(s);
const NS = "http://www.w3.org/2000/svg";
const nf = new Intl.NumberFormat("en-US");
const fmt = (n) => (n == null ? "–" : nf.format(n));
const short = (n) => (n >= 10000 ? (n / 1000).toFixed(n >= 100000 ? 0 : 1) + "k" : nf.format(n));
// Bare YYYY-MM-DD snapshot dates are calendar days; parse them as local, not UTC midnight.
const day = (iso) => (iso ? new Date(/^\d{4}-\d{2}-\d{2}$/.test(iso) ? iso + "T00:00" : iso).toLocaleDateString("en-US", { month: "short", day: "numeric" }) : "–");
const ago = (iso) => {
  if (!iso) return "–";
  const d = Math.floor((Date.now() - new Date(iso)) / 86400000);
  return d <= 0 ? "today" : d === 1 ? "1 day ago" : d < 60 ? `${d} days ago` : day(iso);
};
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const CAT_LABEL = {
  launch: "Launch", release: "Release", partnership: "Partnership", customer_adoption: "Customer adoption",
  business: "Business", technical_deep_dive: "Technical deep dive", tutorial: "Tutorial",
  analysis_opinion: "Analysis", community: "Community", other: "Other",
};

const SIZES = ["XXL", "XL", "L", "M", "S"];
const WEIGHT = { XXL: 16, XL: 8, L: 4, M: 2, S: 1 };
const STATUS_LABEL = { shipped: "Shipped", in_progress: "In progress", planned: "Planned", proposed: "Proposed" };
const pct = (x) => (x == null || !isFinite(x) ? "–" : Math.round(x * 100) + "%");

let DATA = { repos: [], totals: {}, history: {}, news: [], features: {} };

/* ---------- theme ---------- */
(function theme() {
  const root = document.documentElement;
  try {
    const saved = localStorage.getItem("theme");
    if (saved) root.dataset.theme = saved;
  } catch (_) {}
  $("#theme").addEventListener("click", () => {
    const dark = root.dataset.theme ? root.dataset.theme === "dark" : matchMedia("(prefers-color-scheme: dark)").matches;
    root.dataset.theme = dark ? "light" : "dark";
    try { localStorage.setItem("theme", root.dataset.theme); } catch (_) {}
  });
})();

/* ---------- tooltip ---------- */
const tip = $("#tip");
function showTip(html, x, y) {
  tip.innerHTML = html;
  tip.hidden = false;
  const r = tip.getBoundingClientRect();
  const left = Math.min(x + 12, innerWidth - r.width - 8);
  const top = y - r.height - 10 < 8 ? y + 14 : y - r.height - 10;
  tip.style.left = left + "px";
  tip.style.top = top + "px";
}
const hideTip = () => (tip.hidden = true);

function el(name, attrs = {}, parent) {
  const node = document.createElementNS(NS, name);
  for (const [k, v] of Object.entries(attrs)) node.setAttribute(k, v);
  if (parent) parent.appendChild(node);
  return node;
}

// Draw at the host's real pixel width so text stays 11px instead of scaling.
const widthOf = (host) => Math.max(300, Math.round(host.clientWidth || 560));

function niceMax(v) {
  if (v <= 0) return 1;
  const p = Math.pow(10, Math.floor(Math.log10(v)));
  return [1, 2, 2.5, 5, 10].map((m) => m * p).find((m) => m >= v);
}

/* ---------- charts ---------- */
function lineChart(host, points, { label = "Stars" } = {}) {
  host.innerHTML = "";
  if (points.length < 2) {
    host.innerHTML = `<div class="empty">Trend appears after the second daily snapshot.${points.length ? ` Current: <b>${fmt(points[0][1])}</b>` : ""}</div>`;
    return;
  }
  const W = widthOf(host), H = 220, m = { t: 12, r: 12, b: 24, l: 48 };
  const svg = el("svg", { viewBox: `0 0 ${W} ${H}`, role: "img", "aria-label": `${label} over time` }, host);
  const vals = points.map((p) => p[1]);
  const lo = Math.min(...vals), hi = Math.max(...vals);
  const pad = Math.max(1, (hi - lo) * 0.1);
  const y0 = Math.max(0, Math.floor(lo - pad)), y1 = Math.ceil(hi + pad);
  const x = (i) => m.l + (i / (points.length - 1)) * (W - m.l - m.r);
  const y = (v) => H - m.b - ((v - y0) / (y1 - y0)) * (H - m.t - m.b);
  for (let i = 0; i <= 4; i++) {
    const v = y0 + ((y1 - y0) * i) / 4, yy = y(v);
    el("line", { x1: m.l, x2: W - m.r, y1: yy, y2: yy, class: i ? "gridline" : "baseline" }, svg);
    el("text", { x: m.l - 6, y: yy + 4, "text-anchor": "end" }, svg).textContent = short(Math.round(v));
  }
  const ticks = Math.max(2, Math.min(6, points.length, Math.floor((W - m.l - m.r) / 70)));
  for (let i = 0; i < ticks; i++) {
    const idx = Math.round((i * (points.length - 1)) / (ticks - 1));
    el("text", { x: x(idx), y: H - 6, "text-anchor": i === 0 ? "start" : i === ticks - 1 ? "end" : "middle" }, svg).textContent = day(points[idx][0]);
  }
  el("path", { d: points.map((p, i) => `${i ? "L" : "M"}${x(i)},${y(p[1])}`).join(""), fill: "none", stroke: "var(--s1)", "stroke-width": 2, "stroke-linejoin": "round" }, svg);
  const cross = el("line", { y1: m.t, y2: H - m.b, stroke: "var(--axis)", visibility: "hidden" }, svg);
  const dot = el("circle", { r: 4, fill: "var(--s1)", stroke: "var(--surface)", "stroke-width": 2, visibility: "hidden" }, svg);
  const hit = el("rect", { x: m.l, y: m.t, width: W - m.l - m.r, height: H - m.t - m.b, fill: "transparent" }, svg);
  hit.addEventListener("pointermove", (e) => {
    const r = svg.getBoundingClientRect();
    const px = ((e.clientX - r.left) / r.width) * W;
    const i = Math.max(0, Math.min(points.length - 1, Math.round(((px - m.l) / (W - m.l - m.r)) * (points.length - 1))));
    cross.setAttribute("x1", x(i)); cross.setAttribute("x2", x(i)); cross.setAttribute("visibility", "visible");
    dot.setAttribute("cx", x(i)); dot.setAttribute("cy", y(points[i][1])); dot.setAttribute("visibility", "visible");
    showTip(`${day(points[i][0])}<br><b>${fmt(points[i][1])}</b> ${label.toLowerCase()}`, e.clientX, e.clientY);
  });
  hit.addEventListener("pointerleave", () => { cross.setAttribute("visibility", "hidden"); dot.setAttribute("visibility", "hidden"); hideTip(); });
}

const clip = (s, n) => (s.length > n ? s.slice(0, n - 1) + "…" : s);

/* Horizontal bars; series = [{key, label, color}] stacked left to right. */
function hbar(host, rows, series, { onClick, format = fmt, tip, note, right = 48 } = {}) {
  host.innerHTML = "";
  if (!rows.length) { host.innerHTML = `<div class="empty">No data</div>`; return; }
  const W = widthOf(host), rowH = 26, m = { t: 4, r: right, b: 4, l: Math.min(170, Math.round(W * 0.36)) };
  const H = m.t + m.b + rows.length * rowH;
  const svg = el("svg", { viewBox: `0 0 ${W} ${H}`, role: "img" }, host);
  const total = (r) => series.reduce((s, k) => s + (r[k.key] || 0), 0);
  const max = niceMax(Math.max(...rows.map(total)));
  const sx = (v) => (v / max) * (W - m.l - m.r);
  rows.forEach((r, i) => {
    const y = m.t + i * rowH;
    const g = el("g", { style: onClick ? "cursor:pointer" : "" }, svg);
    el("rect", { x: 0, y, width: W, height: rowH, fill: "transparent" }, g);
    el("text", { x: m.l - 8, y: y + rowH / 2 + 4, "text-anchor": "end", class: "lbl" }, g).textContent = clip(r.label, Math.floor((m.l - 12) / 6.2));
    let cx = m.l;
    const segs = series.filter((s) => r[s.key] > 0);
    segs.forEach((s, j) => {
      const w = sx(r[s.key]);
      const last = j === segs.length - 1;
      const gap = last ? 0 : 2;
      const width = Math.max(1, w - gap);
      const h = 14, yy = y + (rowH - h) / 2;
      // Rounded data-end only on the outermost segment.
      const rad = last ? Math.min(4, width) : 0;
      el("path", {
        d: `M${cx},${yy}h${width - rad}${rad ? `a${rad},${rad} 0 0 1 ${rad},${rad}v${h - 2 * rad}a${rad},${rad} 0 0 1 -${rad},${rad}` : `v${h}`}h-${width - rad}z`,
        fill: s.color,
      }, g);
      cx += w;
    });
    const val = el("text", { x: cx + 6, y: y + rowH / 2 + 4, class: "val" }, g);
    val.textContent = format(total(r));
    // Optional muted text after the value, e.g. the row's top organization.
    if (note && note(r)) el("tspan", { class: "note", dx: 6 }, val).textContent = note(r);
    g.addEventListener("pointermove", (e) => {
      if (tip) return showTip(tip(r), e.clientX, e.clientY);
      const parts = series.map((s) => `${s.label}: <b>${fmt(r[s.key] || 0)}</b>`).join("<br>");
      showTip(`<b>${esc(r.label)}</b><br>${parts}`, e.clientX, e.clientY);
    });
    g.addEventListener("pointerleave", hideTip);
    if (onClick) g.addEventListener("click", () => { hideTip(); onClick(r); });
  });
}

function vbars(host, values, labels) {
  host.innerHTML = "";
  const W = widthOf(host), H = 150, m = { t: 8, r: 8, b: 22, l: 36 };
  const svg = el("svg", { viewBox: `0 0 ${W} ${H}`, role: "img", "aria-label": "Weekly commits" }, host);
  const max = niceMax(Math.max(1, ...values));
  const bw = (W - m.l - m.r) / values.length;
  const y = (v) => H - m.b - (v / max) * (H - m.t - m.b);
  [0, max / 2, max].forEach((v, i) => {
    el("line", { x1: m.l, x2: W - m.r, y1: y(v), y2: y(v), class: i ? "gridline" : "baseline" }, svg);
    el("text", { x: m.l - 6, y: y(v) + 4, "text-anchor": "end" }, svg).textContent = short(v);
  });
  values.forEach((v, i) => {
    const x = m.l + i * bw + 1, w = bw - 2, top = y(v), h = H - m.b - top;
    const g = el("g", {}, svg);
    el("rect", { x: m.l + i * bw, y: m.t, width: bw, height: H - m.t - m.b, fill: "transparent" }, g);
    if (h > 0) {
      const r = Math.min(4, h, w / 2);
      el("path", { d: `M${x},${H - m.b}v-${h - r}a${r},${r} 0 0 1 ${r},-${r}h${w - 2 * r}a${r},${r} 0 0 1 ${r},${r}v${h - r}z`, fill: "var(--s1)" }, g);
    }
    if (i % 2 === 0 || i === values.length - 1) el("text", { x: x + w / 2, y: H - 6, "text-anchor": "middle" }, svg).textContent = labels[i];
    g.addEventListener("pointermove", (e) => showTip(`Week of ${labels[i]}<br><b>${fmt(v)}</b> commits`, e.clientX, e.clientY));
    g.addEventListener("pointerleave", hideTip);
  });
}

function sparkline(values) {
  const W = 96, H = 22, max = Math.max(1, ...values);
  const svg = el("svg", { width: W, height: H, viewBox: `0 0 ${W} ${H}`, class: "spark", "aria-hidden": "true" });
  const x = (i) => 1 + (i / (values.length - 1)) * (W - 2);
  const y = (v) => H - 2 - (v / max) * (H - 4);
  el("path", { d: values.map((v, i) => `${i ? "L" : "M"}${x(i)},${y(v)}`).join(""), fill: "none", stroke: "var(--s1)", "stroke-width": 1.5, "stroke-linejoin": "round" }, svg);
  return svg;
}

function weekLabels(n, generated) {
  const end = new Date(generated);
  return Array.from({ length: n }, (_, i) => {
    const d = new Date(end - (n - 1 - i) * 7 * 86400000 - 6 * 86400000);
    return d.toLocaleDateString("en-US", { month: "short", day: "numeric" });
  });
}

/* ---------- history helpers ---------- */
function historyDelta(getter, days) {
  const dates = Object.keys(DATA.history).sort();
  if (dates.length < 2) return null;
  const latest = dates[dates.length - 1];
  const cutoff = new Date(new Date(latest) - days * 86400000).toISOString().slice(0, 10);
  const base = dates.find((d) => d >= cutoff && d < latest);
  if (!base) return null;
  const a = getter(DATA.history[base]), b = getter(DATA.history[latest]);
  return a == null || b == null ? null : { value: b - a, since: base };
}

/* ---------- feature helpers ---------- */
const featuresOf = (r) => DATA.features[r.full_name]?.features || [];
const sizeBadge = (size) => `<span class="sz sz-${esc(size).toLowerCase()}" title="Size ${esc(size)}">${esc(size)}</span>`;
const isBig = (f) => f.size === "XXL" || f.size === "XL";
// Biggest first, then in progress before planned, then most recent activity.
const STATUS_RANK = { in_progress: 0, planned: 1, proposed: 2, shipped: 3 };
const byImpact = (a, b) => (WEIGHT[b.size] || 0) - (WEIGHT[a.size] || 0)
  || (STATUS_RANK[a.status] ?? 9) - (STATUS_RANK[b.status] ?? 9)
  || (b.last_activity || "").localeCompare(a.last_activity || "");
const weightOf = (fs) => fs.reduce((s, f) => s + (WEIGHT[f.size] || 0), 0);
const refUrl = (r, n) => `${r.url}/issues/${n}`;

/* Expandable "3 PRs, 1 issue" list of the evidence behind a feature. */
function refsBlock(r, f) {
  const refs = f.refs || [];
  if (!refs.length) return "";
  const prs = refs.filter((x) => x.kind !== "issue").length, issues = refs.length - prs;
  const label = [prs && `${prs} ${prs === 1 ? "PR" : "PRs"}`, issues && `${issues} ${issues === 1 ? "issue" : "issues"}`].filter(Boolean).join(", ");
  const items = refs.map((x) => `<li><a href="${esc(refUrl(r, x.number))}" target="_blank" rel="noopener">#${esc(x.number)}</a> ${esc(x.title || "")}${x.state ? ` <span class="dim">${esc(x.state)}</span>` : ""}</li>`).join("");
  return `<details class="rm-refs"><summary>${label}</summary><ul>${items}</ul></details>`;
}

/* One project's roadmap: headline XL/XXL items, a Now/Next/Later horizon of the rest, then recently shipped. */
function renderRoadmap(host, r) {
  const info = DATA.features[r.full_name];
  const fs = info?.features || [];
  if (!fs.length) {
    host.innerHTML = `<p class="dim rm-empty">${Object.keys(DATA.features).length
      ? "No features identified for this project yet."
      : "Roadmaps appear after the next daily run classifies features."}</p>`;
    return;
  }
  const showSmall = host.dataset.small === "1";
  const pill = (f) => `<span class="pill st-${esc(f.status)}">${STATUS_LABEL[f.status] || esc(f.status)}</span>`;
  const meta = (f) => [pill(f), f.theme && `<span>${esc(f.theme)}</span>`, f.milestone && `<span>Milestone ${esc(f.milestone)}</span>`,
    f.last_activity && `<span>Active ${ago(f.last_activity)}</span>`].filter(Boolean).join("");

  const headline = fs.filter(isBig).sort((a, b) => (a.status === "shipped") - (b.status === "shipped") || byImpact(a, b));
  const headHtml = headline.map((f) => `
    <li class="rm-item">
      <div>${sizeBadge(f.size)}</div>
      <div><div class="rm-title">${esc(f.title)}</div>
        <div class="rm-meta">${meta(f)}</div>
        ${f.summary ? `<p class="rm-sum">${esc(f.summary)}</p>` : ""}
        ${refsBlock(r, f)}</div>
    </li>`).join("");

  const mini = (f) => `<li><details class="rm-mini"><summary title="${esc(f.summary || "")}">${sizeBadge(f.size)}<span>${esc(f.title)}</span></summary>
    <div class="rm-mini-body">${f.summary ? `<p>${esc(f.summary)}</p>` : ""}<div class="rm-meta">${meta(f)}</div>${refsBlock(r, f)}</div></details></li>`;
  const rest = fs.filter((f) => !isBig(f));
  const small = rest.filter((f) => f.size === "S" && f.status !== "shipped").length;
  const lanes = [["in_progress", "Now", "now", "in progress"], ["planned", "Next", "next", "planned"], ["proposed", "Later", "later", "proposed"]]
    .map(([st, label, cls, note]) => {
      const items = rest.filter((f) => f.status === st && (showSmall || f.size !== "S")).sort(byImpact);
      return `<div class="lane ${cls}"><h4>${label} <span class="dim">${note} · ${items.length}</span></h4>
        ${items.length ? `<ul>${items.map(mini).join("")}</ul>` : `<p class="dim">Nothing here.</p>`}</div>`;
    }).join("");

  const shipped = rest.filter((f) => f.status === "shipped").sort(byImpact);
  const counts = SIZES.map((sz) => [sz, fs.filter((f) => f.size === sz && f.status !== "shipped").length]).filter(([, n]) => n)
    .map(([sz, n]) => `${n} ${sz}`).join(" · ");

  host.innerHTML = `
    ${info.summary ? `<p class="rm-lede">${esc(info.summary)}</p>` : ""}
    <p class="rm-asof">${counts ? `${counts} in flight · ` : ""}as of ${day(info.classified_at)}</p>
    <h4 class="rm-h">Headline items</h4>
    ${headHtml ? `<ul class="rm-list">${headHtml}</ul>` : `<p class="dim">No XL or XXL features right now.</p>`}
    <h4 class="rm-h">Horizon</h4>
    <div class="rm-lanes">${lanes}</div>
    ${small ? `<button type="button" class="ghost more rm-small">${showSmall ? "Hide small items" : `Show ${small} small ${small === 1 ? "item" : "items"}`}</button>` : ""}
    ${shipped.length ? `<details class="rm-shipped"><summary><h4 class="rm-h">Recently shipped, last 60 days <span class="dim">${shipped.length}</span></h4></summary>
      <ul class="rm-ship-list">${shipped.map(mini).join("")}</ul></details>` : ""}`;
  const btn = host.querySelector(".rm-small");
  if (btn) btn.addEventListener("click", () => { host.dataset.small = showSmall ? "" : "1"; renderRoadmap(host, r); });
}

/* ---------- sections ---------- */
function renderKpis() {
  const t = DATA.totals;
  const dates = Object.keys(DATA.history).sort();
  const items = [
    ["Stars", t.stars, "stars"],
    ["Forks", t.forks, "forks"],
    ["Open PRs", t.open_prs, "open_prs"],
    ["PRs merged, 30d", t.prs_merged_30d, null],
    ["Commits, 30d", t.commits_30d, null],
    ["Contributors (all time)", t.contributors, "contributors"],
    ["External contributors, 90d", t.active_external_90d, null, "Summed per repo"],
    ["Repositories", t.repos, null],
  ];
  $("#kpis").innerHTML = items.map(([label, value, key, note]) => {
    let delta = note || "";
    if (key) {
      const d = historyDelta((h) => h.totals[key], 7);
      if (d) delta = `<span class="${d.value > 0 ? "up" : ""}">${d.value >= 0 ? "+" : ""}${fmt(d.value)}</span> since ${day(d.since)}`;
      else if (dates.length) delta = `Tracking since ${day(dates[0])}`;
    }
    const cls = delta.includes('class="up"') ? "delta up" : "delta";
    return `<div class="kpi"><div class="label">${label}</div><div class="value">${fmt(value)}</div><div class="${cls}">${delta}</div></div>`;
  }).join("");
}

/* Stars over time: all projects (totals) or one project from its daily snapshots. */
function renderStars() {
  const full = $("#stars-repo").value;
  const r = full && DATA.repos.find((x) => x.full_name === full);
  const get = full ? (h) => h.repos[full]?.stars : (h) => h.totals.stars;
  const dates = Object.keys(DATA.history).sort().filter((d) => get(DATA.history[d]) != null);
  lineChart($("#chart-stars"), dates.map((d) => [d, get(DATA.history[d])]));
  $("#stars-title").textContent = r ? `Stars over time: ${r.name}` : "Total stars over time";
  const d = historyDelta(get, 7);
  $("#stars-delta").innerHTML = d ? `<span class="${d.value > 0 ? "pos" : "dim"}">${d.value >= 0 ? "+" : ""}${fmt(d.value)}</span> <span class="dim">since ${day(d.since)}</span>` : "";
  $("#stars-hint").textContent = dates.length ? `${r ? r.full_name : "All tracked repos"}. Daily snapshots since ${day(dates[0])}.` : "";
}

function renderCharts() {
  renderStars();

  const merged = [...DATA.repos].sort((a, b) => b.prs_merged_30d - a.prs_merged_30d).slice(0, 10)
    .map((r) => ({ label: r.name, merged: r.prs_merged_30d, repo: r }));
  hbar($("#chart-merged"), merged, [{ key: "merged", label: "PRs merged", color: "var(--s1)" }], { onClick: (r) => openDetail(r.repo) });

  const contrib = [...DATA.repos].sort((a, b) => b.active_contributors_90d - a.active_contributors_90d).slice(0, 12)
    .map((r) => ({ label: r.name, internal: r.active_internal_90d, external: r.active_external_90d, repo: r }));
  const series = [
    { key: "internal", label: "NVIDIA", color: "var(--s1)" },
    { key: "external", label: "External", color: "var(--s2)" },
  ];
  $("#contrib-legend").innerHTML = series.map((s) => `<span style="--c:${s.color}">${s.label}</span>`).join("");
  hbar($("#chart-contrib"), contrib, series, { onClick: (r) => openDetail(r.repo) });
  renderOrgs();
  renderForks();
}

const unattributedOf = (o) => o?.unattributed || o?.external_unattributed || o?.individuals || o?.external_individuals || { people: 0, contributions: 0 };
const byContrib = (a, b) => b.contributions - a.contributions || b.people - a.people;

let orgsMode = "ext", topExtAll = false;
function renderOrgs() {
  const co = DATA.totals.contributor_orgs;
  $("#orgs-card").hidden = !co?.external_people;
  if (!co?.external_people) return;
  const orgs = [...co.orgs].sort(byContrib);
  const un = unattributedOf(co);
  const total = co.external_contributions || orgs.reduce((s, o) => s + o.contributions, 0) + un.contributions;
  const top = orgs.slice(0, 3).map((o) => `<b class="org-name">${esc(o.org)}</b>`);
  $("#orgs-headline").innerHTML = `<b>${pct((total - un.contributions) / total)}</b> of external contributions come from ${fmt(orgs.length)} identified organizations${top.length ? `, led by ${top.join(", ")}` : ""}.`;
  $("#orgs-sub").textContent = `${pct(un.contributions / total)} unattributed (personal or no-reply emails, no profile signal) · ${fmt(co.external_people)} external contributors, ${fmt(total)} commits and PRs`;

  const rows = orgs.slice(0, 15).map((o) => ({ label: o.org, org: o.contributions, src: o }));
  rows.push({ label: "Unattributed", un: un.contributions, src: { ...un, repos: [] } });
  hbar($("#chart-orgs"), rows, [
    { key: "org", label: "Organization", color: "var(--s1)" },
    { key: "un", label: "Unattributed", color: "var(--unattr)" },
  ], {
    format: (v) => pct(v / total),
    tip: (r) => {
      const o = r.src, repos = o.repos || [];
      return `<b>${esc(r.label)}</b><br>Contributions: <b>${fmt(o.contributions)}</b> (${pct(o.contributions / total)})<br>People: <b>${fmt(o.people)}</b>`
        + (repos.length ? `<br>Repos: ${esc(repos.slice(0, 6).join(", "))}${repos.length > 6 ? ` +${repos.length - 6}` : ""}` : "");
    },
  });
  renderRepoOrgs();
  renderTopExternal();
}

function renderForks() {
  const fo = DATA.totals.fork_orgs;
  $("#forks-card").hidden = !fo?.forks_90d;
  if (!fo?.forks_90d) return;
  const orgs = [...(fo.orgs || [])].sort((a, b) => b.forks - a.forks);
  const total = fo.forks_90d, identified = orgs.reduce((s, o) => s + o.forks, 0);
  $("#forks-headline").innerHTML = `<b>${fmt(total)}</b> forks in 90 days; <b>${pct(identified / total)}</b> from ${fmt(orgs.length)} identified organizations`
    + ` <span class="dim">· ${pct((fo.internal || 0) / total)} NVIDIA · ${pct((fo.unattributed || 0) / total)} unattributed</span>`;
  const rows = orgs.slice(0, 12).map((o) => ({ label: o.org, org: o.forks, src: o }));
  if (fo.internal) rows.push({ label: "NVIDIA", nv: fo.internal, src: { forks: fo.internal } });
  if (fo.unattributed) rows.push({ label: "Unattributed", un: fo.unattributed, src: { forks: fo.unattributed } });
  hbar($("#chart-forks"), rows, [
    { key: "org", label: "Organization", color: "var(--s1)" },
    { key: "nv", label: "NVIDIA", color: "var(--o4)" },
    { key: "un", label: "Unattributed", color: "var(--unattr)" },
  ], {
    tip: (r) => {
      const o = r.src, repos = o.repos || [];
      return `<b>${esc(r.label)}</b><br>Forks: <b>${fmt(o.forks)}</b> (${pct(o.forks / total)})`
        + (o.people ? `<br>People: <b>${fmt(o.people)}</b>` : "")
        + (repos.length ? `<br>Repos: ${esc(repos.slice(0, 6).join(", "))}${repos.length > 6 ? ` +${repos.length - 6}` : ""}` : "");
    },
  });
}

/* Per repo: NVIDIA | that repo's top 3 orgs | other orgs | unattributed, by contributions. */
function renderRepoOrgs() {
  const hasInternal = DATA.repos.some((r) => r.internal_contributions != null);
  $("#orgs-mode").hidden = !hasInternal;
  const all = orgsMode === "all" && hasInternal;
  const rows = DATA.repos.filter((r) => r.external_orgs).map((r) => {
    const orgs = [...r.external_orgs].sort(byContrib), un = unattributedOf(r);
    const ext = orgs.reduce((s, o) => s + o.contributions, 0) + un.contributions;
    const row = { label: r.name, repo: r, ext, orgs, unC: un.contributions, nv: all ? r.internal_contributions || 0 : 0, un: un.contributions };
    orgs.slice(0, 3).forEach((o, i) => (row["o" + i] = o.contributions));
    row.other = orgs.slice(3).reduce((s, o) => s + o.contributions, 0);
    return row;
  }).filter((r) => r.ext > 0).sort((a, b) => b.ext - a.ext).slice(0, 15);
  const series = [
    ...(all ? [{ key: "nv", label: "NVIDIA", color: "var(--s1)" }] : []),
    { key: "o0", label: "Top org", color: "var(--o1)" },
    { key: "o1", label: "2nd org", color: "var(--o2)" },
    { key: "o2", label: "3rd org", color: "var(--o3)" },
    { key: "other", label: "Other orgs", color: "var(--o4)" },
    { key: "un", label: "Unattributed", color: "var(--unattr)" },
  ];
  $("#repo-orgs-legend").innerHTML = series.map((s) => `<span style="--c:${s.color}">${s.label}</span>`).join("")
    + `<span class="dim legend-note">Top orgs differ per repo; hover a bar for names.</span>`;
  // Name each row's leading org after the bar when there is room; phones get it in the tooltip only.
  const roomy = widthOf($("#chart-repo-orgs")) >= 600;
  hbar($("#chart-repo-orgs"), rows, series, {
    right: roomy ? 150 : 48,
    onClick: (r) => openDetail(r.repo),
    format: (v) => short(v),
    note: roomy ? (r) => (r.orgs[0] ? clip(r.orgs[0].org, 22) : "") : null,
    tip: (r) => {
      const base = all ? r.ext + r.nv : r.ext;
      const line = (name, v) => `${esc(name)}: <b>${fmt(v)}</b> (${pct(v / base)})`;
      const parts = [];
      if (all) parts.push(line("NVIDIA", r.nv));
      r.orgs.slice(0, 3).forEach((o) => parts.push(line(o.org, o.contributions)));
      if (r.other) parts.push(line(`Other orgs (${r.orgs.length - 3})`, r.other));
      if (r.unC) parts.push(line("Unattributed", r.unC));
      return `<b>${esc(r.label)}</b> <span class="dim">commits and PRs, 90d</span><br>${parts.join("<br>")}`;
    },
  });
}

function renderTopExternal() {
  const people = DATA.totals.contributor_orgs?.top_external || [];
  $("#top-ext-wrap").hidden = !people.length;
  if (!people.length) return;
  const LIMIT = 20, shown = topExtAll ? people : people.slice(0, LIMIT);
  $("#top-ext tbody").innerHTML = shown.map((p) => {
    const unsure = p.confidence === "medium" || p.confidence === "low";
    const org = p.org
      ? `<span title="${esc(p.evidence || "")}">${esc(p.org)}${unsure ? `<span class="unsure" aria-label="${esc(p.confidence)} confidence"> ?</span>` : ""}</span>`
      : `<span class="dim" title="${esc(p.evidence || "No organization signal")}">Unattributed</span>`;
    const repos = p.repos || [];
    return `<tr class="static">
      <td class="l"><a href="https://github.com/${encodeURIComponent(p.login)}" target="_blank" rel="noopener">${esc(p.login)}</a>${p.name && p.name !== p.login ? ` <span class="dim">${esc(p.name)}</span>` : ""}</td>
      <td class="l">${org}</td>
      <td class="l repos-cell">${esc(repos.slice(0, 4).join(", "))}${repos.length > 4 ? ` <span class="dim">+${repos.length - 4}</span>` : ""}</td>
      <td>${fmt(p.contributions)}</td></tr>`;
  }).join("");
  const more = $("#top-ext-more");
  more.hidden = people.length <= LIMIT;
  more.textContent = topExtAll ? "Show top 20" : `Show all ${people.length}`;
}

/* Roadmap section: pick a project, see its roadmap. */
const ROADMAP_KEY = "roadmap-repo";
function roadmapRepos() {
  return DATA.repos.filter((r) => featuresOf(r).length).sort((a, b) => weightOf(featuresOf(b)) - weightOf(featuresOf(a)));
}
function selectRoadmap(name, { scroll = false } = {}) {
  const repos = roadmapRepos();
  const r = repos.find((x) => x.name === name) || repos[0];
  if (!r) return;
  $("#roadmap-repo").value = r.name;
  renderRoadmap($("#roadmap-view"), r);
  try { localStorage.setItem(ROADMAP_KEY, r.name); } catch (_) {}
  if (scroll) $("#roadmap-card").scrollIntoView({ behavior: "smooth", block: "start" });
}
function renderRoadmapSection() {
  const repos = roadmapRepos();
  $("#roadmap-card").hidden = !repos.length;
  if (!repos.length) return;
  $("#roadmap-repo").innerHTML = repos.map((r) => {
    const big = featuresOf(r).filter((f) => isBig(f) && f.status !== "shipped").length;
    return `<option value="${esc(r.name)}">${esc(r.name)}${big ? ` · ${big} headline ${big === 1 ? "item" : "items"}` : ""}</option>`;
  }).join("");
  const gen = DATA.featuresGenerated;
  $("#roadmap-hint").textContent = `Features grouped and sized (XXL to S) by Claude from open and recently merged PRs, issues, and milestones${gen ? `, as of ${day(gen)}` : ""}. Not an official roadmap.`;
  const fromHash = location.hash.startsWith("#roadmap=") ? decodeURIComponent(location.hash.slice(9)) : null;
  let saved = null;
  try { saved = localStorage.getItem(ROADMAP_KEY); } catch (_) {}
  selectRoadmap(fromHash || saved, { scroll: !!fromHash });
}

let sortKey = "stars", sortAsc = false;
function renderTable() {
  const q = $("#filter").value.trim().toLowerCase();
  const val = (r, k) => (k === "latest_release" ? r.latest_release?.publishedAt || ""
    : k === "name" ? r.name.toLowerCase() : r[k] ?? -Infinity);
  const rows = DATA.repos
    .filter((r) => !q || (r.full_name + " " + r.description + " " + r.topics.join(" ")).toLowerCase().includes(q))
    .sort((a, b) => {
      const x = val(a, sortKey), y = val(b, sortKey);
      return (x < y ? -1 : x > y ? 1 : 0) * (sortAsc ? 1 : -1);
    });
  const tbody = $("#repos tbody");
  tbody.innerHTML = "";
  for (const r of rows) {
    const tr = document.createElement("tr");
    const d7 = r.stars_7d == null ? `<span class="dim">–</span>` : r.stars_7d > 0 ? `<span class="pos">+${fmt(r.stars_7d)}</span>` : fmt(r.stars_7d);
    const owner = r.owner !== "dsx-ai-factory" ? `${esc(r.owner)}/` : "";
    tr.innerHTML = `
      <td class="l name">${owner}${esc(r.name)}<small>${esc(r.description)}</small></td>
      <td>${fmt(r.stars)}</td><td>${d7}</td><td>${fmt(r.forks)}</td>
      <td>${fmt(r.open_prs)}</td><td>${fmt(r.prs_merged_30d)}</td><td>${fmt(r.open_issues)}</td>
      <td>${fmt(r.contributors)}</td><td>${fmt(r.active_external_90d)}</td><td>${fmt(r.commits_30d)}</td>
      <td class="l spark-cell"></td>
      <td class="l">${r.latest_release ? `${esc(r.latest_release.tagName)} <span class="dim">${day(r.latest_release.publishedAt)}</span>` : `<span class="dim">–</span>`}</td>
      <td class="l">${ago(r.pushed_at)}</td>`;
    tr.querySelector(".spark-cell").appendChild(sparkline(r.commits_weekly));
    tr.addEventListener("click", () => openDetail(r));
    tbody.appendChild(tr);
  }
  document.querySelectorAll("#repos th[data-k]").forEach((th) => {
    th.classList.toggle("sorted", th.dataset.k === sortKey);
    th.classList.toggle("asc", th.dataset.k === sortKey && sortAsc);
  });
}

function newsFor(repoName) {
  return DATA.news.filter((n) => n.llm?.relevant && n.llm.repos.includes(repoName));
}

function openDetail(r) {
  $("#d-title").innerHTML = `<a href="${esc(r.url)}" target="_blank" rel="noopener">${esc(r.full_name)}</a>`;
  $("#d-desc").textContent = [r.description, r.language, r.license].filter(Boolean).join(" · ");
  const stat = (v, l) => `<div><b>${fmt(v)}</b><span>${l}</span></div>`;
  const releases = r.recent_releases.map((x) => `<li><a href="${esc(x.url)}" target="_blank" rel="noopener">${esc(x.name || x.tagName)}</a> <span class="dim">${day(x.publishedAt)}${x.isPrerelease ? " · pre-release" : ""}</span></li>`).join("");
  const roadmap = featuresOf(r).length ? `<h3>Roadmap</h3><div class="rm-host" id="d-roadmap"></div>` : "";
  let forks = "";
  if (r.fork_orgs) {
    const total = r.forks_90d || 0;
    const items = [...r.fork_orgs].sort((a, b) => b.forks - a.forks).slice(0, 10).map((o) =>
      `<li>${esc(o.org)}${o.confidence && o.confidence !== "high" ? `<span class="unsure" title="${esc(o.confidence)} confidence"> ?</span>` : ""} <span class="dim">${fmt(o.forks)} ${o.forks === 1 ? "fork" : "forks"}</span></li>`);
    if (r.fork_orgs.length > 10) items.push(`<li class="dim">+${r.fork_orgs.length - 10} more organizations</li>`);
    forks = `<h3>Forks by organization, last 90 days</h3>`
      + (total ? `<p class="hint">${fmt(total)} forks: ${fmt(r.forks_internal_90d || 0)} NVIDIA, ${fmt(r.forks_unattributed_90d || 0)} unattributed.</p>`
        + (items.length ? `<ul>${items.join("")}</ul>` : `<p class="dim">No forks from identified organizations.</p>`)
        : `<p class="dim">No forks in the last 90 days.</p>`);
  }
  let orgs = "";
  if (r.external_orgs) {
    const list = [...r.external_orgs].sort(byContrib), un = unattributedOf(r);
    const ext = list.reduce((s, o) => s + o.contributions, 0) + un.contributions;
    const line = (name, o, title) => `<li${title ? ` title="${esc(title)}"` : ""}>${name} <span class="dim">${pct(o.contributions / ext)} · ${fmt(o.contributions)} contributions · ${fmt(o.people)} ${o.people === 1 ? "person" : "people"}</span></li>`;
    const items = list.slice(0, 12).map((o) => line(esc(o.org) + (o.confidence && o.confidence !== "high" ? `<span class="unsure"> ?</span>` : ""), o, o.confidence && o.confidence !== "high" ? `${o.confidence} confidence` : ""));
    if (un.contributions || un.people) items.push(line(`<span class="dim">Unattributed</span>`, un));
    if (list.length > 12) items.push(`<li class="dim">+${list.length - 12} more organizations</li>`);
    const nv = r.internal_contributions;
    const nvLine = nv != null && nv + ext > 0 ? `<p class="hint">NVIDIA: ${pct(nv / (nv + ext))} of all ${fmt(nv + ext)} commits and PRs in the last 90 days.</p>` : "";
    orgs = `<h3>External contributions by organization, 90d</h3>${nvLine}${items.length ? `<ul>${items.join("")}</ul>` : `<p class="dim">No external contributors in the last 90 days.</p>`}`;
  }
  const news = newsFor(r.name).slice(0, 8).map((n) => `<li><a href="${esc(n.url)}" target="_blank" rel="noopener">${esc(n.title)}</a> <span class="dim">${esc(n.source)} · ${day(n.published)}</span></li>`).join("");
  $("#d-body").innerHTML = `
    <div class="stats">
      ${stat(r.stars, "Stars")}${stat(r.forks, "Forks")}${stat(r.watchers, "Watchers")}
      ${stat(r.open_prs, "Open PRs")}${stat(r.merged_prs, "Merged PRs")}${stat(r.prs_opened_30d, "PRs opened, 30d")}
      ${stat(r.prs_merged_30d, "PRs merged, 30d")}${stat(r.open_issues, "Open issues")}${stat(r.issues_opened_30d, "Issues opened, 30d")}
      ${stat(r.issues_closed_30d, "Issues closed, 30d")}${stat(r.contributors, "Contributors")}${stat(r.active_external_90d, "External, 90d")}
      ${stat(r.external_prs_90d, "External PRs, 90d")}${stat(r.commits_total, "Commits")}${stat(r.releases, "Releases")}
    </div>
    ${roadmap}
    <h3>Weekly commits, last 13 weeks</h3><div class="chart" id="d-commits"></div>
    <h3>Stars over time</h3><div class="chart" id="d-stars"></div>
    ${orgs}
    ${forks}
    <h3>Recent releases</h3>${releases ? `<ul>${releases}</ul>` : `<p class="dim">No releases yet.</p>`}
    <h3>Related news</h3>${news ? `<ul>${news}</ul>` : `<p class="dim">No classified news tied to this repo yet.</p>`}
    <p class="hint">Created ${day(r.created_at)} · last push ${ago(r.pushed_at)}${r.topics.length ? " · " + r.topics.map(esc).join(", ") : ""}</p>`;
  if (roadmap) renderRoadmap($("#d-roadmap"), r);
  $("#detail").showModal(); // open first so the charts can measure their width
  vbars($("#d-commits"), r.commits_weekly, weekLabels(r.commits_weekly.length, DATA.generated));
  const dates = Object.keys(DATA.history).sort().filter((d) => DATA.history[d].repos[r.full_name]);
  lineChart($("#d-stars"), dates.map((d) => [d, DATA.history[d].repos[r.full_name].stars]));
}

let impFilter = "medium";
function renderNews() {
  const classified = DATA.news.some((n) => n.llm);
  const cat = $("#news-cat").value, repo = $("#news-repo").value, showAll = $("#news-irrelevant").checked;
  const rank = { high: 3, medium: 2, low: 1 };
  const min = impFilter === "high" ? 3 : impFilter === "medium" ? 2 : 0;
  const items = DATA.news.filter((n) => {
    if (!classified) return true;
    if (!n.llm) return showAll;
    if (!n.llm.relevant) return showAll;
    if (rank[n.llm.importance] < min) return false;
    if (cat && n.llm.category !== cat) return false;
    if (repo && !n.llm.repos.includes(repo)) return false;
    return true;
  });
  const relevant = DATA.news.filter((n) => n.llm?.relevant).length;
  $("#news-hint").textContent = classified
    ? `${relevant} relevant of ${DATA.news.length} collected mentions. Classified by Claude.`
    : `${DATA.news.length} keyword matches. Not classified yet: add an ANTHROPIC_API_KEY secret to enable relevance and importance.`;
  $("#news-list").innerHTML = items.slice(0, 150).map((n) => {
    const l = n.llm;
    const tags = [];
    if (l) {
      tags.push(`<span class="imp ${l.importance}">${l.importance[0].toUpperCase() + l.importance.slice(1)}</span>`);
      tags.push(`<span class="tag">${CAT_LABEL[l.category] || l.category}</span>`);
      l.repos.forEach((r) => tags.push(`<span class="tag">${esc(r)}</span>`));
      if (!l.relevant) tags.push(`<span class="tag">Filtered out</span>`);
    }
    tags.push(`<span>${esc(n.source)}</span>`);
    if (n.discussion_url && n.discussion_url !== n.url) tags.push(`<a href="${esc(n.discussion_url)}" target="_blank" rel="noopener">discussion</a>`);
    return `<li class="${l && !l.relevant ? "filtered" : ""}">
      <div class="date">${day(n.published || n.first_seen)}</div>
      <div><a class="title" href="${esc(n.url)}" target="_blank" rel="noopener">${esc(n.title)}</a>
      ${l?.summary ? `<div class="summary">${esc(l.summary)}</div>` : ""}
      <div class="tags">${tags.join("")}</div></div></li>`;
  }).join("") || `<li><div></div><div class="dim">No items match these filters.</div></li>`;
}

/* ---------- boot ---------- */
async function load(name, fallback) {
  try {
    const res = await fetch(`data/${name}.json`, { cache: "no-cache" });
    return res.ok ? await res.json() : fallback;
  } catch (_) {
    return fallback;
  }
}

(async function main() {
  const [repos, history, news, features] = await Promise.all([
    load("repos", { repos: [], totals: {} }),
    load("history", {}),
    load("news", { items: [] }),
    load("features", { repos: {} }),
  ]);
  DATA = {
    repos: repos.repos, totals: repos.totals, generated: repos.generated_at, history, news: news.items,
    features: features.repos || {}, featuresGenerated: features.generated_at,
  };
  $("#updated").textContent = repos.generated_at ? `Updated ${new Date(repos.generated_at).toLocaleString("en-US", { dateStyle: "medium", timeStyle: "short" })}` : "No data yet";

  const cats = [...new Set(DATA.news.map((n) => n.llm?.category).filter(Boolean))].sort();
  $("#news-cat").innerHTML += cats.map((c) => `<option value="${c}">${CAT_LABEL[c] || c}</option>`).join("");
  $("#news-repo").innerHTML += DATA.repos.map((r) => r.name).sort().map((n) => `<option>${esc(n)}</option>`).join("");
  $("#stars-repo").innerHTML += [...DATA.repos].sort((a, b) => b.stars - a.stars)
    .map((r) => `<option value="${esc(r.full_name)}">${esc(r.name)} · ${short(r.stars)}</option>`).join("");

  renderKpis();
  renderCharts();
  renderTable();
  renderRoadmapSection();
  renderNews();

  $("#filter").addEventListener("input", renderTable);
  $("#stars-repo").addEventListener("change", renderStars);
  document.querySelectorAll("#repos th[data-k]").forEach((th) => th.addEventListener("click", () => {
    if (sortKey === th.dataset.k) sortAsc = !sortAsc;
    else { sortKey = th.dataset.k; sortAsc = th.dataset.k === "name"; }
    renderTable();
  }));
  document.querySelectorAll("#imp-seg button").forEach((b) => b.addEventListener("click", () => {
    impFilter = b.dataset.imp;
    document.querySelectorAll("#imp-seg button").forEach((x) => x.classList.toggle("on", x === b));
    renderNews();
  }));
  $("#roadmap-repo").addEventListener("change", (e) => {
    selectRoadmap(e.target.value);
    history.replaceState(null, "", `#roadmap=${encodeURIComponent(e.target.value)}`);
  });
  addEventListener("hashchange", () => {
    if (location.hash.startsWith("#roadmap=")) selectRoadmap(decodeURIComponent(location.hash.slice(9)), { scroll: true });
  });
  ["#news-cat", "#news-repo", "#news-irrelevant"].forEach((s) => $(s).addEventListener("change", renderNews));
  document.querySelectorAll("#orgs-mode button").forEach((b) => b.addEventListener("click", () => {
    orgsMode = b.dataset.mode;
    document.querySelectorAll("#orgs-mode button").forEach((x) => x.classList.toggle("on", x === b));
    renderRepoOrgs();
  }));
  $("#top-ext-more").addEventListener("click", () => { topExtAll = !topExtAll; renderTopExternal(); });
  let resizeTimer;
  addEventListener("resize", () => {
    clearTimeout(resizeTimer);
    resizeTimer = setTimeout(renderCharts, 150);
  });
  $("#d-close").addEventListener("click", () => $("#detail").close());
  $("#detail").addEventListener("click", (e) => { if (e.target.id === "detail") $("#detail").close(); });
})();
