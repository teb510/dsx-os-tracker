"use strict";

const $ = (s, el = document) => el.querySelector(s);
const NS = "http://www.w3.org/2000/svg";
const nf = new Intl.NumberFormat("en-US");
const fmt = (n) => (n == null ? "–" : nf.format(n));
const short = (n) => (n >= 10000 ? (n / 1000).toFixed(n >= 100000 ? 0 : 1) + "k" : nf.format(n));
const day = (iso) => (iso ? new Date(iso).toLocaleDateString("en-US", { month: "short", day: "numeric" }) : "–");
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
function hbar(host, rows, series, { onClick, format = fmt, tip } = {}) {
  host.innerHTML = "";
  if (!rows.length) { host.innerHTML = `<div class="empty">No data</div>`; return; }
  const W = widthOf(host), rowH = 26, m = { t: 4, r: 48, b: 4, l: Math.min(170, Math.round(W * 0.36)) };
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
    el("text", { x: cx + 6, y: y + rowH / 2 + 4, class: "val" }, g).textContent = format(total(r));
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
const inFlight = (f) => f.status === "in_progress" || f.status === "planned";
const sizeBadge = (size) => `<span class="sz sz-${esc(size).toLowerCase()}" title="Size ${esc(size)}">${esc(size)}</span>`;
// Biggest first, then in progress before planned, then most recent activity.
const STATUS_RANK = { in_progress: 0, planned: 1, shipped: 2, proposed: 3 };
const byImpact = (a, b) => (WEIGHT[b.size] || 0) - (WEIGHT[a.size] || 0)
  || (STATUS_RANK[a.status] ?? 9) - (STATUS_RANK[b.status] ?? 9)
  || (b.last_activity || "").localeCompare(a.last_activity || "");
const weightOf = (fs) => fs.reduce((s, f) => s + (WEIGHT[f.size] || 0), 0);
const flightWeight = (r) => weightOf(featuresOf(r).filter(inFlight));

function biggestInFlight(r) {
  const fs = featuresOf(r);
  const pick = (st) => fs.filter((f) => f.status === st).sort(byImpact)[0];
  return pick("in_progress") || pick("planned");
}

function sizeCounts(fs, sizes = ["XXL", "XL", "L"]) {
  return sizes.map((s) => [s, fs.filter((f) => f.size === s).length]).filter(([, n]) => n).map(([s, n]) => `${n} ${s}`).join(" · ");
}

const refUrl = (r, n) => `${r.url}/issues/${n}`;
function refLinks(r, refs, max = 6) {
  const links = (refs || []).slice(0, max).map((x) =>
    `<a href="${esc(refUrl(r, x.number))}" target="_blank" rel="noopener" title="${esc(x.title)}${x.state ? ` (${esc(x.state)})` : ""}">#${esc(x.number)}</a>`);
  if ((refs || []).length > max) links.push(`<span class="dim">+${refs.length - max}</span>`);
  return links.join("");
}

function jumpToRoadmap(name) {
  if ($("#detail").open) $("#detail").close();
  const sel = $("#roadmap-repo");
  if (sel.value && sel.value !== name) { sel.value = ""; renderRoadmaps(); }
  const d = document.getElementById(`roadmap-${name}`);
  if (!d) return;
  d.open = true;
  d.scrollIntoView({ behavior: "smooth", block: "start" });
  d.classList.add("flash");
  setTimeout(() => d.classList.remove("flash"), 1600);
  history.replaceState(null, "", `#roadmap-${name}`);
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

function renderCharts() {
  const dates = Object.keys(DATA.history).sort();
  lineChart($("#chart-stars"), dates.map((d) => [d, DATA.history[d].totals.stars]));
  $("#stars-hint").textContent = dates.length ? `All tracked repos. Daily snapshots since ${day(dates[0])}.` : "";

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
}

function renderOrgs() {
  const co = DATA.totals.contributor_orgs;
  $("#orgs-card").hidden = !co?.external_people;
  if (!co?.external_people) return;
  const total = co.external_people;
  const ind = co.individuals || { people: 0, contributions: 0 };
  $("#orgs-headline").innerHTML = `<b>${pct((total - ind.people) / total)}</b> from ${fmt(co.orgs.length)} organizations, <b>${pct(ind.people / total)}</b> individuals <span class="dim">· ${fmt(total)} external contributors</span>`;
  const series = [
    { key: "org", label: "Organization", color: "var(--s1)" },
    { key: "ind", label: "Individuals", color: "var(--s2)" },
  ];
  $("#orgs-legend").innerHTML = series.map((s) => `<span style="--c:${s.color}">${s.label}</span>`).join("");
  const rows = co.orgs.slice(0, 15).map((o) => ({ label: o.org, org: o.people, src: o }));
  rows.push({ label: "Individuals", ind: ind.people, src: { ...ind, repos: [] } });
  hbar($("#chart-orgs"), rows, series, {
    format: (v) => pct(v / total),
    tip: (r) => {
      const o = r.src, repos = o.repos || [];
      return `<b>${esc(r.label)}</b><br>People: <b>${fmt(o.people)}</b> (${pct(o.people / total)})<br>Contributions: <b>${fmt(o.contributions)}</b>`
        + (repos.length ? `<br>Repos: ${esc(repos.slice(0, 6).join(", "))}${repos.length > 6 ? ` +${repos.length - 6}` : ""}` : "");
    },
  });
}

function renderFlight() {
  const has = DATA.repos.some((r) => featuresOf(r).length);
  $("#flight-card").hidden = !has;
  if (!has) return;
  const sizes = $("#flight-l").checked ? ["XXL", "XL", "L"] : ["XXL", "XL"];
  const rows = [];
  for (const r of DATA.repos) for (const f of featuresOf(r)) if (inFlight(f) && sizes.includes(f.size)) rows.push({ r, f });
  rows.sort((a, b) => byImpact(a.f, b.f));
  const MAX = 30;
  $("#flight-hint").textContent = `${rows.length} ${sizes.join(", ")} features in progress or planned across all projects${rows.length > MAX ? `, top ${MAX} shown` : ""}. Click one for that project's roadmap.`;
  $("#flight-list").innerHTML = rows.slice(0, MAX).map(({ r, f }) => `
    <li data-jump="${esc(r.name)}" tabindex="0">
      <div>${sizeBadge(f.size)}</div>
      <div><div class="repo">${esc(r.name)}${f.theme ? ` · ${esc(f.theme)}` : ""}</div>
        <div class="title">${esc(f.title)}</div>
        ${f.summary ? `<div class="summary">${esc(f.summary)}</div>` : ""}</div>
      <div class="meta"><span class="status ${esc(f.status)}">${STATUS_LABEL[f.status] || esc(f.status)}</span><br>
        ${fmt((f.refs || []).length)} refs${f.last_activity ? ` · ${ago(f.last_activity)}` : ""}</div>
    </li>`).join("") || `<li><div></div><div class="dim">No features of this size in flight.</div></li>`;
}

let minSize = "M";
const COLS = [["shipped", "Shipped, recent"], ["in_progress", "Now"], ["planned", "Next"], ["proposed", "Later"]];
function renderRoadmaps() {
  const repos = DATA.repos.filter((r) => featuresOf(r).length);
  $("#roadmaps").hidden = !repos.length;
  if (!repos.length) return;
  const open = new Set([...document.querySelectorAll("#roadmap-list details[open]")].map((d) => d.id));
  const repoF = $("#roadmap-repo").value, min = WEIGHT[minSize];
  const card = (r, f) => `
    <div class="fcard">
      <div class="top">${sizeBadge(f.size)}${f.theme ? `<span>${esc(f.theme)}</span>` : ""}${f.milestone ? `<span class="tag">${esc(f.milestone)}</span>` : ""}</div>
      <div class="title">${esc(f.title)}</div>
      ${f.summary ? `<div class="summary">${esc(f.summary)}</div>` : ""}
      ${f.refs?.length ? `<div class="refs">${refLinks(r, f.refs)}</div>` : ""}
    </div>`;
  $("#roadmap-list").innerHTML = repos
    .filter((r) => !repoF || r.name === repoF)
    .sort((a, b) => weightOf(featuresOf(b)) - weightOf(featuresOf(a)))
    .map((r) => {
      const fs = featuresOf(r), info = DATA.features[r.full_name];
      const shown = fs.filter((f) => (WEIGHT[f.size] || 0) >= min);
      const counts = SIZES.map((s) => [s, fs.filter((f) => f.size === s).length]).filter(([, n]) => n)
        .map(([s, n]) => `<span>${sizeBadge(s)}${n}</span>`).join("");
      const owner = r.owner !== "dsx-ai-factory" ? `${esc(r.owner)}/` : "";
      const id = `roadmap-${r.name}`;
      const cols = COLS.map(([st, label]) => {
        const items = shown.filter((f) => f.status === st).sort(byImpact);
        return `<div class="rm-col"><h4>${label} <span class="dim">${items.length}</span></h4>${items.map((f) => card(r, f)).join("") || `<p class="dim">Nothing at this size.</p>`}</div>`;
      }).join("");
      const hidden = fs.length - shown.length;
      return `<details class="rm" id="${esc(id)}"${open.has(id) || repoF ? " open" : ""}>
        <summary><div class="rm-head"><b>${owner}${esc(r.name)}</b><span class="counts">${counts}</span></div>
          ${info.summary ? `<p class="rm-sum">${esc(info.summary)}</p>` : ""}</summary>
        <div class="rm-cols">${cols}</div>
        ${hidden ? `<p class="rm-more">${hidden} smaller ${hidden === 1 ? "feature" : "features"} hidden by the size filter.</p>` : ""}
      </details>`;
    }).join("");
  const gen = DATA.featuresGenerated;
  $("#roadmap-hint").textContent = `Features grouped and sized by Claude from open and recently merged PRs, issues, and milestones${gen ? `, as of ${day(gen)}` : ""}. Now is in progress, Next is planned, Later is proposed.`;
}

let sortKey = "stars", sortAsc = false;
function renderTable() {
  const q = $("#filter").value.trim().toLowerCase();
  const val = (r, k) => (k === "latest_release" ? r.latest_release?.publishedAt || ""
    : k === "name" ? r.name.toLowerCase() : k === "feature_weight" ? flightWeight(r) : r[k] ?? -Infinity);
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
    const top = biggestInFlight(r);
    const flight = top
      ? `${sizeBadge(top.size)}<span class="ft" title="${esc(top.title)}">${esc(top.title)}</span><small>${esc(sizeCounts(featuresOf(r).filter(inFlight))) || "&nbsp;"}</small>`
      : `<span class="dim">–</span>`;
    tr.innerHTML = `
      <td class="l name">${owner}${esc(r.name)}<small>${esc(r.description)}</small></td>
      <td class="l flight">${flight}</td>
      <td>${fmt(r.stars)}</td><td>${d7}</td><td>${fmt(r.forks)}</td>
      <td>${fmt(r.open_prs)}</td><td>${fmt(r.prs_merged_30d)}</td><td>${fmt(r.open_issues)}</td>
      <td>${fmt(r.contributors)}</td><td>${fmt(r.active_external_90d)}</td><td>${fmt(r.commits_30d)}</td>
      <td class="l spark-cell"></td>
      <td class="l">${r.latest_release ? `${esc(r.latest_release.tagName)} <span class="dim">${day(r.latest_release.publishedAt)}</span>` : `<span class="dim">–</span>`}</td>
      <td class="l">${ago(r.pushed_at)}</td>`;
    tr.querySelector(".spark-cell").appendChild(sparkline(r.commits_weekly));
    if (top) tr.querySelector(".flight").addEventListener("click", (e) => { e.stopPropagation(); jumpToRoadmap(r.name); });
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
  const flying = featuresOf(r).filter(inFlight).sort(byImpact).slice(0, 8);
  const roadmap = flying.length ? `<h3>Roadmap: in flight</h3>
    <ul class="feat">${flying.map((f) => `<li>${sizeBadge(f.size)}<span>${esc(f.title)} <span class="dim">${STATUS_LABEL[f.status]}</span></span></li>`).join("")}</ul>
    <p><a href="#roadmap-${esc(r.name)}" data-jump="${esc(r.name)}">Full roadmap →</a></p>` : "";
  let orgs = "";
  if (r.external_orgs) {
    const ext = r.external_orgs.reduce((s, o) => s + o.people, 0) + (r.external_individuals?.people || 0);
    const line = (name, o) => `<li>${esc(name)} <span class="dim">${fmt(o.people)} ${o.people === 1 ? "person" : "people"} (${pct(o.people / ext)}) · ${fmt(o.contributions)} contributions</span></li>`;
    const items = r.external_orgs.slice(0, 12).map((o) => line(o.org, o));
    if (r.external_individuals?.people) items.push(line("Individuals", r.external_individuals));
    if (r.external_orgs.length > 12) items.push(`<li class="dim">+${r.external_orgs.length - 12} more organizations</li>`);
    orgs = `<h3>External contributors by organization, 90d</h3>${items.length ? `<ul>${items.join("")}</ul>` : `<p class="dim">No external contributors in the last 90 days.</p>`}`;
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
    <h3>Recent releases</h3>${releases ? `<ul>${releases}</ul>` : `<p class="dim">No releases yet.</p>`}
    <h3>Related news</h3>${news ? `<ul>${news}</ul>` : `<p class="dim">No classified news tied to this repo yet.</p>`}
    <p class="hint">Created ${day(r.created_at)} · last push ${ago(r.pushed_at)}${r.topics.length ? " · " + r.topics.map(esc).join(", ") : ""}</p>`;
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
  $("#roadmap-repo").innerHTML += DATA.repos.filter((r) => featuresOf(r).length).map((r) => r.name).sort().map((n) => `<option>${esc(n)}</option>`).join("");

  renderKpis();
  renderFlight();
  renderCharts();
  renderTable();
  renderRoadmaps();
  renderNews();
  if (location.hash.startsWith("#roadmap-")) jumpToRoadmap(decodeURIComponent(location.hash.slice(9)));

  $("#filter").addEventListener("input", renderTable);
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
  document.querySelectorAll("#size-seg button").forEach((b) => b.addEventListener("click", () => {
    minSize = b.dataset.size;
    document.querySelectorAll("#size-seg button").forEach((x) => x.classList.toggle("on", x === b));
    renderRoadmaps();
  }));
  $("#roadmap-repo").addEventListener("change", renderRoadmaps);
  $("#flight-l").addEventListener("change", renderFlight);
  // Anything marked data-jump (flight rows, dialog links) opens that repo's roadmap.
  document.addEventListener("click", (e) => {
    const t = e.target.closest("[data-jump]");
    if (t) { e.preventDefault(); jumpToRoadmap(t.dataset.jump); }
  });
  document.addEventListener("keydown", (e) => {
    const t = e.target.closest?.("li[data-jump]");
    if (t && (e.key === "Enter" || e.key === " ")) { e.preventDefault(); jumpToRoadmap(t.dataset.jump); }
  });
  ["#news-cat", "#news-repo", "#news-irrelevant"].forEach((s) => $(s).addEventListener("change", renderNews));
  let resizeTimer;
  addEventListener("resize", () => {
    clearTimeout(resizeTimer);
    resizeTimer = setTimeout(renderCharts, 150);
  });
  $("#d-close").addEventListener("click", () => $("#detail").close());
  $("#detail").addEventListener("click", (e) => { if (e.target.id === "detail") $("#detail").close(); });
})();
