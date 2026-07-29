// OBS chat overlay. A display-only page OBS adds as a browser source: it shows
// recent chat, join notices, and clip alerts over the broadcast video. It cannot
// sign in, so it authenticates with the bearer key in its own URL (?key=...) and
// connects to the chat socket read-only. It never sends chat and is never counted
// as a viewer.
//
// URL params tune it without a rebuild, and all default to today's behavior:
//   pos=bl|br|tl|tr   which corner the chat column anchors to (default bl)
//   scale=0.75..1.5   font-size multiplier (default 1)
//   show=chat,joins,clips,highlights,status,ticker   which parts to draw (default all)
//   max=1..20         how many chat lines stay on screen (default 8)
//   mute=1            silence the highlight chime
//   scene=starting|brb|ending   full-screen card instead of the chat column
//   at=HH:MM          for scene=starting, a countdown target (local, today)
//   title=...         the scene's title line
// Unknown values fall back to the default silently; garbage never breaks the page.

const overlay = document.getElementById("overlay");
const statusChip = document.getElementById("ov-status");
const tickerEl = document.getElementById("ov-ticker");
const tickerInner = tickerEl.querySelector(".ov-ticker-inner");
const sceneEl = document.getElementById("ov-scene");

// The host (admin) shows a red video-camera icon; a moderator shows a "mod" tag.
const CAMERA_SVG =
  '<svg viewBox="0 0 24 24" width="16" height="16" fill="currentColor" aria-hidden="true">' +
  '<path d="M3 7.5A1.5 1.5 0 0 1 4.5 6h9A1.5 1.5 0 0 1 15 7.5v9A1.5 1.5 0 0 1 13.5 18h-9' +
  'A1.5 1.5 0 0 1 3 16.5v-9Zm14 3 3.25-2.17a.6.6 0 0 1 .95.5v6.34a.6.6 0 0 1-.95.5L17 13.5v-3Z"/></svg>';

// The bearer key rides in the URL. Without it there is nothing to authenticate,
// so the page just sits blank rather than hammering the socket.
const params = new URLSearchParams(location.search);
const KEY = params.get("key") || "";

// ---- layout / config params ----
// Everything here is best effort and clamped: a bad value degrades to the default
// rather than breaking the overlay, which runs unattended for hours.

function clampNumber(raw, min, max, fallback) {
  const n = parseFloat(raw);
  if (!isFinite(n)) return fallback;
  return Math.min(max, Math.max(min, n));
}

const POS = ["bl", "br", "tl", "tr"].includes(params.get("pos"))
  ? params.get("pos")
  : "bl";
const SCALE = clampNumber(params.get("scale"), 0.75, 1.5, 1);
const MAX_CHAT = Math.round(clampNumber(params.get("max"), 1, 20, 8));
const MUTE = params.get("mute") === "1";

// show= is a CSV allow-list; absent means everything. Unknown names are ignored.
const SHOW_ALL = ["chat", "joins", "clips", "highlights", "status", "ticker"];
const SHOW = new Set(
  params.has("show")
    ? params.get("show").split(",").map((s) => s.trim()).filter((s) => SHOW_ALL.includes(s))
    : SHOW_ALL
);
function shows(part) {
  return SHOW.has(part);
}

// scene= turns the page into a full-screen card. null is the normal chat overlay.
const SCENE = ["starting", "brb", "ending"].includes(params.get("scene"))
  ? params.get("scene")
  : null;

// Apply the layout the moment we load, so nothing paints in the wrong place first.
overlay.dataset.pos = POS;
// When the chat column takes the top-right corner, the status chip yields to the
// top-left so chat lines never render through it.
if (POS === "tr" && shows("chat")) document.documentElement.dataset.chip = "tl";
document.documentElement.style.setProperty("--ov-scale", String(SCALE));

// How long each kind of item lingers. The overlay is ambient, not an archive, so
// everything clears itself after a while; chat also caps at a fixed count.
const CHAT_TTL = 45000;
const JOIN_TTL = 10000;
const CLIP_TTL = 12000;
const HIGHLIGHT_TTL = 15000;

