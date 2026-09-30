// Stats: the numbers the app already keeps, in one place.
//
// No new backend and no new tracking. Everything here is composed from
// endpoints that already exist, so the page costs nothing to run and shows
// history from before it was written. What it cannot show is anything the app
// never recorded: there is no series of people watching at once, because
// presence lives in memory and is never written down, and no watch time for
// recordings, because a view is counted once and its length is not measured.
//
// The charts are one system: a column per day in one ink, one axis each (the
// three measures have nothing in common to share a scale), a clean top tick,
// the peak labelled and nothing else, a tooltip on hover and on the arrow
// keys, and every value in a table underneath for anyone who wants it plain.

let me = null;               // this browser's identity, for the strip

// The dashboard's sections, which this page is the last of. The same list
// admin.js hands the strip.
const SECTIONS = [
  { key: "golive", label: "Go live", href: "/admin#golive" },
  { key: "people", label: "People", href: "/admin#people" },
  { key: "library", label: "Library", href: "/admin#library" },
  { key: "channel", label: "Channel", href: "/admin#channel" },
  { key: "chat", label: "Chat rules", href: "/admin#chat" },
  { key: "connections", label: "Connections", href: "/admin#connections" },
  { key: "stats", label: "Stats", href: "/analytics" },
];

// ---- small helpers (private copies, as every other page keeps) ----

function $(id) { return document.getElementById(id); }

function formatDuration(secs) {
  if (!secs || secs < 60) return `${secs || 0}s`;
  const hours = Math.floor(secs / 3600);
  const mins = Math.floor((secs % 3600) / 60);
  if (hours > 0) return `${hours}h ${mins}m`;
  return `${mins}m`;
}

function durationClock(secs) {
  secs = Math.max(0, Math.round(secs || 0));
  const h = Math.floor(secs / 3600);
  const m = Math.floor((secs % 3600) / 60);
  const s = secs % 60;
  const pad = (n) => String(n).padStart(2, "0");
  return h > 0 ? `${h}:${pad(m)}:${pad(s)}` : `${m}:${pad(s)}`;
}

function formatBytes(bytes) {
  if (!bytes) return "0 B";
  const units = ["B", "KB", "MB", "GB", "TB"];
  let n = bytes;
  let i = 0;
  while (n >= 1024 && i < units.length - 1) { n /= 1024; i++; }
  return `${n >= 10 || i === 0 ? Math.round(n) : n.toFixed(1)} ${units[i]}`;
}

function textNode(tag, cls, text) {
  const el = document.createElement(tag);
  if (cls) el.className = cls;
  el.textContent = text;
  return el;
}

// A readout: an engraved label over a number.
function fillMeters(id, meters) {
  const row = $(id);
  row.textContent = "";
  meters.forEach(([value, label]) => {
    const meter = document.createElement("div");
    meter.className = "meter";
    meter.append(textNode("span", "engraved", label), textNode("strong", "", String(value)));
    row.appendChild(meter);
  });
}

function rowNode() {
  const row = document.createElement("li");
  row.className = "row";
  const main = document.createElement("div");
  main.className = "row-main";
  row.appendChild(main);
  return { row, main };
}

async function getJSON(url) {
  try {
    const reply = await fetch(url);
    if (!reply.ok) return null;
    return await reply.json();
  } catch {
    return null;
  }
}

async function requireAdmin() {
  let data;
  try { data = await (await fetch("/api/me")).json(); } catch { data = { authed: false }; }
  if (!data.authed) { window.location.href = "/"; return false; }
  if (!data.admin) { window.location.href = "/home"; return false; }
  me = data;
  return true;
}

// ---- the readouts and the lists ----

function renderPeople(users) {
  const admins = users.filter((u) => u.is_admin).length;
  const watch = users.reduce((sum, u) => sum + (u.watch_seconds || 0), 0);
  const messages = users.reduce((sum, u) => sum + (u.messages || 0), 0);
  fillMeters("people-meters", [
    [users.length, users.length === 1 ? "Account" : "Accounts"],
    [admins, admins === 1 ? "Admin" : "Admins"],
    [formatDuration(watch), "Watch time"],
    // Chat is purged on a timer, so this is a rolling window rather than a
    // lifetime total, and the label says so.
    [messages, "Messages, 7 days"],
  ]);

  const board = $("watch-board");
  const ranked = users
    .filter((u) => u.watch_seconds > 0)
    .sort((a, b) => b.watch_seconds - a.watch_seconds);
  $("watch-empty").hidden = ranked.length > 0;
  board.textContent = "";
  ranked.forEach((u) => {
    const { row, main } = rowNode();
    main.append(
      textNode("span", "row-title", u.display_name),
      textNode("span", "row-meta", `@${u.username} · ${u.messages} messages`),
    );
    row.appendChild(textNode("span", "row-value", formatDuration(u.watch_seconds)));
    board.appendChild(row);
  });
}

