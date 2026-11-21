/* Desktop Audit UI. Vanilla, no build step: the repo should stay clonable
   and runnable with pip install -e . and nothing else. */

const $ = (id) => document.getElementById(id);
const el = (tag, cls, text) => {
  const n = document.createElement(tag);
  if (cls) n.className = cls;
  if (text !== undefined) n.textContent = text;
  return n;
};
const esc = (s) => String(s ?? "");
const fmt = (n, d = 2) =>
  n === null || n === undefined ? "—" : Number(n).toFixed(d);
const num = (n) =>
  n === null || n === undefined ? "—" : Number(n).toLocaleString();

let MODE = "sheet";
let ROWS = [];
let RUN = null;
let DATA = null;
let BASELINE = null;
let FILTER = null;
let SELECTED = null;
let TIMER = null;

/* ------------------------------------------------------------- bootstrap */

async function init() {
  document.querySelectorAll(".tabs.small .tab").forEach((t) =>
    t.addEventListener("click", () => setMode(t.dataset.mode)));
  document.querySelectorAll("#viewTabs .tab").forEach((t) =>
    t.addEventListener("click", () => setView(t.dataset.view)));
  $("runBtn").addEventListener("click", start);
  $("oppInput").addEventListener("change", onPick);
  $("oppFilter").addEventListener("input", renderOppList);
  // Double-click a row to run it, which is one gesture instead of two when
  // working through several sites.
  $("oppInput").addEventListener("dblclick", () => {
    if (selectedId()) start();
  });
  $("ratio").addEventListener("input", onRatio);
  ["adId", "adLat", "adLon", "adRadius", "adRad", "adState"].forEach((id) =>
    $(id).addEventListener("input", updateCli));

  try {
    const r = await fetch("/api/sites").then((x) => x.json());
    ROWS = r.rows || [];
    renderOppList();
    // A source that is missing must say so. Derived sites mean the list is no
    // longer empty when the release sheet is absent, so an absent sheet would
    // otherwise look like a short list rather than a misconfiguration.
    const missing = (r.sources || []).filter((s) => !s.found);
    const broken = (r.sources || []).filter((s) => s.error || s.skipped);
    const notes = [
      ...missing.map((s) => `No ${s.label} at ${s.path}`),
      ...broken.map((s) => s.error
        ? `${s.label}: ${s.error}`
        : `${s.label}: ${s.skipped} row(s) could not be parsed`),
    ];
    if (notes.length) {
      const warn = $("sourceWarning");
      warn.textContent = notes.join(". ") + ".";
      warn.classList.remove("hidden");
    }
  } catch (e) {
    $("rowPreview").textContent = "Could not load the tranche sheet.";
  }
  updateCli();
}

function setMode(m) {
  MODE = m;
  document.querySelectorAll(".tabs.small .tab").forEach((t) =>
    t.classList.toggle("active", t.dataset.mode === m));
  $("modeSheet").classList.toggle("hidden", m !== "sheet");
  $("modeAdhoc").classList.toggle("hidden", m !== "adhoc");
  updateCli();
}

function setView(v) {
  document.querySelectorAll("#viewTabs .tab").forEach((t) =>
    t.classList.toggle("active", t.dataset.view === v));
  ["parcels", "compare", "sensitivity", "unresolved", "rules",
   "evidence", "maps", "assumptions"].forEach((n) =>
    $("view-" + n).classList.toggle("hidden", n !== v));
}

function renderOppList() {
  const q = $("oppFilter").value.trim().toLowerCase();
  const sel = $("oppInput");
  // Tracked separately from sel.value: a filter that matches nothing empties
  // the listbox and clears the DOM value, which would silently drop the
  // operator's choice as soon as they cleared the filter again.
  const keep = sel.value || SELECTED;
  sel.innerHTML = "";
  const rows = q
    ? ROWS.filter((r) => r.opportunity_id.toLowerCase().includes(q))
    : ROWS;
  rows.forEach((row) => {
    const o = el("option");
    o.value = row.opportunity_id;
    // Kept short enough not to clip in the sidebar; the preview underneath
    // carries the full detail once a row is chosen.
    o.textContent =
      `${row.opportunity_id}  ${row.rad_ft}/${row.radius_ft}` +
      (row.has_human_report ? "  ✓" : "") +
      (row.derived ? "  ᴰ" : "");
    o.title =
      `${row.opportunity_id}: ${row.rad_ft} ft RAD, ${num(row.radius_ft)} ft ring` +
      (row.has_human_report ? ", human audit available" : "") +
      (row.derived
        ? ", inputs derived from the human report rather than the release sheet"
        : "");
    sel.appendChild(o);
  });
  if (rows.some((r) => r.opportunity_id === keep)) sel.value = keep;
  else if (rows.length === 1) sel.value = rows[0].opportunity_id;
  if (sel.value) SELECTED = sel.value;
  onPick();
}

