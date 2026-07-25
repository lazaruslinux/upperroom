// Home page. The card-style landing shown right after sign in. It confirms the
// viewer is logged in, shows whether the stream is live with a real preview
// thumbnail, and sends them into the player when they tap the card.
//
// The header and the personal settings behind it belong to nav.js, which every
// signed-in page shares.

const greeting = document.getElementById("greeting");
const card = document.getElementById("stream-card");
const cardChannel = document.getElementById("card-channel");
const cardTitle = document.getElementById("card-title");
const cardDesc = document.getElementById("card-desc");
const thumb = document.getElementById("thumb");
const thumbFallback = document.getElementById("thumb-fallback");
const streamBadge = document.getElementById("stream-badge");
const badgeLabel = document.getElementById("badge-label");
const statusPill = document.getElementById("status-pill");

let me = null;
let channel = null;          // the streamer shown on the card
let online = false;

function avatarColor(seed) {
  let hash = 0;
  for (let i = 0; i < seed.length; i++) hash = (hash * 31 + seed.charCodeAt(i)) % 360;
  return `hsl(${hash}, 55%, 45%)`;
}

function avatarNode(username, name, version, cls) {
  if (version) {
    const img = document.createElement("img");
    img.className = cls;
    img.alt = "";
    img.src = `/api/avatar/${encodeURIComponent(username)}?v=${version}`;
    return img;
  }
  const span = document.createElement("span");
  span.className = cls;
  span.textContent = (name || username || "?").trim().charAt(0).toUpperCase();
  span.style.background = avatarColor(username || "?");
  return span;
}

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

function renderGreeting() {
  const name = (me.name || me.username || "there").split(" ")[0];
  // "signed in as name", with the name in the accent color.
  greeting.textContent = "signed in as ";
  const who = document.createElement("b");
  who.textContent = name;
  greeting.appendChild(who);
}

// Changing your display name or avatar in the shared settings modal has to be
// reflected here too: the greeting names you, and the card names the streamer,
// who may well be you.
function onProfileChange(field, value) {
  if (field === "name") {
    renderGreeting();
    if (channel && channel.username === me.username) {
      channel.name = value;
      renderChannel();
    }
  }
  if (field === "avatar" && channel && channel.username === me.username) {
    channel.avatar = value;
    renderChannel();
  }
}

// The card represents the streamer (the channel owner), not the viewer, so it
// shows their name, @username, and avatar.
function renderChannel() {
  if (!channel) return;
  // The operator's site name leads the top bar (above "powered by upperroom")
  // and names the browser tab. Distinct from the stream title on the card below.
  if (channel.site_name) {
    const siteTitle = document.getElementById("site-title");
    if (siteTitle) siteTitle.textContent = channel.site_name;
    document.title = channel.site_name;
  }
  const fresh = avatarNode(channel.username, channel.name, channel.avatar || 0, "card-avatar");
  document.querySelector(".card-avatar").replaceWith(fresh);
  if (channel.title) cardTitle.textContent = channel.title;
  cardDesc.textContent = channel.description || "Tap to join stream and start chatting";
  cardChannel.innerHTML = "";
  const nm = document.createElement("span");
  nm.className = "channel-name";
  nm.textContent = channel.name;
  cardChannel.appendChild(nm);
  if (channel.username) {
    const handle = document.createElement("span");
    handle.className = "channel-handle";
    handle.textContent = "@" + channel.username;
    cardChannel.appendChild(handle);
  }
}

async function loadChannel() {
  try {
    channel = await (await fetch("/api/channel")).json();
  } catch {
    channel = { username: null, name: "upperroom", avatar: 0 };
  }
  renderChannel();
  applySchedule(channel);
}

// ---- the next scheduled stream ----
// Signed in, so this is the one place the operator's note is shown; the login
// page gets the time alone from the public status poll.

const nextBox = document.getElementById("next-stream");
const nextCount = document.getElementById("next-count");
const nextWhen = document.getElementById("next-when");
const nextNote = document.getElementById("next-note");
let nextAt = null;
let nextNoteText = "";
let nextTick = null;

