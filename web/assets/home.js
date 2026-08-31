// Home page. The card-style landing shown right after sign in. It confirms the
// viewer is logged in, plays the live stream muted inside the card, and sends
// them into the player, where the sound is, when they tap it.
//
// The header belongs to nav.js, which every signed-in page shares. The archive
// of past broadcasts and clips is its own page now, /browse.

const STREAM_URL = "/live/index.m3u8";

const greeting = document.getElementById("greeting");
const card = document.getElementById("stream-card");
const cardChannel = document.getElementById("card-channel");
const cardTitle = document.getElementById("card-title");
const cardPlaying = document.getElementById("card-playing");
const cardDesc = document.getElementById("card-desc");
const thumb = document.getElementById("thumb");
const thumbFallback = document.getElementById("thumb-fallback");
const cardVideo = document.getElementById("card-video");
const streamBadge = document.getElementById("stream-badge");
const badgeLabel = document.getElementById("badge-label");
const statusPill = document.getElementById("status-pill");
const offlineBlock = document.getElementById("offline-block");

let me = null;
let channel = null;          // the streamer shown on the card
let online = false;
let hls = null;
let previewOn = false;       // a player is built (it may not have picture yet)

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
  // A guest pass buys the stream and chat, nothing else on the site. Send them
  // where their pass actually works rather than rendering a page whose every
  // request will 401.
  if (data.guest) {
    window.location.href = "/watch";
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

// The card represents the streamer (the channel owner), not the viewer, so it
// shows their name, @username, and avatar.
function renderChannel() {
  if (!channel) return;
  // The operator's site name leads the top bar and names the browser tab.
  // Distinct from the stream title on the card below.
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
}

// ---- live status + thumbnail ----

function showLive(isLive, watching) {
  online = isLive;
  card.classList.toggle("is-live", isLive);

  // While live the stream card is shown; while offline it is hidden entirely and
  // a compact "broadcaster is offline" block takes its place, pointing at chat
  // and the browse page. The status poll runs on an interval, so this flips
  // on its own the moment a stream starts or ends, without a reload.
  card.hidden = !isLive;
  offlineBlock.hidden = isLive;

  // One badge that toggles state: red LIVE with a blinking dot when live, muted
  // Offline otherwise.
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
  // The preview covers the still frame, so there is no point paying for one,
  // and neither costs anything worth paying in a tab nobody is looking at.
  if (!online || previewOn || document.hidden) return;
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

// ---- live preview ----
// The card plays the real stream, muted, the way a front page does. It is the
// same HLS the watch page plays, so it costs the same bandwidth and takes a
// place in the room: a full room refuses it with a 403, and that is correct.
// Every failure path here is silent and ends at the still frame the card showed
// before, and every one of them tears the player all the way down.

function showPreview() {
  if (!previewOn) return;
  cardVideo.hidden = false;
  thumb.hidden = true;
  thumbFallback.hidden = true;
  card.classList.add("is-previewing");
}

function stopPreview() {
  previewOn = false;
  if (hls) { hls.destroy(); hls = null; }
  cardVideo.pause();
  // Dropping the source is what actually stops the download; pausing alone
  // leaves the player filling its buffer.
  cardVideo.removeAttribute("src");
  cardVideo.load();
  cardVideo.hidden = true;
  card.classList.remove("is-previewing");
  if (online) {
    refreshThumb();
  } else {
    thumb.hidden = true;
    thumbFallback.hidden = false;
  }
}

function startPreview() {
  // A hidden tab must never start one, and a fatal is not retried on the poll:
  // the next try comes when the tab is looked at again, or on a reload.
  if (previewOn || !online || document.hidden) return;
  if (window.Hls && Hls.isSupported()) {
    previewOn = true;
    hls = new Hls({ lowLatencyMode: true, backBufferLength: 10 });
    hls.loadSource(STREAM_URL);
    hls.attachMedia(cardVideo);
    hls.on(Hls.Events.MANIFEST_PARSED, () => {
      // A refused autoplay is a fallback, not an error: show the still frame.
      cardVideo.play().catch(() => stopPreview());
    });
    hls.on(Hls.Events.ERROR, (event, data) => {
      // Any fatal, a full room's 403 included, drops back to the thumbnail.
      if (data.fatal) stopPreview();
    });
  } else if (cardVideo.canPlayType("application/vnd.apple.mpegurl")) {
    // Safari plays HLS natively and needs no hls.js.
    previewOn = true;
    cardVideo.src = STREAM_URL;
    cardVideo.play().catch(() => stopPreview());
  }
}

// Picture has arrived: swap the still frame out. Bound once, so it serves both
// the hls.js and the native path.
cardVideo.addEventListener("playing", showPreview);
// A native-path failure (a 403 among them) arrives here. Guarded by previewOn
// so the empty-source error that teardown itself raises is not a second pass.
cardVideo.addEventListener("error", () => { if (previewOn) stopPreview(); });

// Bandwidth guards. A backgrounded tab keeps a muted video running otherwise,
// which is a full viewer's bandwidth and a room slot for a card nobody is
// looking at.
document.addEventListener("visibilitychange", () => {
  if (document.hidden) stopPreview();
  else if (online) startPreview();
});
window.addEventListener("pagehide", () => stopPreview());

async function refreshStatus() {
  let data = { online: false };
  try {
    data = await (await fetch("/api/status")).json();
  } catch {
    /* treat a failed poll as offline */
  }
  applyAccent(data.accent);
  // What the streamer is playing rides this poll, so the card follows a change
  // made mid-broadcast rather than waiting for a reload. Absent when offline.
  if (cardPlaying) {
    const game = data.game || "";
    cardPlaying.textContent = game ? `Playing: ${game}` : "";
    cardPlaying.hidden = !game;
  }
  const wasOnline = online;
  showLive(!!data.online, data.watching);
  if (data.online && !wasOnline) {
    refreshThumb();
    startPreview();
  } else if (!data.online && wasOnline) {
    // The stream ended: tear the player down and go back to the offline state.
    stopPreview();
  }
  // Going live retires the schedule server-side; hide it here at the same time.
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

// ---- what changed in this release ----
// Shown once, on the page people land on after signing in, and only for the
// release actually running. Acknowledging it is what marks it read, so closing
// the tab instead means it is still waiting next time rather than lost.

function showWhatsNew(info) {
  if (!info || !info.notes || !info.notes.length) return;
  const modal = document.getElementById("whats-new");
  if (!modal) return;
  document.getElementById("whats-new-title").textContent =
    `upperroom has been updated to v${info.version}`;
  const list = document.getElementById("whats-new-list");
  list.innerHTML = "";
  info.notes.forEach((note) => {
    const item = document.createElement("li");
    item.textContent = note;
    list.appendChild(item);
  });
  const ok = document.getElementById("whats-new-ok");
  ok.addEventListener("click", async () => {
    modal.hidden = true;
    // Best effort: a failed acknowledgement just means it is offered again,
    // which is the harmless direction to fail in.
    try { await fetch("/api/whats-new/seen", { method: "POST" }); } catch (e) {}
  });
  modal.hidden = false;
  ok.focus();
}

async function boot() {
  if (!(await requireAuth())) return;
  mountNav(me, { current: "home", promptEmail: true });
  showWhatsNew(me.whats_new);
  renderGreeting();
  loadChannel();
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