function renderBroadcasts(vods) {
  const list = $("broadcasts");
  $("broadcast-empty").hidden = vods.length > 0;
  list.textContent = "";
  vods.forEach((v) => {
    const { row, main } = rowNode();
    const link = document.createElement("a");
    link.className = "row-title";
    link.href = `/media?type=vod&id=${v.id}`;
    link.textContent = v.title || "Live Stream";
    main.append(link, textNode("span", "row-meta",
      new Date(v.started_at * 1000).toLocaleString([], {
        month: "short", day: "numeric", hour: "numeric", minute: "2-digit",
      })));
    row.appendChild(textNode("span", "row-value",
      `${durationClock(v.duration)} · ${v.views} ${v.views === 1 ? "view" : "views"}`));
    list.appendChild(row);
  });
}

function renderLibrary(vods, clips, retention) {
  const views = [...vods, ...clips].reduce((sum, m) => sum + (m.views || 0), 0);
  const usage = retention && retention.usage ? retention.usage : {};
  fillMeters("library-meters", [
    [vods.length, vods.length === 1 ? "Broadcast" : "Broadcasts"],
    [clips.length, clips.length === 1 ? "Clip" : "Clips"],
    [views, "Views"],
    // The store may be away; an unknown size is not zero.
    [usage.available === false ? "Unknown" : formatBytes(usage.total_bytes || 0), "Stored"],
  ]);
}

function renderInvites(invites) {
  const redeemed = invites.filter((i) => i.redeemed_at).length;
  const revoked = invites.filter((i) => !i.redeemed_at && i.revoked_at).length;
  const active = invites.length - redeemed - revoked;
  fillMeters("invite-meters", [
    [invites.length, "Made"],
    [redeemed, "Used"],
    [active, "Still open"],
    [revoked, "Revoked"],
  ]);
}

// ---- one column per day -----------------------------------------------------

const SVGNS = "http://www.w3.org/2000/svg";
const PLOT_H = 180;

function svgEl(name, attrs) {
  const el = document.createElementNS(SVGNS, name);
  for (const k in attrs) el.setAttribute(k, attrs[k]);
  return el;
}

function dayLabel(iso) {
  return new Date(`${iso}T00:00:00Z`).toLocaleDateString([], {
    month: "short", day: "numeric", timeZone: "UTC",
  });
}

// The top gridline sits on a round number, so the one scale reads at a
// glance: 1, 2 or 5 times a power of ten, at or above the peak.
function niceCeil(value) {
  if (value <= 0) return 1;
  const power = 10 ** Math.floor(Math.log10(value));
  for (const step of [1, 2, 5, 10]) {
    if (step * power >= value) return step * power;
  }
  return 10 * power;
}

// A column with its data end rounded and its foot square on the baseline.
function columnPath(x, y, w, h) {
  const r = Math.min(4, w / 2, h);
  return `M${x} ${y + h}V${y + r}Q${x} ${y} ${x + r} ${y}H${x + w - r}Q${x + w} ${y} ${x + w} ${y + r}V${y + h}Z`;
}

