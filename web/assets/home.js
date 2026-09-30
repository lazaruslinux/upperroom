// Home: the off-air place.
//
// Nobody on air: when the last broadcast was, a way to hear of the next one,
// the last broadcast itself, and the latest clips. Somebody on air: a current
// frame on the monitor and one loud way into the room. Members who sign in
// while the stream is live go straight to the room (gate.js), so this is
// mostly what people see between broadcasts.
//
// It no longer plays the stream itself. A muted preview here cost a full
// viewer's bandwidth and a place in the room for a picture nobody was
// listening to; a still frame says the same thing for the price of a JPEG.

let me = null;
let channel = null;
let online = false;
let thumbTimer = null;

const monitor = document.getElementById("monitor");
const offline = document.getElementById("offline");
const lastAir = document.getElementById("last-air");
const liveBlock = document.getElementById("live-block");
const offair = document.getElementById("offair");
const slateTitle = document.getElementById("slate-title");
const slateSub = document.getElementById("slate-sub");

async function requireAuth() {
  let data;
  try {
    data = await (await fetch("/api/me")).json();
  } catch {
    data = { authed: false };
  }
  if (!data.authed) {
    window.location.href = "/";
    return false;
  }
  me = data;
  return true;
}

// ---- words for when ----

function dayWord(epoch) {
  const then = new Date(epoch * 1000);
  const now = new Date();
  const midnight = new Date(now.getFullYear(), now.getMonth(), now.getDate()).getTime();
  if (then.getTime() >= midnight) return "today";
  const diffDays = Math.floor((midnight - then.getTime()) / 86400000) + 1;
  if (diffDays === 1) return "yesterday";
  if (diffDays < 7) return then.toLocaleDateString(undefined, { weekday: "long" });
  return then.toLocaleDateString(undefined, { month: "long", day: "numeric" });
}

function lastBroadcastTitle(epoch) {
  if (Date.now() / 1000 - epoch > 6 * 86400) {
    const when = new Date(epoch * 1000).toLocaleDateString(undefined, { month: "long", day: "numeric" });
    return `Watch the ${when} broadcast`;
  }
  return `Watch ${dayWord(epoch)}’s broadcast`;
}

function lengthWords(seconds) {
  const m = Math.round(seconds / 60);
  if (m < 60) return `${m} min`;
  const h = Math.floor(m / 60);
  return m % 60 ? `${h} h ${m % 60} min` : `${h} h`;
}

function durationClock(secs) {
  secs = Math.max(0, Math.round(secs || 0));
  const h = Math.floor(secs / 3600);
  const m = Math.floor((secs % 3600) / 60);
  const s = secs % 60;
  const pad = (n) => String(n).padStart(2, "0");
  return h > 0 ? `${h}:${pad(m)}:${pad(s)}` : `${m}:${pad(s)}`;
}

function relDate(epoch) {
  if (!epoch) return "";
  const secs = Math.floor(Date.now() / 1000) - epoch;
  if (secs < 3600) return `${Math.max(1, Math.floor(secs / 60))} min ago`;
  if (secs < 86400) return `${Math.floor(secs / 3600)} h ago`;
  return dayWord(epoch);
}

// ---- the monitor: a still frame while live, the off-air card while not ----

function refreshThumb() {
  if (!online || document.hidden) return;
  const next = new Image();
  next.alt = "The stream right now";
  next.className = "still";
  next.addEventListener("load", () => {
    const old = monitor.querySelector("img.still");
    if (old) old.replaceWith(next);
    else monitor.prepend(next);
  });
  // No frame yet (the stream just came up) keeps whatever was there.
  next.src = `/api/thumbnail?t=${Date.now()}`;
}

function applyStatus(data) {
  const was = online;
  online = !!data.online;
  offline.hidden = online;
  liveBlock.hidden = !online;
  offair.hidden = online;
  const game = online ? data.game || "" : "";
  slateSub.textContent = game ? `Playing ${game}` : "";
  slateSub.hidden = !game;
  if (online && !was) {
    loadChannel();
    refreshThumb();
    thumbTimer = setInterval(refreshThumb, 15000);
  } else if (!online && was) {
    clearInterval(thumbTimer);
    thumbTimer = null;
    const still = monitor.querySelector("img.still");
    if (still) still.remove();
    loadChannel();
    loadLibrary();
  }
}

document.addEventListener("visibilitychange", () => {
  if (!document.hidden) refreshThumb();
});

// ---- the channel, and when it was last on ----

let lastVod = null;

async function loadChannel() {
  try {
    channel = await (await fetch("/api/channel")).json();
  } catch {
    return;
  }
  if (channel.title) slateTitle.textContent = channel.title;
  renderLastAir();
}