function formatCountdown(seconds) {
  if (seconds <= 0) return "starting soon";
  const days = Math.floor(seconds / 86400);
  const hours = Math.floor((seconds % 86400) / 3600);
  const minutes = Math.floor((seconds % 3600) / 60);
  if (days > 0) return `in ${days}d ${hours}h`;
  if (hours > 0) return `in ${hours}h ${minutes}m`;
  return `in ${Math.max(1, minutes)}m`;
}

function renderNext() {
  // Hidden while the stream is actually on: the card above says everything.
  if (!nextAt || online) {
    nextBox.hidden = true;
    if (nextTick) { clearInterval(nextTick); nextTick = null; }
    return;
  }
  const away = nextAt - Math.floor(Date.now() / 1000);
  nextCount.textContent = `Next stream ${formatCountdown(away)}`;
  // The server stores UTC; every viewer reads it in their own time.
  nextWhen.textContent = new Date(nextAt * 1000).toLocaleString([], {
    weekday: "short", month: "short", day: "numeric",
    hour: "numeric", minute: "2-digit",
  });
  nextNote.textContent = nextNoteText;
  nextNote.hidden = !nextNoteText;
  nextBox.hidden = false;
  if (!nextTick) nextTick = setInterval(renderNext, 30000);
}

function applySchedule(data) {
  const when = data.next_stream_at || 0;
  // Stop showing it once it is well past, matching what the server publishes.
  nextAt = when && Date.now() / 1000 < when + 7200 ? when : null;
  nextNoteText = nextAt ? (data.next_stream_note || "") : "";
  renderNext();
}

// ---- live status + thumbnail ----

function showLive(isLive, watching) {
  online = isLive;
  card.classList.toggle("is-live", isLive);

  // One badge that toggles state: red blinking LIVE when live, muted Offline
  // otherwise.
  streamBadge.classList.toggle("is-live", isLive);
  streamBadge.classList.toggle("is-offline", !isLive);
  badgeLabel.textContent = isLive ? "LIVE" : "Offline";

  // A separate count pill: "N watching" while live, "N in chat" while offline
  // (people can hang out in chat between streams). Hidden when offline and
  // nobody is around, so an empty offline card stays clean.
  const count = typeof watching === "number" ? watching : 0;
  if (isLive) {
    statusPill.hidden = false;
    statusPill.textContent = count === 1 ? "1 watching" : `${count} watching`;
  } else if (count > 0) {
    statusPill.hidden = false;
    statusPill.textContent = count === 1 ? "1 in chat" : `${count} in chat`;
  } else {
    statusPill.hidden = true;
  }

  if (!isLive) {
    thumb.hidden = true;
    thumbFallback.hidden = false;
  }
}

function refreshThumb() {
  if (!online) return;
  // Cache-bust so each refresh pulls the freshest captured frame.
  const next = new Image();
  next.onload = () => {
    thumb.src = next.src;
    thumb.hidden = false;
    thumbFallback.hidden = true;
  };
  next.onerror = () => {
    // No frame yet (stream just came up); keep showing the branded fallback.
    thumb.hidden = true;
    thumbFallback.hidden = false;
  };
  next.src = `/api/thumbnail?t=${Date.now()}`;
}

async function refreshStatus() {
  let data = { online: false };
  try {
    data = await (await fetch("/api/status")).json();
  } catch {
    /* treat a failed poll as offline */
  }
  applyAccent(data.accent);
  const wasOnline = online;
  showLive(!!data.online, data.watching);
  if (data.online && !wasOnline) refreshThumb();
  // Going live retires the schedule server-side; hide it here at the same time.
  if (data.online !== wasOnline) renderNext();
}

card.addEventListener("click", () => {
  window.location.href = "/watch";
});

// ---- accent flavor (channel-wide brand color) ----
// Unlike the theme, the accent is the channel's brand and is server-driven. The
// head bootstrap paints the last-seen value from localStorage; the status poll
// syncs it with the server and remembers it for the next no-flash paint.
function applyAccent(value) {
  if (!["green", "amber", "blue", "ghost"].includes(value)) return;
  if (document.documentElement.dataset.accent !== value) {
    document.documentElement.dataset.accent = value;
    try { localStorage.setItem("selfstream_accent", value); } catch (e) {}
  }
}

// ---- library (past VODs + clips) ----