function drawChart(plot, series, spec) {
  plot.textContent = "";
  const width = Math.max(240, plot.clientWidth);
  const padL = 36, padR = 4, padT = 20, padB = 2;
  const innerW = width - padL - padR;
  const innerH = PLOT_H - padT - padB;
  const top = niceCeil(Math.max(...series.map((d) => d.value)));
  const band = innerW / series.length;
  const barW = Math.max(2, Math.min(24, band - 2));
  const y = (v) => padT + (1 - v / top) * innerH;

  const svg = svgEl("svg", {
    class: "chart-svg", width, height: PLOT_H, viewBox: `0 0 ${width} ${PLOT_H}`,
    role: "img", tabindex: "0",
    "aria-label": `${spec.title}. Use the arrow keys to read each day; the table below has them all.`,
  });

  // Three recessive gridlines: the baseline, halfway and the round top.
  [0, top / 2, top].forEach((v) => {
    const gy = Math.round(y(v)) + 0.5;
    svg.appendChild(svgEl("line", { class: "grid", x1: padL, x2: width - padR, y1: gy, y2: gy }));
    const tick = svgEl("text", { class: "tick", x: padL - 8, y: gy + 4, "text-anchor": "end" });
    tick.textContent = spec.tick(v);
    svg.appendChild(tick);
  });

  let peak = 0;
  series.forEach((d, i) => { if (d.value > series[peak].value) peak = i; });

  const cols = series.map((d, i) => {
    const g = svgEl("g", { class: "col" });
    const cx = padL + band * i + band / 2;
    if (d.value > 0) {
      const h = Math.max(1, y(0) - y(d.value));
      g.appendChild(svgEl("path", { class: "bar", d: columnPath(cx - barW / 2, y(0) - h, barW, h) }));
    }
    // The hit target is the whole day's slot, far bigger than a thin column.
    g.appendChild(svgEl("rect", { class: "hit", x: padL + band * i, y: padT, width: band, height: innerH }));
    svg.appendChild(g);
    return { g, cx, d };
  });

  // The one direct label: the peak, on its cap.
  const best = cols[peak];
  const label = svgEl("text", { class: "peak", x: best.cx, y: y(best.d.value) - 6, "text-anchor": "middle" });
  label.textContent = spec.short(best.d.value);
  svg.appendChild(label);

  plot.appendChild(svg);

  const tip = document.createElement("div");
  tip.className = "chart-tip";
  tip.hidden = true;
  tip.setAttribute("aria-hidden", "true");
  plot.appendChild(tip);
  const live = document.createElement("p");
  live.className = "sr-only";
  live.setAttribute("aria-live", "polite");
  plot.appendChild(live);

  let hot = -1;
  function show(i) {
    if (hot >= 0) cols[hot].g.classList.remove("is-hot");
    hot = i;
    if (i < 0) { tip.hidden = true; return; }
    const col = cols[i];
    col.g.classList.add("is-hot");
    tip.textContent = "";
    tip.append(textNode("strong", "", spec.long(col.d.value)), textNode("span", "", dayLabel(col.d.date)));
    tip.hidden = false;
    const left = Math.min(Math.max(0, col.cx - tip.offsetWidth / 2), width - tip.offsetWidth);
    tip.style.left = `${left}px`;
    tip.style.top = `${Math.max(0, y(col.d.value) - tip.offsetHeight - 10)}px`;
    live.textContent = `${dayLabel(col.d.date)}: ${spec.long(col.d.value)}`;
  }
  cols.forEach((col, i) => {
    col.g.addEventListener("pointerenter", () => show(i));
  });
  svg.addEventListener("pointerleave", () => show(-1));
  svg.addEventListener("focus", () => show(hot >= 0 ? hot : series.length - 1));
  svg.addEventListener("blur", () => show(-1));
  svg.addEventListener("keydown", (e) => {
    if (e.key === "ArrowLeft") { e.preventDefault(); show(Math.max(0, hot - 1)); }
    else if (e.key === "ArrowRight") { e.preventDefault(); show(Math.min(series.length - 1, hot + 1)); }
    else if (e.key === "Home") { e.preventDefault(); show(0); }
    else if (e.key === "End") { e.preventDefault(); show(series.length - 1); }
  });
}