function selectedId() {
  return ($("oppInput").value || SELECTED || "").trim().toUpperCase();
}

function currentRow() {
  return ROWS.find((r) => r.opportunity_id === selectedId()) || null;
}

function onPick() {
  if ($("oppInput").value) SELECTED = $("oppInput").value;
  const row = currentRow();
  const p = $("rowPreview");
  if (!row) {
    p.textContent = "Pick a row to see its inputs.";
    p.classList.add("muted");
  } else {
    p.classList.remove("muted");
    p.textContent =
      `${row.latitude}, ${row.longitude}\n` +
      `ring ${num(row.radius_ft)} ft · RAD ${num(row.rad_ft)} ft · ${row.state}` +
      (row.has_human_report ? "\nhuman audit available" : "") +
      (row.derived ? "\ninputs derived from the human report" : "");
    $("compareChk").checked = row.has_human_report;
  }
  updateCli();
}

function updateCli() {
  const c = $("cliHint");
  let cmd = null;
  if (MODE === "sheet") {
    const id = selectedId();
    if (id) cmd = `da run --id ${id}`;
  } else {
    const v = (id) => $(id).value.trim();
    if (v("adLat") && v("adLon") && v("adRadius") && v("adRad")) {
      cmd = `da run --id ${v("adId") || "AD-HOC"} ` +
        `--lat ${v("adLat")} --lon ${v("adLon")} ` +
        `--radius-ft ${v("adRadius")} --rad-ft ${v("adRad")}`;
      const st = v("adState");
      if (st && st.toUpperCase() !== "IA") cmd += ` --state ${st.toUpperCase()}`;
    }
  }
  if (cmd) {
    if ($("skipAsr").checked) cmd += " --skip-asr";
    if ($("skipElev").checked) cmd += " --skip-elevation";
    $("cliText").textContent = cmd;
    c.hidden = false;
  } else {
    c.hidden = true;
  }
}

/* ------------------------------------------------------------------- run */

async function start() {
  const body = {
    skip_asr: $("skipAsr").checked,
    skip_elevation: $("skipElev").checked,
  };
  if (MODE === "sheet") {
    body.opportunity_id = selectedId();
    body.compare = $("compareChk").checked;
    if (!body.opportunity_id) return alert("Select an opportunity.");
  } else {
    body.ad_hoc = true;
    body.opportunity_id = $("adId").value.trim();
    body.lat = $("adLat").value.trim();
    body.lon = $("adLon").value.trim();
    body.radius_ft = $("adRadius").value.trim();
    body.rad_ft = $("adRad").value.trim();
    body.state = $("adState").value.trim() || "IA";
  }

  $("idle").classList.add("hidden");
  $("outcome").classList.add("hidden");
  $("failure").classList.add("hidden");
  $("progress").classList.remove("hidden");
  $("log").textContent = "";
  $("spin").classList.remove("hidden");
  $("progressTitle").textContent = "Running";
  $("runBtn").disabled = true;
  DATA = null; BASELINE = null; FILTER = null;

  const t0 = Date.now();
  TIMER = setInterval(() => {
    $("elapsed").textContent = Math.round((Date.now() - t0) / 1000) + "s";
  }, 500);

  let res;
  try {
    res = await fetch("/api/run", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    }).then((r) => r.json());
  } catch (e) {
    return fail("Could not reach the server. Is it still running?");
  }
  if (res.error) return fail(res.error);
  RUN = res.run_id;
  listen(RUN);
}

function listen(runId) {
  const src = new EventSource(`/api/events/${runId}`);
  src.onmessage = (ev) => {
    const m = JSON.parse(ev.data);
    if (m.type === "log") {
      const line = m.line;
      const node = el("span", /parcels in ring|tower:|recommend|candidate/i
        .test(line) ? "hit" : "", line + "\n");
      $("log").appendChild(node);
      $("log").scrollTop = $("log").scrollHeight;
    } else if (m.type === "done") {
      src.close();
      finish(runId);
    } else if (m.type === "error") {
      src.close();
      fail(m.error, m.traceback);
    }
  };
  src.onerror = () => { src.close(); };
}