const libGrid = document.getElementById("lib-grid");
const libEmpty = document.getElementById("lib-empty");
const clipFilter = document.getElementById("clip-filter");
const mineOnlyToggle = document.getElementById("mine-only");
let libTab = "vods";
let mineOnly = false;
const libCache = { vods: null, clips: null };

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
  if (secs < 3600) return `${Math.max(1, Math.floor(secs / 60))}m ago`;
  if (secs < 86400) return `${Math.floor(secs / 3600)}h ago`;
  if (secs < 2592000) return `${Math.floor(secs / 86400)}d ago`;
  return new Date(epoch * 1000).toLocaleDateString();
}

function mediaCard(item, kind) {
  const a = document.createElement("a");
  a.className = "media-card";
  a.href = `/media?type=${kind}&id=${item.id}`;

  const thumb = document.createElement("div");
  thumb.className = "media-thumb";
  if (item.poster) {
    const img = document.createElement("img");
    img.src = `/media/${kind}s/${item.id}.jpg`;
    img.alt = "";
    img.loading = "lazy";
    thumb.appendChild(img);
  } else {
    thumb.classList.add("media-thumb-fallback");
    const mark = document.createElement("span");
    mark.className = "thumb-mark";
    mark.textContent = "no signal";
    thumb.appendChild(mark);
  }
  if (item.duration) {
    const dur = document.createElement("span");
    dur.className = "media-dur";
    dur.textContent = durationClock(item.duration);
    thumb.appendChild(dur);
  }
  a.appendChild(thumb);

  const meta = document.createElement("div");
  meta.className = "media-meta";
  const title = document.createElement("div");
  title.className = "media-title";
  title.textContent = kind === "vod" ? item.title : item.name;
  const sub = document.createElement("div");
  sub.className = "media-sub muted";
  const views = item.views === 1 ? "1 view" : `${item.views} views`;
  const when = relDate(kind === "vod" ? item.started_at : item.created_at);
  let line = `${views} · ${when}`;
  if (kind === "clip" && item.creator) line += ` · @${item.creator}`;
  sub.textContent = line;
  meta.append(title, sub);
  a.appendChild(meta);
  return a;
}

async function renderLibrary() {
  const kind = libTab === "vods" ? "vod" : "clip";
  let items = libCache[libTab];
  if (items === null) {
    try { items = (await (await fetch(`/api/${libTab}`)).json())[libTab] || []; }
    catch { items = []; }
    libCache[libTab] = items;
  }
  // The "my clips only" filter applies to the clips tab for every role.
  clipFilter.hidden = libTab !== "clips";
  let display = items;
  if (libTab === "clips" && mineOnly && me) {
    display = items.filter((c) => c.creator === me.username);
  }

  libGrid.innerHTML = "";
  if (!display.length) {
    libEmpty.hidden = false;
    if (libTab === "vods") {
      libEmpty.textContent = "No past broadcasts yet. Recordings appear here after a stream ends.";
    } else if (mineOnly) {
      libEmpty.textContent = "You haven't made any clips yet.";
    } else {
      libEmpty.textContent = "No clips yet. Viewers can clip the last 30 seconds while live.";
    }
    return;
  }
  libEmpty.hidden = true;
  display.forEach((item) => libGrid.appendChild(mediaCard(item, kind)));
}

document.querySelectorAll(".lib-tab").forEach((tab) => {
  tab.addEventListener("click", () => {
    libTab = tab.dataset.tab;
    document.querySelectorAll(".lib-tab").forEach((t) => t.classList.toggle("selected", t === tab));
    renderLibrary();
  });
});

mineOnlyToggle.addEventListener("change", () => {
  mineOnly = mineOnlyToggle.checked;
  renderLibrary();
});

async function boot() {
  if (!(await requireAuth())) return;
  mountNav(me, { current: "home", onProfileChange, promptEmail: true });
  renderGreeting();
  loadChannel();
  renderLibrary();
  await refreshStatus();
  refreshThumb();
  setInterval(refreshStatus, 10000);
  setInterval(refreshThumb, 15000);
}

boot();

// Register the pass-through service worker. It caches nothing; it exists so
// Chrome will offer to install the site to a phone's home screen.
if ("serviceWorker" in navigator) {
  window.addEventListener("load", () => {
    navigator.serviceWorker.register("/sw.js").catch(() => {});
  });
}