function chartFigure(series, spec) {
  const figure = document.createElement("figure");
  figure.className = "chart";
  const head = document.createElement("figcaption");
  head.className = "chart-head";
  head.appendChild(textNode("span", "chart-title", spec.title));
  const total = spec.total(series);
  if (total) head.appendChild(textNode("span", "chart-total", total));
  figure.appendChild(head);

  if (!series.some((d) => d.value > 0)) {
    // A row of zeros is "nothing yet", not a chart of a flat line.
    figure.appendChild(textNode("p", "chart-empty", spec.empty));
    return figure;
  }

  const plot = document.createElement("div");
  plot.className = "chart-plot";
  figure.appendChild(plot);
  const axis = document.createElement("div");
  axis.className = "chart-axis";
  axis.setAttribute("aria-hidden", "true");
  axis.append(textNode("span", "", dayLabel(series[0].date)),
    textNode("span", "", dayLabel(series[series.length - 1].date)));
  figure.appendChild(axis);
  if (spec.note) figure.appendChild(textNode("p", "chart-note", spec.note));

  // Every value, plainly, for anyone who would rather not hover.
  const details = document.createElement("details");
  details.className = "chart-table";
  details.appendChild(textNode("summary", "", "Show as a table"));
  const table = document.createElement("table");
  const headRow = document.createElement("tr");
  headRow.append(textNode("th", "", "Day"), textNode("th", "", spec.column));
  const thead = document.createElement("thead");
  thead.appendChild(headRow);
  const tbody = document.createElement("tbody");
  series.forEach((d) => {
    const tr = document.createElement("tr");
    tr.append(textNode("td", "", dayLabel(d.date)), textNode("td", "", String(d.value)));
    tbody.appendChild(tr);
  });
  table.append(thead, tbody);
  details.appendChild(table);
  figure.appendChild(details);

  // Drawn at the width it actually has, so the text stays crisp and a column
  // stays a column; drawn again if that width changes.
  requestAnimationFrame(() => drawChart(plot, series, spec));
  let lastWidth = 0;
  new ResizeObserver(() => {
    if (Math.abs(plot.clientWidth - lastWidth) < 2) return;
    lastWidth = plot.clientWidth;
    drawChart(plot, series, spec);
  }).observe(plot);
  return figure;
}

function minutesLong(m) {
  if (m < 60) return `${m} ${m === 1 ? "minute" : "minutes"}`;
  return formatDuration(m * 60);
}

function renderCharts(days) {
  const host = $("charts");
  host.textContent = "";
  if (!days || !days.length) {
    host.appendChild(textNode("p", "chart-empty", "Nothing yet."));
    return;
  }
  const pick = (key) => days.map((d) => ({ date: d.date, value: d[key] || 0 }));
  // Halfway up a small scale can fall between two whole numbers; say so
  // rather than round the gridline to a value it is not at.
  const count = (n) => (Number.isInteger(n) ? String(n) : n.toFixed(1));
  host.appendChild(chartFigure(pick("watch_minutes"), {
    title: "Watch time each day",
    column: "Minutes",
    empty: "Nobody has watched in the last 30 days.",
    total: (s) => `${formatDuration(s.reduce((a, d) => a + d.value, 0) * 60)} in all`,
    tick: (v) => (v >= 60 ? `${Math.round(v / 60 * 10) / 10}h` : `${Number.isInteger(v) ? v : v.toFixed(1)}m`),
    short: (v) => (v >= 60 ? formatDuration(v * 60) : `${v}m`),
    long: minutesLong,
  }));
  host.appendChild(chartFigure(pick("viewers"), {
    title: "People who watched each day",
    column: "People",
    empty: "Nobody has watched in the last 30 days.",
    // Each day counts a person once; adding the days up would count a regular
    // thirty times, so the headline is the busiest day instead.
    total: (s) => `busiest day ${Math.max(...s.map((d) => d.value))}`,
    tick: count,
    short: count,
    long: (v) => `${v} ${v === 1 ? "person" : "people"}`,
  }));
  host.appendChild(chartFigure(pick("messages"), {
    title: "Chat messages each day",
    column: "Messages",
    empty: "No chat in the days that are kept.",
    // Chat is purged after the retention window (7 days by default), so the
    // older days read zero because they are gone, not because it was quiet.
    note: "Chat is only kept for a few days, so older days read zero.",
    total: (s) => `${s.reduce((a, d) => a + d.value, 0)} in all`,
    tick: count,
    short: count,
    long: (v) => `${v} ${v === 1 ? "message" : "messages"}`,
  }));
}

async function boot() {
  if (!(await requireAdmin())) return;
  mountNav(me, { current: "analytics", sections: SECTIONS, section: "stats", pageName: "Stats" });
  const [users, vods, clips, invites, retention, activity] = await Promise.all([
    getJSON("/api/admin/users"),
    getJSON("/api/vods"),
    getJSON("/api/clips"),
    getJSON("/api/admin/invites"),
    getJSON("/api/admin/retention"),
    getJSON("/api/admin/activity?days=30"),
  ]);
  renderPeople((users && users.users) || []);
  const vodList = (vods && vods.vods) || [];
  const clipList = (clips && clips.clips) || [];
  renderBroadcasts(vodList);
  renderLibrary(vodList, clipList, retention);
  renderInvites((invites && invites.invites) || []);
  renderCharts((activity && activity.days) || []);
}

boot();