async function finish(runId) {
  clearInterval(TIMER);
  $("spin").classList.add("hidden");
  $("progressTitle").textContent = "Complete";
  $("runBtn").disabled = false;
  const data = await fetch(`/api/result/${runId}`).then((r) => r.json());
  if (data.status === "error") return fail(data.error);
  DATA = data;
  BASELINE = { candidates: data.candidates, binding: data.binding_counts };
  render(data);
}

function fail(msg, trace) {
  clearInterval(TIMER);
  $("spin").classList.add("hidden");
  $("runBtn").disabled = false;
  const f = $("failure");
  f.innerHTML = "";
  f.appendChild(el("strong", null, "Run failed"));
  f.appendChild(el("div", null, msg));
  if (trace) {
    const d = el("details");
    d.appendChild(el("summary", null, "Traceback"));
    d.appendChild(el("pre", null, trace));
    f.appendChild(d);
  }
  f.classList.remove("hidden");
}

/* ---------------------------------------------------------------- render */

function render(d) {
  $("outcome").classList.remove("hidden");
  renderVerdict(d);
  renderMetrics(d);
  renderDerivation(d);
  renderParcels(d);
  renderUnresolved(d);
  renderRules(d);
  renderEvidence(d);
  renderMaps(d);
  renderAssumptions(d);
  renderSources(d);

  const hasCmp = !!d.comparison;
  $("tabCompare").hidden = !hasCmp;
  if (hasCmp) renderComparison(d.comparison);
  setView("parcels");

  $("ratio").value = 1;
  $("ratioOut").textContent = "1.00 × height";
  $("setbackOut").textContent = d.tower
    ? `${num(d.tower.overall_height_ft)} ft setback` : "";
  $("sensResult").innerHTML =
    "<p class='muted'>Move the slider to recompute.</p>";

  $("dlBtn").onclick = () => { window.location = `/api/docx/${d.run_id}`; };
}

function renderVerdict(d) {
  const v = $("verdict");
  const rec = d.recommendation || "";
  v.className = "verdict " +
    (/proceed to scip/i.test(rec) ? "go" :
     /unresolved/i.test(rec) ? "open" : "no");
  $("recText").textContent = rec;
  const bits = [`${d.candidates} candidate parcel${d.candidates === 1 ? "" : "s"}`];
  if (!d.meets_target) bits.push(`target is ${d.candidate_target}`);
  if (d.zoning_level) bits.push(`zoning level ${d.zoning_level.level}`);
  bits.push(d.site.opportunity_id);
  if (d.site.input_provenance === "supplied_directly") {
    bits.push("inputs supplied directly");
  }
  if (d.site.input_provenance === "derived_from_report") {
    bits.push("inputs derived from the human report");
  }
  $("recSub").textContent = bits.join(" · ");
}

function renderMetrics(d) {
  const m = $("metrics");
  m.innerHTML = "";
  const add = (v, k) => {
    const n = el("div", "metric");
    n.appendChild(el("div", "v", v));
    n.appendChild(el("div", "k", k));
    m.appendChild(n);
  };
  add(d.parcels.length, "parcels in ring");
  add(d.candidates, "candidates");
  add(d.dispositions?.do_not_pursue ?? 0, "do not pursue");
  add(d.dispositions?.unresolved ?? 0, "unresolved");
  add(d.evidence_count, "evidence records");
  add(d.rules.length, "verified rules");
  add(d.towers_constructed ?? 0, "built towers 2 mi");
  if (d.towers_granted) add(d.towers_granted, "granted, unbuilt");
  add(d.jurisdictions.length, "jurisdictions");
}