// ---- accent flavor and live status ----
// Match the channel's brand color at load, the same data-accent hook the rest of
// the site uses, and read the live state / uptime / viewer count the status chip
// and scene countdown need. Best effort: if a poll fails the last values stand.

let statusOnline = false;
let sinceEpoch = null;   // when the current broadcast started, epoch seconds
let watching = 0;        // viewer count, updated live from presence frames
let siteName = "";       // the channel's site name, for the scene card

function applyAccent(value) {
  if (["green", "amber", "blue", "ghost"].includes(value)) {
    document.documentElement.dataset.accent = value;
  }
}

async function pollStatus() {
  try {
    const data = await (await fetch("/api/status")).json();
    applyAccent(data.accent);
    statusOnline = !!data.online;
    sinceEpoch = data.online ? (data.since || null) : null;
    if (typeof data.watching === "number") watching = data.watching;
    if (typeof data.site_name === "string") siteName = data.site_name;
  } catch (e) {
    /* leave the last known values in place */
  }
  paintStatus();
  paintScene();
}

// ---- status chip ----
// Built once; the text is refreshed in place every second (uptime) and whenever a
// presence frame changes the viewer count.

let chipLive, chipTime, chipCount;

function buildChip() {
  const dot = document.createElement("span");
  dot.className = "ov-status-dot";
  chipLive = document.createElement("span");
  chipLive.className = "ov-status-live";
  chipLive.textContent = "LIVE";
  chipTime = document.createElement("span");
  chipTime.className = "ov-status-time";
  chipCount = document.createElement("span");
  chipCount.className = "ov-status-count";
  statusChip.append(dot, chipLive, chipTime, chipCount);
}

function fmtClock(seconds) {
  const s = Math.max(0, Math.floor(seconds));
  const h = Math.floor(s / 3600);
  const m = Math.floor((s % 3600) / 60);
  const sec = s % 60;
  const two = (n) => String(n).padStart(2, "0");
  return h > 0 ? `${h}:${two(m)}:${two(sec)}` : `${m}:${two(sec)}`;
}

function paintStatus() {
  // Hidden entirely when offline, when the operator filtered it out, or in scene
  // mode (the scene card carries its own live state).
  if (!shows("status") || SCENE || !statusOnline || sinceEpoch == null) {
    statusChip.hidden = true;
    // Nothing is reserved while the chip is gone, so a top-anchored chat column
    // sits back at its plain 24px.
    document.documentElement.style.setProperty("--ov-chip-h", "0px");
    delete document.documentElement.dataset.chipvis;
    return;
  }
  chipTime.textContent = " " + fmtClock(Date.now() / 1000 - sinceEpoch);
  chipCount.textContent = ` · ${watching} watching`;
  statusChip.hidden = false;
  // Publish the band the chip occupies, measured now that it is visible, so the
  // CSS can move a top-anchored column below it instead of letting chat render
  // underneath. Re-measured on every tick, which also picks up the small height
  // change when the self-hosted fonts finish loading.
  document.documentElement.style.setProperty("--ov-chip-h", statusChip.offsetHeight + "px");
  document.documentElement.dataset.chipvis = "1";
}

// ---- ticker ----
// A single operator line along the bottom. Empty means nothing is shown. It
// scrolls only when the text is wider than the screen, and never under
// prefers-reduced-motion.

function setTicker(text) {
  const value = (text == null ? "" : String(text));
  if (!shows("ticker") || !value) {
    tickerEl.hidden = true;
    tickerEl.classList.remove("scrolling");
    // No band along the bottom any more, so bottom-anchored chat and the scene
    // card go back to their plain 24px and 6vh spacing.
    document.documentElement.style.setProperty("--ov-ticker-h", "0px");
    delete document.documentElement.dataset.ticker;
    return;
  }
  tickerInner.textContent = value;   // textContent keeps any markup inert
  tickerEl.hidden = false;
  document.documentElement.dataset.ticker = "1";
  // Measure after layout so both numbers are real: the band height the rest of
  // the overlay keeps clear of, and the text width that decides scrolling.
  requestAnimationFrame(() => {
    document.documentElement.style.setProperty("--ov-ticker-h", tickerEl.offsetHeight + "px");
    decideTickerScroll();
  });
}

