// Playback page for a saved VOD or a clip, with chat replayed in sync. Driven by
// the query string, e.g. /media?type=vod&id=12 or /media?type=clip&id=5.
let me = null;               // this browser's identity, for the shared nav


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
const heatmap = document.getElementById("heatmap");

let replay = [];        // chat lines, sorted by offset_s
let shownIdx = 0;       // how many have been revealed
let lastT = 0;
let replayOn = true;
let viewCounted = false;
let hmPlayhead = null;   // the moving marker, once the strip is built
let hmDuration = 0;      // media length the strip was bucketed against

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

// The host (admin) shows a bright-red video-camera icon; a moderator shows a
// small blue "mod" tag. Matches the live chat marks.
const CAMERA_SVG =
  '<svg viewBox="0 0 24 24" width="13" height="13" fill="currentColor" aria-hidden="true">' +
  '<path d="M3 7.5A1.5 1.5 0 0 1 4.5 6h9A1.5 1.5 0 0 1 15 7.5v9A1.5 1.5 0 0 1 13.5 18h-9' +
  'A1.5 1.5 0 0 1 3 16.5v-9Zm14 3 3.25-2.17a.6.6 0 0 1 .95.5v6.34a.6.6 0 0 1-.95.5L17 13.5v-3Z"/></svg>';

function roleBadgeNode(admin, mod) {
  if (!admin && !mod) return null;
  const span = document.createElement("span");
  if (admin) {
    span.className = "role-tag host";
    span.title = "Broadcaster";
    span.innerHTML = CAMERA_SVG;   // a static, trusted icon; no user data
  } else {
    span.className = "role-tag mod";
    span.title = "Moderator";
    span.textContent = "mod";
  }
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
  updateHeatmapPlayhead();
  if (!replayOn) return;
  const t = video.currentTime;
  if (t + 0.5 < lastT) resetTo(t);   // jumped backward
  else revealUpTo(t);
  lastT = t;
  countView();
});
video.addEventListener("seeking", () => {
  updateHeatmapPlayhead();
  if (replayOn) resetTo(video.currentTime);
});

replayToggle.addEventListener("click", () => {
  replayOn = !replayOn;
  replayToggle.textContent = replayOn ? "On" : "Off";
  if (replayOn) resetTo(video.currentTime);
  else replayMessages.innerHTML = "";
});

// ---- chat-activity heatmap ----

// Bucket the recorded chat by timestamp and draw a bar per bucket, so the
// busy moments of the stream stand out. Bail quietly if there is nothing to
// show; the strip stays hidden.
function buildHeatmap(duration) {
  if (!duration) return;
  const msgs = replay.filter((m) => !m.deleted);
  if (!msgs.length) return;

  const bucketSeconds = Math.max(2, Math.ceil(duration / 100));
  const n = Math.ceil(duration / bucketSeconds);
  const counts = new Array(n).fill(0);
  for (const m of msgs) {
    let i = Math.floor(m.offset_s / bucketSeconds);
    if (i >= n) i = n - 1;   // clamp anything at or past the end into the last bucket
    counts[i]++;
  }
  const max = Math.max(...counts);

  for (let i = 0; i < n; i++) {
    const bar = document.createElement("div");
    bar.className = "hm-bar";
    bar.style.height = counts[i] ? `${18 + (82 * counts[i]) / max}%` : "2px";
    const label = counts[i] === 1 ? "1 message" : `${counts[i]} messages`;
    bar.title = `${clock(i * bucketSeconds)} · ${label}`;
    heatmap.appendChild(bar);
  }

  hmPlayhead = document.createElement("div");
  hmPlayhead.className = "hm-playhead";
  heatmap.appendChild(hmPlayhead);

  hmDuration = duration;
  heatmap.hidden = false;
  updateHeatmapPlayhead();
}

// Slide the marker to match playback. No-op until the strip is built.
function updateHeatmapPlayhead() {
  if (!hmPlayhead || !hmDuration) return;
  const pct = Math.min(1, Math.max(0, video.currentTime / hmDuration)) * 100;
  hmPlayhead.style.left = `${pct}%`;
}

heatmap.addEventListener("click", (event) => {
  if (!hmDuration) return;
  const rect = heatmap.getBoundingClientRect();
  const t = ((event.clientX - rect.left) / rect.width) * hmDuration;
  video.currentTime = Math.min(hmDuration, Math.max(0, t));
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
  // A guest pass buys the stream and chat, nothing else on the site.
  // Send them where their pass actually works rather than rendering a
  // page whose every request will 401.
  if (data.guest) { window.location.href = "/watch"; return false; }
  me = data;
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

  buildHeatmap(meta.duration);
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

// The operator's site name leads the top bar and names the browser tab, so the
// platform brand ("powered by upperroom") stays a credit rather than the title.
async function boot() {
  if (!(await requireAuth())) return;
  // This page already asks for status, so hand the site name to the nav rather
  // than making it fetch the same thing again.
  let status = {};
  try { status = await (await fetch("/api/status")).json(); } catch (e) {}
  applyAccent(status.accent);
  mountNav(me, { current: "media", siteName: status.site_name });
  if (!ID) { titleEl.textContent = "Not found"; return; }
  await loadMedia();
}

boot();