function renderDerivation(d) {
  const n = $("derivation");
  if (!d.tower) { n.textContent = "No tower derived."; return; }
  const t = d.tower;
  const dz = t.ground_delta_ft;
  n.innerHTML =
    `RAD <b>${num(t.rad_ft)} ft</b>` +
    ` ${dz >= 0 ? "+" : "−"} Δz <b>${fmt(Math.abs(dz), 1)} ft</b>` +
    ` → round up to 5 → overall height <b>${num(t.overall_height_ft)} ft</b>` +
    ` → <b>${t.tower_type.replace(/_/g, " ")}</b>` +
    (t.guy_anchor_radius_ft
      ? ` → guy anchors at <b>${num(Math.round(t.guy_anchor_radius_ft))} ft</b> radius`
      : "") +
    `<br>No ground-elevation adjustment is applied to the height; it is ` +
    `specific to each candidate location.` +
    `<br>Jurisdictions: <b>${d.jurisdictions.join(", ") || "none determined"}</b>` +
    ` · ring <b>${num(d.site.search_ring_radius_ft)} ft</b>` +
    ` at <b>${d.site.latitude}, ${d.site.longitude}</b>`;
}

function renderParcels(d) {
  const f = $("bindingFilters");
  f.innerHTML = "";
  const counts = d.binding_counts || {};
  const mk = (label, n, key) => {
    const b = el("button", "filter" + (FILTER === key ? " on" : ""),
                 `${label} ${n}`);
    b.onclick = () => { FILTER = FILTER === key ? null : key; renderParcels(DATA); };
    f.appendChild(b);
  };
  mk("All", d.parcels.length, null);
  mk("Candidates", d.candidates, "__pursue");
  Object.entries(counts).sort((a, b) => b[1] - a[1])
    .forEach(([k, n]) => mk(k, n, k));

  let rows = d.parcels;
  if (FILTER === "__pursue") rows = rows.filter((p) => p.disposition === "pursue");
  else if (FILTER) rows = rows.filter((p) => p.binding_constraint === FILTER);

  const t = $("parcelTable");
  t.innerHTML = "";
  const head = el("thead");
  const hr = el("tr");
  ["", "Owner", "District", "Acres", "In ring", "Buildable",
   "Compound", "Disposition", "Binding constraint"]
    .forEach((h) => hr.appendChild(el("th", null, h)));
  head.appendChild(hr);
  t.appendChild(head);

  const tb = el("tbody");
  rows.forEach((p) => {
    const tr = el("tr");
    tr.appendChild(el("td", null, p.letter || ""));

    const owner = el("td");
    owner.appendChild(el("div", null, p.owner || "—"));
    owner.appendChild(el("div", "pin", p.pin || ""));
    tr.appendChild(owner);

    tr.appendChild(el("td", null,
      p.districts.map((x) => x.code).join(", ") || "—"));
    tr.appendChild(el("td", "num", fmt(p.net_acres)));
    tr.appendChild(el("td", "num", fmt(p.in_ring_acres)));
    tr.appendChild(el("td", "num", fmt(p.buildable_acres)));
    tr.appendChild(el("td", null, p.compound_fit || "—"));

    const dcell = el("td");
    const cls = p.disposition === "pursue" ? "pursue"
      : p.disposition === "unresolved" ? "open" : "stop";
    dcell.appendChild(el("span", "pill " + cls,
      (p.disposition || "—").replace(/_/g, " ")));
    tr.appendChild(dcell);

    const bc = el("td");
    if (p.binding_constraint) {
      bc.appendChild(el("span", "pill plain",
        `gate ${p.binding_gate} · ${p.binding_constraint}`));
    } else {
      bc.appendChild(el("span", "muted", "—"));
    }
    if (p.conditions.length) {
      const dd = el("details", "item");
      dd.appendChild(el("summary", null, `${p.conditions.length} condition(s)`));
      const body = el("div", "body");
      p.conditions.forEach((c) => body.appendChild(el("p", null, c)));
      dd.appendChild(body);
      bc.appendChild(dd);
    }
    tr.appendChild(bc);
    tb.appendChild(tr);
  });
  t.appendChild(tb);
}

function renderUnresolved(d) {
  const v = $("view-unresolved");
  v.innerHTML = "";
  if (!d.unresolved.length) {
    v.appendChild(el("p", "muted", "Nothing unresolved."));
    return;
  }
  d.unresolved.forEach((u) => {
    const dd = el("details", "item");
    const s = el("summary");
    s.appendChild(document.createTextNode(u.what_is_missing + " "));
    s.appendChild(el("span", "pill " + (u.blocking ? "stop" : "plain"),
      u.blocking ? "blocking" : "advisory"));
    dd.appendChild(s);
    const body = el("div", "body");
    body.appendChild(el("p", null, "Why it matters: " + u.why_it_matters));
    body.appendChild(el("p", null, "How to resolve: " + u.how_to_resolve));
    dd.appendChild(body);
    v.appendChild(dd);
  });
}

