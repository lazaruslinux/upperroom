// Home page. The card-style landing shown right after sign in. It confirms the
// viewer is logged in, shows whether the stream is live with a real preview
// thumbnail, and sends them into the player when they tap the card.

const greeting = document.getElementById("greeting");
const adminLink = document.getElementById("admin-link");
const logoutButton = document.getElementById("logout");
const card = document.getElementById("stream-card");
const cardAvatar = document.getElementById("card-avatar");
const thumb = document.getElementById("thumb");
const thumbFallback = document.getElementById("thumb-fallback");
const liveBadge = document.getElementById("live-badge");
const watchPill = document.getElementById("watch-pill");
const offlinePill = document.getElementById("offline-pill");

let me = null;
let online = false;

function avatarColor(seed) {
  let hash = 0;
  for (let i = 0; i < seed.length; i++) hash = (hash * 31 + seed.charCodeAt(i)) % 360;
  return `hsl(${hash}, 55%, 45%)`;
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

function setupIdentity() {
  const name = (me.name || me.username || "there").split(" ")[0];
  greeting.textContent = `Welcome back, ${name}.`;
  if (me.admin) adminLink.hidden = false;
  // The channel badge on the card uses the signed in person's own avatar if
  // they have one, otherwise a coloured initial.
  if (me.avatar) {
    const img = document.createElement("img");
    img.className = "card-avatar";
    img.alt = "";
    img.src = `/api/avatar/${encodeURIComponent(me.username)}?v=${me.avatar}`;
    cardAvatar.replaceWith(img);
  } else {
    cardAvatar.textContent = (me.name || me.username || "?").trim().charAt(0).toUpperCase();
    cardAvatar.style.background = avatarColor(me.username || "?");
  }
}

// ---- live status + thumbnail ----

function showLive(isLive, watching) {
  online = isLive;
  liveBadge.hidden = !isLive;
  offlinePill.hidden = isLive;
  card.classList.toggle("is-live", isLive);
  if (isLive && typeof watching === "number") {
    watchPill.hidden = false;
    watchPill.textContent = watching === 1 ? "1 watching" : `${watching} watching`;
  } else {
    watchPill.hidden = true;
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
    // No frame yet (stream just came up) — keep showing the branded fallback.
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
  const wasOnline = online;
  showLive(!!data.online, data.watching);
  if (data.online && !wasOnline) refreshThumb();
}

card.addEventListener("click", () => {
  window.location.href = "/watch";
});

logoutButton.addEventListener("click", async () => {
  try { await fetch("/api/logout", { method: "POST" }); } catch {}
  window.location.href = "/";
});

async function boot() {
  if (!(await requireAuth())) return;
  setupIdentity();
  await refreshStatus();
  refreshThumb();
  setInterval(refreshStatus, 10000);
  setInterval(refreshThumb, 15000);
}

boot();
