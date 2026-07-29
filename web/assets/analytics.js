// Analytics: the numbers the app already keeps, in one place.
//
// Deliberately no new backend and no new tracking. Everything here is composed
// from endpoints that already exist, so the page costs nothing to run and shows
// history from before it was written. What it cannot show is anything the app
// never recorded: there is no time series of concurrent viewers, because
// presence lives in memory and is never written down, and no watch time for
// VODs, because a view is counted once and its duration is not measured.

let me = null;               // this browser's identity, for the shared nav

// ---- small helpers (private copies, as every other page keeps) ----

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

function statCard(value, label) {
  const card = document.createElement("div");
  card.className = "stat-card is-static";
  const v = document.createElement("span");
  v.className = "stat-value";
  v.textContent = value;
  const l = document.createElement("span");
  l.className = "stat-label";
  l.textContent = label;
  card.append(v, l);
  return card;
}

function fillStrip(id, cards) {
  const strip = document.getElementById(id);
  strip.innerHTML = "";
  cards.forEach(([value, label]) => strip.appendChild(statCard(value, label)));
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

// ---- load + render ----

async function requireAdmin() {
  let data;
  try { data = await (await fetch("/api/me")).json(); } catch { data = { authed: false }; }
  if (!data.authed) { window.location.href = "/"; return false; }
  // A guest pass buys the stream and chat, nothing else on the site.
  // Send them where their pass actually works rather than rendering a
  // page whose every request will 401.
  if (data.guest) { window.location.href = "/watch"; return false; }
  if (!data.admin) { window.location.href = "/home"; return false; }
  me = data;
  return true;
}

function renderPeople(users) {
  const admins = users.filter((u) => u.is_admin).length;
  const watch = users.reduce((sum, u) => sum + (u.watch_seconds || 0), 0);
  const messages = users.reduce((sum, u) => sum + (u.messages || 0), 0);
  fillStrip("stat-strip", [
    [users.length, users.length === 1 ? "account" : "accounts"],
    [admins, admins === 1 ? "admin" : "admins"],
    [formatDuration(watch), "watch time"],
    // Chat is purged on a timer, so this is a rolling window rather than a
    // lifetime total. Say so rather than implying it is everything.
    [messages, "messages (last 7 days)"],
  ]);

  const board = document.getElementById("watch-board");
  const ranked = users
    .filter((u) => u.watch_seconds > 0)
    .sort((a, b) => b.watch_seconds - a.watch_seconds);
  document.getElementById("watch-empty").hidden = ranked.length > 0;
  board.innerHTML = "";
  ranked.forEach((u) => {
    const row = document.createElement("div");
    row.className = "activity-row ban-row";
    const left = document.createElement("span");
    left.innerHTML = `<b></b> <span class="muted"></span>`;
    left.querySelector("b").textContent = u.display_name;
    left.querySelector(".muted").textContent = `@${u.username} · ${u.messages} messages`;
    const right = document.createElement("span");
    right.className = "act-dur";
    right.textContent = formatDuration(u.watch_seconds);
    row.append(left, right);
    board.appendChild(row);
  });
}

function renderBroadcasts(vods) {
  const list = document.getElementById("broadcasts");
  document.getElementById("broadcast-empty").hidden = vods.length > 0;
  list.innerHTML = "";
  vods.forEach((v) => {
    const row = document.createElement("div");
    row.className = "activity-row ban-row";
    const left = document.createElement("span");
    left.innerHTML = `<a></a> <span class="muted"></span>`;
    const link = left.querySelector("a");
    link.href = `/media?type=vod&id=${v.id}`;
    link.textContent = v.title || "Live Stream";
    left.querySelector(".muted").textContent =
      new Date(v.started_at * 1000).toLocaleString([], {
        month: "short", day: "numeric", hour: "numeric", minute: "2-digit",
      });
    const right = document.createElement("span");
    right.className = "act-dur";
    right.textContent = `${durationClock(v.duration)} · ${v.views} views`;
    row.append(left, right);
    list.appendChild(row);
  });
}

function renderLibrary(vods, clips, retention) {
  const views = [...vods, ...clips].reduce((sum, m) => sum + (m.views || 0), 0);
  const usage = retention && retention.usage ? retention.usage : {};
  fillStrip("library-strip", [
    [vods.length, vods.length === 1 ? "broadcast" : "broadcasts"],
    [clips.length, clips.length === 1 ? "clip" : "clips"],
    [views, "views"],
    [formatBytes(usage.total_bytes || 0), "stored"],
  ]);
}

function renderInvites(invites) {
  const redeemed = invites.filter((i) => i.redeemed_at).length;
  const revoked = invites.filter((i) => !i.redeemed_at && i.revoked_at).length;
  const active = invites.length - redeemed - revoked;
  fillStrip("invite-strip", [
    [invites.length, "generated"],
    [redeemed, "redeemed"],
    [active, "still active"],
    [revoked, "revoked"],
  ]);
}

// ---- over-time line charts ------------------------------------------------
// Hand-rolled inline SVG, no chart library (the repo is self-contained). Each
// chart is theme-aware through the same CSS variables the rest of the chrome
// uses (accent line, border baseline, muted axis), scales to its column via a
// responsive viewBox, carries a per-point <title> tooltip, and keeps square
// corners throughout.

const SVGNS = "http://www.w3.org/2000/svg";

function svgEl(name, attrs) {
  const el = document.createElementNS(SVGNS, name);
  for (const k in attrs) el.setAttribute(k, attrs[k]);
  return el;
}

function lineChart(title, series, unit) {
  const figure = document.createElement("figure");
  figure.className = "chart";
  const caption = document.createElement("figcaption");
  caption.textContent = title;
  figure.appendChild(caption);

  const values = series.map((d) => d.value);
  const max = values.length ? Math.max(...values) : 0;
  // A flat run of zeros is "nothing yet", not a chart of a straight line, so keep
  // the muted empty pattern the rest of the page uses.
  if (!series.length || max <= 0) {
    const empty = document.createElement("p");
    empty.className = "muted chart-empty";
    empty.textContent = "Nothing yet.";
    figure.appendChild(empty);
    return figure;
  }

  const W = 600, H = 200;
  const padL = 6, padR = 6, padT = 12, padB = 10;
  const innerW = W - padL - padR;
  const innerH = H - padT - padB;
  const n = series.length;
  const x = (i) => (n === 1 ? W / 2 : padL + (i / (n - 1)) * innerW);
  const y = (v) => padT + (1 - v / max) * innerH;
  const baseY = y(0);

  const svg = svgEl("svg", {
    viewBox: `0 0 ${W} ${H}`, class: "chart-svg", role: "img",
    "aria-label": title,
  });

  // Baseline (x axis), muted 1px.
  svg.appendChild(svgEl("line", {
    x1: padL, y1: baseY, x2: W - padR, y2: baseY,
    stroke: "var(--border)", "stroke-width": 1,
  }));

  // The data line: accent, mitered and butt-capped for the square aesthetic, and
  // a non-scaling stroke so it stays crisp at any rendered width.
  const d = series
    .map((pt, i) => `${i === 0 ? "M" : "L"} ${x(i).toFixed(1)} ${y(pt.value).toFixed(1)}`)
    .join(" ");
  svg.appendChild(svgEl("path", {
    d, fill: "none", stroke: "var(--accent)", "stroke-width": 2,
    "stroke-linejoin": "miter", "stroke-linecap": "butt",
    "vector-effect": "non-scaling-stroke",
  }));

  // Square marks, each carrying a native tooltip.
  series.forEach((pt, i) => {
    const s = 4;
    const rect = svgEl("rect", {
      x: (x(i) - s / 2).toFixed(1), y: (y(pt.value) - s / 2).toFixed(1),
      width: s, height: s, fill: "var(--accent)",
    });
    const tip = document.createElementNS(SVGNS, "title");
    tip.textContent = `${pt.date} · ${pt.value === 1 ? `1 ${unit}` : `${pt.value} ${unit}s`}`;
    rect.appendChild(tip);
    svg.appendChild(rect);
  });

  figure.appendChild(svg);

  // First and last dates only, so the axis does not crowd; the rest is on the
  // per-point tooltips.
  const axis = document.createElement("div");
  axis.className = "chart-axis muted";
  const first = document.createElement("span");
  first.textContent = series[0].date;
  const last = document.createElement("span");
  last.textContent = series[series.length - 1].date;
  axis.append(first, last);
  figure.appendChild(axis);

  return figure;
}

function renderCharts(days) {
  const host = document.getElementById("charts");
  host.innerHTML = "";
  if (!days || !days.length) {
    const empty = document.createElement("p");
    empty.className = "muted";
    empty.textContent = "Nothing yet.";
    host.appendChild(empty);
    return;
  }
  host.appendChild(lineChart(
    "Watch time per day (minutes)",
    days.map((d) => ({ date: d.date, value: d.watch_minutes })), "minute"));
  host.appendChild(lineChart(
    "Unique viewers per day",
    days.map((d) => ({ date: d.date, value: d.viewers })), "viewer"));
  // Chat is purged after the retention window (7 days by default), so older days
  // legitimately read zero; the title says so rather than implying chat stopped.
  host.appendChild(lineChart(
    "Chat messages per day (last 7 days kept)",
    days.map((d) => ({ date: d.date, value: d.messages })), "message"));
}

async function boot() {
  if (!(await requireAdmin())) return;
  mountNav(me, { current: "analytics" });
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