function renderLevels(d, v) {
  const rows = d.jurisdiction_levels || [];
  if (!rows.length) return;
  v.appendChild(el("h3", null, "Zoning level by jurisdiction and district"));
  const t = el("table");
  const head = el("thead");
  const hr = el("tr");
  ["Jurisdiction", "District", "Outcome at this height", "Assignment scale",
   "Audit scale", "Parcels"].forEach((h) => hr.appendChild(el("th", null, h)));
  head.appendChild(hr);
  t.appendChild(head);
  const tb = el("tbody");
  rows.forEach((r) => {
    const tr = el("tr");
    tr.appendChild(el("td", null, r.jurisdiction));
    tr.appendChild(el("td", null, r.district || "not mapped"));
    const why = el("td");
    why.appendChild(el("div", null, r.why));
    const cls = r.permission === "permitted" ? "pursue"
      : r.permission === "prohibited" ? "stop" : "open";
    why.appendChild(el("span", "pill " + cls, r.permission));
    tr.appendChild(why);
    tr.appendChild(el("td", "num", r.level_assignment ?? "\u2014"));
    tr.appendChild(el("td", "num", r.level_audit ?? "\u2014"));
    tr.appendChild(el("td", "num", (r.parcels || []).length));
    tb.appendChild(tr);
  });
  t.appendChild(tb);
  const wrap = el("div", "tablewrap");
  wrap.appendChild(t);
  v.appendChild(wrap);
  v.appendChild(el("p", "muted",
    "Assignment scale: 1 no zoning, 2 admin review, 3 admin review with " +
    "counsel, 4 CUP, SUP, variances. Audit scale (TI-OPP-43986, 37954): " +
    "3 typical CUP, 4 variance, rezoning, text amendment or difficult CUP."));
}

function renderRules(d) {
  const v = $("view-rules");
  v.innerHTML = "";
  renderLevels(d, v);
  const byJur = {};
  d.rules.forEach((r) => (byJur[r.jurisdiction] ||= []).push(r));
  Object.entries(byJur).forEach(([jur, rules]) => {
    v.appendChild(el("h3", null, `${jur} · ${rules.length} rules`));
    rules.forEach((r) => {
      const dd = el("details", "item");
      const s = el("summary");
      s.appendChild(document.createTextNode(
        `${r.district ? r.district + " · " : ""}${r.kind.replace(/_/g, " ")}` +
        `${r.value !== null && r.value !== undefined ? ` · ${r.value}${r.units || ""}` : ""} `));
      s.appendChild(el("span", "sec", "§" + r.section));
      dd.appendChild(s);
      const body = el("div", "body");
      body.appendChild(el("blockquote", "quote", r.quote));
      if (r.url) {
        const a = el("a", null, r.url);
        a.href = r.url; a.target = "_blank"; a.rel = "noopener";
        body.appendChild(a);
      }
      dd.appendChild(body);
      v.appendChild(dd);
    });
  });
}

function renderEvidence(d) {
  const v = $("view-evidence");
  v.innerHTML = "";
  const byType = {};
  d.evidence.forEach((e) => (byType[e.source_type] ||= []).push(e));
  Object.entries(byType).forEach(([type, items]) => {
    const dd = el("details", "item");
    dd.appendChild(el("summary", null, `${type} · ${items.length}`));
    const body = el("div", "body");
    const t = el("table");
    const tb = el("tbody");
    items.slice(0, 400).forEach((e) => {
      const tr = el("tr");
      tr.appendChild(el("td", "pin", e.id));
      const c = el("td");
      c.appendChild(el("div", null, e.description));
      c.appendChild(el("div", "muted", "= " + e.value));
      if (e.quote) c.appendChild(el("blockquote", "quote", e.quote));
      if (e.section) c.appendChild(el("span", "sec", "§" + e.section));
      tr.appendChild(c);
      tb.appendChild(tr);
    });
    t.appendChild(tb);
    body.appendChild(t);
    if (items.length > 400) {
      body.appendChild(el("p", "muted", `${items.length - 400} more not shown.`));
    }
    dd.appendChild(body);
    v.appendChild(dd);
  });
}