function decideTickerScroll() {
  tickerEl.classList.remove("scrolling");
  const reduce = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  if (reduce) return;   // a fitting-or-not clipped line, never a moving one
  if (tickerInner.scrollWidth > tickerEl.clientWidth) {
    // Pace it by distance so a long message is not a blur and a short one is not
    // a crawl: roughly 90px a second across the full in-and-out travel.
    const travel = tickerInner.scrollWidth + tickerEl.clientWidth;
    tickerInner.style.setProperty("--ov-ticker-dur", (travel / 90) + "s");
    tickerEl.classList.add("scrolling");
  }
}

// ---- scene mode ----
// A full-screen card for Starting Soon / BRB / Ending. The chat column is hidden
// while a scene is up; the ticker still shows (it is the operator's own line).

const SCENE_DEFAULT_TITLE = {
  starting: "Starting soon",
  brb: "Be right back",
  ending: "Thanks for watching",
};

let sceneSiteEl, sceneTitleEl, sceneCountEl;
let sceneTargetEpoch = null;   // countdown target for scene=starting, or null

function parseSceneTarget() {
  // at=HH:MM is a local time today. Absent, malformed, or already past means no
  // countdown, and the title shows alone.
  const raw = params.get("at") || "";
  const match = /^(\d{1,2}):(\d{2})$/.exec(raw.trim());
  if (!match) return null;
  const h = parseInt(match[1], 10);
  const m = parseInt(match[2], 10);
  if (h > 23 || m > 59) return null;
  const target = new Date();
  target.setHours(h, m, 0, 0);
  const epoch = Math.floor(target.getTime() / 1000);
  return epoch > Date.now() / 1000 ? epoch : null;
}

function buildScene() {
  overlay.hidden = true;   // no chat column while a scene is up
  statusChip.hidden = true;

  sceneSiteEl = document.createElement("div");
  sceneSiteEl.className = "ov-scene-site";

  const rule = document.createElement("div");
  rule.className = "ov-scene-rule";

  sceneTitleEl = document.createElement("div");
  sceneTitleEl.className = "ov-scene-title";
  // title= is operator-controlled but treated as untrusted: textContent only, and
  // capped so a runaway value cannot blow the card apart.
  const title = (params.get("title") || "").slice(0, 80).trim();
  sceneTitleEl.textContent = title || SCENE_DEFAULT_TITLE[SCENE];

  sceneEl.append(sceneSiteEl, rule, sceneTitleEl);

  if (SCENE === "starting") {
    sceneCountEl = document.createElement("div");
    sceneCountEl.className = "ov-scene-count";
    sceneEl.append(sceneCountEl);
    sceneTargetEpoch = parseSceneTarget();
  }
  sceneEl.hidden = false;
}

function paintScene() {
  if (!SCENE) return;
  sceneSiteEl.textContent = siteName || "";
  if (SCENE !== "starting") return;
  // Once the stream is actually up, the countdown is stale: swap it for a quiet
  // LIVE note so the operator notices the scene is still on air.
  if (statusOnline) {
    sceneCountEl.className = "ov-scene-count live";
    sceneCountEl.textContent = "Live now";
    return;
  }
  sceneCountEl.className = "ov-scene-count";
  if (sceneTargetEpoch == null) {
    sceneCountEl.textContent = "";
    return;
  }
  const remaining = sceneTargetEpoch - Date.now() / 1000;
  if (remaining <= 0) {
    // The clock reached the start time but we are not live yet; drop the counter
    // rather than show a negative one, leaving the title alone.
    sceneTargetEpoch = null;
    sceneCountEl.textContent = "";
    return;
  }
  sceneCountEl.textContent = "Starting in " + fmtClock(remaining);
}

