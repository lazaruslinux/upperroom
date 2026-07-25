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
    [vods.length, vods.length === 1 ? "recording" : "recordings"],
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

async function boot() {
  if (!(await requireAdmin())) return;
  mountNav(me, { current: "analytics" });
  const [users, vods, clips, invites, retention] = await Promise.all([
    getJSON("/api/admin/users"),
    getJSON("/api/vods"),
    getJSON("/api/clips"),
    getJSON("/api/admin/invites"),
    getJSON("/api/admin/retention"),
  ]);
  renderPeople((users && users.users) || []);
  const vodList = (vods && vods.vods) || [];
  const clipList = (clips && clips.clips) || [];
  renderBroadcasts(vodList);
  renderLibrary(vodList, clipList, retention);
  renderInvites((invites && invites.invites) || []);
}

boot();