function renderMaps(d) {
  const v = $("view-maps");
  v.innerHTML = "";
  if (!d.maps.length) {
    v.appendChild(el("p", "muted", "No maps rendered for this run."));
    return;
  }
  const wrap = el("div", "maps");
  d.maps.forEach((name) => {
    const fig = el("figure");
    const img = el("img");
    img.src = `/api/map/${d.run_id}/${name}`;
    img.alt = `${name} map`;
    img.loading = "lazy";
    fig.appendChild(img);
    fig.appendChild(el("figcaption", null, name));
    wrap.appendChild(fig);
  });
  v.appendChild(wrap);
}

function renderAssumptions(d) {
  const v = $("view-assumptions");
  v.innerHTML = "";
  v.appendChild(el("pre", "json", JSON.stringify(d.assumptions, null, 2)));
}

function renderSources(d) {
  const s = $("sourceStrip");
  s.innerHTML = "";
  const blocked = d.unresolved.filter((u) => /unavailable|could not/i
    .test(u.what_is_missing));
  const tag = (label, ok) => s.appendChild(
    el("span", "src " + (ok ? "ok" : "bad"), label));
  tag(`${d.parcels.length} parcels`, d.parcels.length > 0);
  tag(`${d.rules.length} rules`, d.rules.length > 0);
  tag(`${d.towers_constructed ?? 0} built towers`, true);
  if (blocked.length) tag(`${blocked.length} source issue(s)`, false);
}

/* ----------------------------------------------------------- sensitivity */

let ratioTimer = null;
function onRatio() {
  const r = parseFloat($("ratio").value);
  $("ratioOut").textContent = r.toFixed(2) + " × height";
  if (DATA?.tower) {
    $("setbackOut").textContent =
      `${Math.round(DATA.tower.overall_height_ft * r)} ft setback`;
  }
  clearTimeout(ratioTimer);
  ratioTimer = setTimeout(() => recompute(r), 160);
}

async function recompute(ratio) {
  if (!RUN) return;
  const out = $("sensResult");
  let r;
  try {
    r = await fetch(`/api/sensitivity/${RUN}`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ ratio }),
    }).then((x) => x.json());
  } catch (e) {
    out.textContent = "Recompute failed.";
    return;
  }
  if (r.error) { out.textContent = r.error; return; }

  out.innerHTML = "";
  const base = BASELINE.candidates;
  const diff = r.candidates - base;
  const head = el("div", "metrics");
  const add = (v, k, cls) => {
    const n = el("div", "metric");
    n.appendChild(el("div", "v " + (cls || ""), v));
    n.appendChild(el("div", "k", k));
    head.appendChild(n);
  };
  add(r.candidates, "candidates");
  add((diff > 0 ? "+" : "") + diff, "vs baseline",
      "delta " + (diff > 0 ? "up" : diff < 0 ? "down" : ""));
  add(num(r.setback_ft) + " ft", "setback applied");
  add(r.dispositions?.do_not_pursue ?? 0, "do not pursue");
  out.appendChild(head);

  const t = el("table");
  const head2 = el("thead");
  const hr = el("tr");
  ["", "Owner", "Disposition", "Binding constraint", "Buildable sq ft"]
    .forEach((h) => hr.appendChild(el("th", null, h)));
  head2.appendChild(hr);
  t.appendChild(head2);
  const tb = el("tbody");
  const baseBy = {};
  DATA.parcels.forEach((p) => (baseBy[p.pin] = p.disposition));
  r.parcels.forEach((p) => {
    const tr = el("tr");
    tr.appendChild(el("td", null, p.letter || ""));
    tr.appendChild(el("td", null, p.owner || "—"));
    const dc = el("td");
    const cls = p.disposition === "pursue" ? "pursue"
      : p.disposition === "unresolved" ? "open" : "stop";
    dc.appendChild(el("span", "pill " + cls,
      (p.disposition || "").replace(/_/g, " ")));
    if (baseBy[p.pin] && baseBy[p.pin] !== p.disposition) {
      dc.appendChild(document.createTextNode(" "));
      dc.appendChild(el("span", "sec", "changed"));
    }
    tr.appendChild(dc);
    tr.appendChild(el("td", null, p.binding_constraint || "—"));
    tr.appendChild(el("td", "num", num(p.buildable_sqft)));
    tb.appendChild(tr);
  });
  t.appendChild(tb);
  const wrap = el("div", "tablewrap");
  wrap.appendChild(t);
  out.appendChild(wrap);
}