// ---- rendering ----

function autoRemove(node, ttl) {
  setTimeout(() => node.remove(), ttl);
}

// A bottom-anchored column grows upward, so on a short browser source it reaches
// the status chip's band and the top lines would render beneath the chip. CSS can
// push a top-anchored column down but not a bottom-anchored one, so here the
// oldest items are dropped instead, the same removal the max-count and TTL paths
// use. Nothing is ever clipped; the column just holds fewer lines.
function evictForChipBand() {
  if (statusChip.hidden) return;                     // no band to keep clear of
  if (POS !== "bl" && POS !== "br") return;          // CSS handles the top corners
  const chipBottom = statusChip.getBoundingClientRect().bottom;
  // Stop at one child so the item that was just appended always survives.
  while (
    overlay.children.length > 1 &&
    overlay.getBoundingClientRect().top < chipBottom + 8
  ) {
    overlay.firstElementChild.remove();
  }
}

function append(node, ttl) {
  overlay.appendChild(node);
  autoRemove(node, ttl);
  evictForChipBand();
}

function renderChat(msg) {
  if (!shows("chat")) return;
  const item = document.createElement("div");
  item.className = "ov-item ov-chat";
  if (msg.id != null) item.dataset.msgid = msg.id;

  // Role mark (red camera for the host, "mod" tag for a moderator), like the site.
  if (msg.admin || msg.mod) {
    const tag = document.createElement("span");
    if (msg.admin) {
      tag.className = "ov-tag host";
      tag.innerHTML = CAMERA_SVG;   // a static, trusted icon; no user data
    } else {
      tag.className = "ov-tag mod";
      tag.textContent = "mod";
    }
    item.appendChild(tag);
  }

  const name = document.createElement("span");
  name.className = "ov-name" + (msg.admin ? " op" : msg.mod ? " mod" : "");
  name.textContent = msg.name;
  item.appendChild(name);

  const body = document.createElement("span");
  body.className = "ov-body";
  body.textContent = msg.text;   // textContent keeps any markup inert
  item.appendChild(body);

  append(item, CHAT_TTL);

  // Keep only the most recent chat lines on screen; drop the oldest.
  const chats = overlay.querySelectorAll(".ov-chat");
  for (let i = 0; i < chats.length - MAX_CHAT; i++) chats[i].remove();
}

function renderJoin(text) {
  if (!shows("joins")) return;
  const item = document.createElement("div");
  item.className = "ov-item ov-join";
  item.textContent = text;
  append(item, JOIN_TTL);
}

function renderClip(msg) {
  if (!shows("clips")) return;
  const item = document.createElement("div");
  item.className = "ov-item ov-clip";
  const who = document.createElement("span");
  who.className = "ov-clip-who";
  who.textContent = (msg.by || "someone") + " clipped:";
  const name = document.createElement("span");
  name.className = "ov-clip-name";
  name.textContent = msg.name || "";
  item.append(who, name);
  append(item, CLIP_TTL);
}

function renderHighlight(msg) {
  if (!shows("highlights")) return;
  const item = document.createElement("div");
  item.className = "ov-item ov-highlight";

  // Role mark, like a chat line: a red camera for the host, a "mod" tag for a
  // moderator, so a highlight shows who sent it the way chat does.
  if (msg.admin || msg.mod) {
    const tag = document.createElement("span");
    if (msg.admin) {
      tag.className = "ov-tag host";
      tag.innerHTML = CAMERA_SVG;   // a static, trusted icon; no user data
    } else {
      tag.className = "ov-tag mod";
      tag.textContent = "mod";
    }
    item.appendChild(tag);
  }

  const who = document.createElement("span");
  who.className = "ov-highlight-who";
  who.textContent = (msg.name || "someone") + " highlighted:";
  const body = document.createElement("span");
  body.className = "ov-highlight-body";
  body.textContent = msg.message || "";
  item.append(who, body);
  append(item, HIGHLIGHT_TTL);
  // Keep only the most recent few highlights on screen, like the chat cap.
  const items = overlay.querySelectorAll(".ov-highlight");
  for (let i = 0; i < items.length - MAX_CHAT; i++) items[i].remove();
  playChime();
}