function renderLastAir() {
  const ended = channel && channel.last_air_ended_at;
  if (!ended) return;
  let line = `Last on air ${dayWord(ended)}`;
  if (lastVod && lastVod.duration && Math.abs(lastVod.started_at + lastVod.duration - ended) < 3600) {
    line += `, for ${lengthWords(lastVod.duration)}`;
  }
  lastAir.textContent = `${line}.`;
}

// ---- the go-live push ----

const pushRow = document.getElementById("push-row");
const pushChip = document.getElementById("push-chip");
const pushMsg = document.getElementById("push-msg");

// One quiet chip, offered only where a tap can actually sign this device up.
// Never a pop-up: the browser asks for permission only after the tap.
function showPush(chip, line) {
  pushChip.hidden = !chip;
  pushMsg.textContent = line || "";
  pushMsg.hidden = !line;
  pushRow.hidden = !chip && !line;
}

async function renderPush() {
  showPush((await pushNotify.state()) === "off");
}

pushChip.addEventListener("click", () => {
  pushChip.disabled = true;
  pushNotify.turnOn().then((state) => {
    if (state === "on") showPush(false, "This device will be notified when it goes live.");
    else if (state === "blocked") showPush(false, pushNotify.explain("blocked"));
    else showPush(state === "off");
  }).catch(() => {
    showPush(true, "Could not turn it on here. Try again from Options.");
  }).finally(() => { pushChip.disabled = false; });
});

// ---- the last broadcast and the latest clips ----

function clipCard(clip) {
  const a = document.createElement("a");
  a.className = "media-card";
  a.href = `/media?type=clip&id=${clip.id}`;
  const thumb = document.createElement("div");
  thumb.className = "media-thumb";
  if (clip.poster) {
    const img = document.createElement("img");
    img.src = `/media/clips/${clip.id}.jpg`;
    img.alt = "";
    img.loading = "lazy";
    img.addEventListener("error", () => img.remove());
    thumb.appendChild(img);
  }
  if (clip.duration) {
    const dur = document.createElement("span");
    dur.className = "media-dur";
    dur.textContent = durationClock(clip.duration);
    thumb.appendChild(dur);
  }
  const title = document.createElement("div");
  title.className = "media-title";
  title.textContent = clip.name;
  const sub = document.createElement("div");
  sub.className = "media-sub";
  sub.textContent = [relDate(clip.created_at), clip.creator ? `by @${clip.creator}` : ""]
    .filter(Boolean).join(" · ");
  a.append(thumb, title, sub);
  return a;
}

async function loadLibrary() {
  let vods = [];
  let clips = [];
  try {
    const [v, c] = await Promise.all([
      fetch("/api/vods").then((r) => (r.ok ? r.json() : { vods: [] })),
      fetch("/api/clips").then((r) => (r.ok ? r.json() : { clips: [] })),
    ]);
    vods = v.vods || [];
    clips = c.clips || [];
  } catch {
    /* a library that cannot be read shows nothing rather than an error */
  }
  lastVod = vods[0] || null;
  renderLastAir();
  const link = document.getElementById("last-vod");
  if (lastVod) {
    link.href = `/media?type=vod&id=${lastVod.id}`;
    document.getElementById("last-vod-title").textContent = lastBroadcastTitle(lastVod.started_at);
    document.getElementById("last-vod-meta").textContent =
      [lastVod.title, durationClock(lastVod.duration)].filter(Boolean).join(" · ");
    const thumb = document.getElementById("last-vod-thumb");
    thumb.textContent = "";
    if (lastVod.poster) {
      const img = document.createElement("img");
      img.alt = "";
      img.src = `/media/vods/${lastVod.id}.jpg`;
      img.addEventListener("error", () => img.remove());
      thumb.appendChild(img);
    }
  }
  link.hidden = !lastVod;
  const grid = document.getElementById("clip-grid");
  grid.textContent = "";
  clips.slice(0, 6).forEach((clip) => grid.appendChild(clipCard(clip)));
  document.getElementById("clips-section").hidden = clips.length === 0;
}

async function boot() {
  if (!(await requireAuth())) return;
  // Home is where members land while nobody is on air, so the one-time
  // notices can show here.
  mountNav(me, { current: "home", landing: true, onStatus: applyStatus });
  renderPush();
  loadChannel();
  loadLibrary();
}

boot();

// Register the service worker. It caches nothing; it shows the go-live push,
// and it is why Chrome will offer to install the site to a phone's home screen.
if ("serviceWorker" in navigator) {
  window.addEventListener("load", () => {
    navigator.serviceWorker.register("/sw.js").catch(() => {});
  });
}