/* ------------------------------------------------------------ comparison */

function renderComparison(c) {
  const v = $("view-compare");
  v.innerHTML = "";

  const grid = el("div", "cmp-grid");
  const card = (k, human, ours, agree) => {
    const n = el("div", "cmp-card");
    n.appendChild(el("div", "k", k));
    const pair = el("div", "pair");
    pair.appendChild(el("span", null, "human: " + esc(human ?? "—")));
    pair.appendChild(el("span", null, "ours: " + esc(ours ?? "—")));
    if (agree !== null && agree !== undefined) {
      pair.appendChild(el("span", agree ? "agree" : "disagree",
        agree ? "agree" : "differs"));
    }
    n.appendChild(pair);
    grid.appendChild(n);
  };
  card("Recommendation", c.recommendation.human, c.recommendation.ours,
       c.recommendation.agree);
  card("Parcels", c.parcels.human_count, c.parcels.ours_count,
       c.parcels.human_count === c.parcels.ours_count);
  card("Matched", `${c.parcels.matched} of ${c.parcels.human_count}`,
       `${c.parcels.matched} of ${c.parcels.ours_count}`,
       c.parcels.matched === c.parcels.human_count);
  card("Mean acreage error",
       c.parcels.acre_comparable + " comparable",
       c.parcels.mean_acre_error === null ? "—"
         : (c.parcels.mean_acre_error * 100).toFixed(1) + "%",
       c.parcels.mean_acre_error === null ? null
         : c.parcels.mean_acre_error < 0.05);
  card("Zoning level", "—", c.zoning.ours_level, null);
  card("Candidates", "—", c.candidates, null);
  v.appendChild(grid);

  if (c.parcels.owner_changed?.length) {
    const dd = el("details", "item");
    dd.open = true;
    dd.appendChild(el("summary", null,
      `Ownership changed since the human audit · ${c.parcels.owner_changed.length}`));
    const body = el("div", "body");
    body.appendChild(el("p", null,
      "Current county data, not the human report. This is evidence the run " +
      "read the source rather than the audit it is compared against."));
    c.parcels.owner_changed.forEach((o) => body.appendChild(el("p", null, o)));
    dd.appendChild(body);
    v.appendChild(dd);
  }

  const diffs = [];
  if (c.parcels.only_human?.length) {
    diffs.push(["In the human report only", c.parcels.only_human]);
  }
  if (c.parcels.only_ours?.length) {
    diffs.push(["In our output only", c.parcels.only_ours]);
  }
  if (c.zoning.sections_we_did_not_apply?.length) {
    diffs.push(["Sections the human cited that we did not apply",
                c.zoning.sections_we_did_not_apply]);
  }
  diffs.forEach(([title, items]) => {
    const dd = el("details", "item");
    dd.appendChild(el("summary", null, `${title} · ${items.length}`));
    const body = el("div", "body");
    items.forEach((i) => body.appendChild(el("p", null, i)));
    dd.appendChild(body);
    v.appendChild(dd);
  });

  const t = el("table");
  const head = el("thead");
  const hr = el("tr");
  ["Human", "Ours", "Owner", "Human acres", "Our acres", "Disposition", "Note"]
    .forEach((h) => hr.appendChild(el("th", null, h)));
  head.appendChild(hr);
  t.appendChild(head);
  const tb = el("tbody");
  (c.parcel_rows || []).forEach((r) => {
    const tr = el("tr");
    tr.appendChild(el("td", null, r.human_letter || "—"));
    tr.appendChild(el("td", null, r.ours_letter || "—"));
    const o = el("td");
    o.appendChild(el("div", null, r.owner || "—"));
    if (r.ours_owner && r.owner && r.ours_owner !== r.owner) {
      o.appendChild(el("div", "pin", "now: " + r.ours_owner));
    }
    tr.appendChild(o);
    tr.appendChild(el("td", "num", fmt(r.human_acres)));
    tr.appendChild(el("td", "num", fmt(r.ours_acres)));
    tr.appendChild(el("td", null, (r.ours_disposition || "—").replace(/_/g, " ")));
    tr.appendChild(el("td", "muted", r.note || ""));
    tb.appendChild(tr);
  });
  t.appendChild(tb);
  const wrap = el("div", "tablewrap");
  wrap.appendChild(t);
  v.appendChild(wrap);
}

init();