// A soft two-tone chime when a highlight lands, synthesized with the Web Audio
// API so the overlay ships no audio assets. Guarded end to end: a browser source
// with no audio device (or a browser that blocks autoplay) must never throw here
// and break the overlay, so a failure just leaves the highlight silent.
let audioCtx = null;

function playChime() {
  if (MUTE) return;   // the operator asked for a quiet overlay
  try {
    const Ctx = window.AudioContext || window.webkitAudioContext;
    if (!Ctx) return;
    if (!audioCtx) audioCtx = new Ctx();
    const now = audioCtx.currentTime;
    // Two short sine notes, a rising third, at a low gain so it sits under the
    // broadcast rather than over it.
    [[523.25, 0], [659.25, 0.16]].forEach(([freq, offset]) => {
      const osc = audioCtx.createOscillator();
      const gain = audioCtx.createGain();
      osc.type = "sine";
      osc.frequency.value = freq;
      const start = now + offset;
      gain.gain.setValueAtTime(0.0001, start);
      gain.gain.exponentialRampToValueAtTime(0.2, start + 0.02);
      gain.gain.exponentialRampToValueAtTime(0.0001, start + 0.3);
      osc.connect(gain).connect(audioCtx.destination);
      osc.start(start);
      osc.stop(start + 0.32);
    });
  } catch (e) {
    /* no audio device or autoplay blocked: the overlay still works silently */
  }
}

function applyDelete(id) {
  if (id == null) return;
  const line = overlay.querySelector(`[data-msgid="${id}"]`);
  if (line) line.remove();
}

// ---- socket with unattended reconnect ----
// A browser source runs for hours with no one watching it, so a dropped socket
// must always come back on its own. Retry every 5 seconds, forever.

function connect() {
  const scheme = location.protocol === "https:" ? "wss" : "ws";
  const socket = new WebSocket(
    `${scheme}://${location.host}/ws?overlay=${encodeURIComponent(KEY)}`
  );

  socket.addEventListener("message", (event) => {
    let msg;
    try { msg = JSON.parse(event.data); } catch (e) { return; }
    // The ticker and the viewer count matter in every mode, including scene mode.
    if (msg.type === "ticker") {
      setTicker(msg.text);
      return;
    }
    if (msg.type === "presence") {
      if (typeof msg.count === "number") {
        watching = msg.count;
        paintStatus();
      }
      return;
    }
    // A scene card shows no chat column, so the rest is only for the normal overlay.
    if (SCENE) return;
    if (msg.type === "chat") {
      if (!msg.deleted) renderChat(msg);
    } else if (msg.type === "clip") {
      renderClip(msg);
    } else if (msg.type === "highlight") {
      renderHighlight(msg);
    } else if (msg.type === "system") {
      // Join lines are the only system notice the overlay surfaces; "left" and
      // command replies stay off-screen to keep it quiet.
      if (typeof msg.text === "string" && msg.text.endsWith(" joined")) {
        renderJoin(msg.text);
      }
    } else if (msg.type === "delete") {
      applyDelete(msg.id);
    } else if (msg.type === "wipe") {
      overlay.innerHTML = "";
    }
    // Any other type (hello, ...) is ignored.
  });

  socket.addEventListener("close", () => setTimeout(connect, 5000));
  // An error will be followed by a close, which schedules the retry.
  socket.addEventListener("error", () => { try { socket.close(); } catch (e) {} });
}

// ---- boot ----

buildChip();
if (SCENE) buildScene();

// Poll the public status for accent, live state, uptime and viewer count. Scene
// mode leans on this to notice the stream go live, so it polls more often.
pollStatus();
setInterval(pollStatus, SCENE ? 15000 : 60000);

// Tick the uptime and the countdown locally every second between polls.
setInterval(() => { paintStatus(); paintScene(); }, 1000);

if (KEY) connect();
