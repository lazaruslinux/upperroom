// Playback page for a saved VOD or a clip, with chat replayed in sync. Driven by
// the query string, e.g. /media?type=vod&id=12 or /media?type=clip&id=5.

const params = new URLSearchParams(location.search);
const TYPE = params.get("type") === "clip" ? "clip" : "vod";
const ID = parseInt(params.get("id"), 10);

const video = document.getElementById("video");
const titleEl = document.getElementById("media-title");
const subEl = document.getElementById("media-sub");
const descEl = document.getElementById("media-desc");
const replayMessages = document.getElementById("replay-messages");
const replayEmpty = document.getElementById("replay-empty");
const replayToggle = document.getElementById("replay-toggle");

let replay = [];        // chat lines, sorted by offset_s
let shownIdx = 0;       // how many have been revealed
let lastT = 0;
let replayOn = true;
let viewCounted = false;

// ---- shared render helpers (kept local to this page) ----

const FONTS = {
  system: "",
  mono: "'Roboto Mono', monospace",
  comic: "'Comic Neue', cursive",
  retro: "'VT323', monospace",
  caveat: "'Caveat', cursive",
};

function avatarColor(seed) {
  let hash = 0;
  for (let i = 0; i < seed.length; i++) hash = (hash * 31 + seed.charCodeAt(i)) % 360;
  return `hsl(${hash}, 55%, 45%)`;
}

function avatarNode(username, name, version) {
  if (version) {
    const img = document.createElement("img");
    img.className = "avatar";
    img.alt = "";
    img.src = `/api/avatar/${encodeURIComponent(username)}?v=${version}`;
    return img;
  }
  const span = document.createElement("span");
  span.className = "avatar";
  span.textContent = (name || username || "?").trim().charAt(0).toUpperCase();
  span.style.background = avatarColor(username || "?");
  return span;
}

// Square mono text tag: amber "op" for admin, blue "mod" for moderator.
function roleBadgeNode(admin, mod) {
  if (!admin && !mod) return null;
  const span = document.createElement("span");
  span.className = "role-tag " + (admin ? "op" : "mod");
  span.textContent = admin ? "op" : "mod";
  return span;
}

function clock(secs) {
  secs = Math.max(0, Math.floor(secs || 0));
  const m = Math.floor(secs / 60);
  const s = secs % 60;
  return `${m}:${String(s).padStart(2, "0")}`;
}

function relDate(epoch) {
  if (!epoch) return "";
  const secs = Math.floor(Date.now() / 1000) - epoch;
  if (secs < 3600) return `${Math.max(1, Math.floor(secs / 60))}m ago`;
  if (secs < 86400) return `${Math.floor(secs / 3600)}h ago`;
  if (secs < 2592000) return `${Math.floor(secs / 86400)}d ago`;
  return new Date(epoch * 1000).toLocaleDateString();
}

function appendReplayLine(msg) {
  const line = document.createElement("div");
  line.className = "msg";
  line.appendChild(avatarNode(msg.username, msg.display_name, msg.avatar_version || 0));
  const badge = roleBadgeNode(msg.admin, msg.moderator);
  if (badge) line.appendChild(badge);
  const wrap = document.createElement("span");
  wrap.className = "msg-body";
  const head = document.createElement("span");
  head.className = "msg-head";
  const name = document.createElement("span");
  name.className = msg.admin ? "name admin" : "name";
  name.textContent = msg.display_name;
  const time = document.createElement("span");
  time.className = "msg-time";
  time.textContent = clock(msg.offset_s);
  head.append(name, time);
  const body = document.createElement("span");
  body.className = "body";
  if (msg.deleted) {
    body.textContent = "deleted by a moderator";
    body.classList.add("deleted");
  } else {
    body.textContent = msg.text;
    body.style.fontFamily = FONTS[msg.font] || "";
  }
  wrap.append(head, body);
  line.appendChild(wrap);
  replayMessages.appendChild(line);
  replayMessages.scrollTop = replayMessages.scrollHeight;
}

// ---- replay sync ----

function revealUpTo(t) {
  while (shownIdx < replay.length && replay[shownIdx].offset_s <= t) {
    appendReplayLine(replay[shownIdx]);
    shownIdx++;
  }
}

function resetTo(t) {
  replayMessages.innerHTML = "";
  shownIdx = 0;
  if (replayOn) revealUpTo(t);
}

video.addEventListener("timeupdate", () => {
  if (!replayOn) return;
  const t = video.currentTime;
  if (t + 0.5 < lastT) resetTo(t);   // jumped backward
  else revealUpTo(t);
  lastT = t;
  countView();
});
video.addEventListener("seeking", () => { if (replayOn) resetTo(video.currentTime); });

replayToggle.addEventListener("click", () => {
  replayOn = !replayOn;
  replayToggle.textContent = replayOn ? "On" : "Off";
  if (replayOn) resetTo(video.currentTime);
  else replayMessages.innerHTML = "";
});

// ---- view count (once per visit, after playback starts) ----

async function countView() {
  if (viewCounted) return;
  viewCounted = true;
  try { await fetch(`/api/${TYPE}s/${ID}/view`, { method: "POST" }); } catch { /* ignore */ }
}

// ---- load ----

async function requireAuth() {
  let data;
  try { data = await (await fetch("/api/me")).json(); } catch { data = { authed: false }; }
  if (!data.authed) { window.location.href = "/"; return false; }
  return true;
}

async function loadMedia() {
  let meta;
  try {
    const reply = await fetch(`/api/${TYPE}s/${ID}`);
    if (!reply.ok) throw new Error("not found");
    meta = await reply.json();
  } catch {
    titleEl.textContent = "Not found";
    subEl.textContent = "This recording is no longer available.";
    return;
  }
  titleEl.textContent = TYPE === "vod" ? meta.title : meta.name;
  const views = meta.views === 1 ? "1 view" : `${meta.views} views`;
  const when = relDate(TYPE === "vod" ? meta.started_at : meta.created_at);
  let sub = `${views} · ${when}`;
  if (TYPE === "clip" && meta.creator) sub += ` · clipped by @${meta.creator}`;
  subEl.textContent = sub;
  if (TYPE === "vod" && meta.description) descEl.textContent = meta.description;
  video.poster = meta.poster ? `/media/${TYPE}s/${ID}.jpg` : "";
  video.src = `/media/${TYPE}s/${meta.filename}`;

  // Chat replay
  try {
    replay = (await (await fetch(`/api/${TYPE}s/${ID}/chat`)).json()).messages || [];
  } catch { replay = []; }
  if (!replay.length) {
    replayEmpty.hidden = false;
    replayToggle.hidden = true;
  }
}

// The channel accent (the brand color) is server-driven. The head bootstrap
// paints the last-seen value from localStorage; this syncs it with the server on
// load and remembers it for the next no-flash paint.
function applyAccent(value) {
  if (!["green", "amber", "blue", "ghost"].includes(value)) return;
  if (document.documentElement.dataset.accent !== value) {
    document.documentElement.dataset.accent = value;
    try { localStorage.setItem("selfstream_accent", value); } catch (e) {}
  }
}

async function boot() {
  if (!(await requireAuth())) return;
  try { applyAccent((await (await fetch("/api/status")).json()).accent); } catch (e) {}
  if (!ID) { titleEl.textContent = "Not found"; return; }
  await loadMedia();
}

boot();
