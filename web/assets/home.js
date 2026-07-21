// Home page. The card-style landing shown right after sign in. It confirms the
// viewer is logged in, shows whether the stream is live with a real preview
// thumbnail, sends them into the player when they tap the card, and holds the
// per-account settings (avatar, bio, password) in its own menu.

const greeting = document.getElementById("greeting");
const adminLink = document.getElementById("admin-link");
const modLink = document.getElementById("mod-link");
const logoutButton = document.getElementById("logout");
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
  // Prompt-style line: "> signed in as name", with the name in accent green.
  greeting.textContent = "> signed in as ";
  const who = document.createElement("b");
  who.textContent = name;
  greeting.appendChild(who);
}

function setupIdentity() {
  renderGreeting();
  // Admin outranks moderator and its dashboard is a superset, so an admin only
  // needs the Admin button; a plain moderator gets the Mod button.
  if (me.admin) adminLink.hidden = false;
  else if (me.mod) modLink.hidden = false;
}

// The card represents the streamer (the channel owner), not the viewer, so it
// shows their name, @username, and avatar.
function renderChannel() {
  if (!channel) return;
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

// ---- your settings (avatar, bio, password) ----

const userModal = document.getElementById("user-modal");
const myAvatar = document.getElementById("my-avatar");
const nameInput = document.getElementById("name-input");
const nameSave = document.getElementById("name-save");
const bioInput = document.getElementById("bio-input");
const bioSave = document.getElementById("bio-save");
const avatarButton = document.getElementById("avatar-button");
const avatarInput = document.getElementById("avatar-input");
const pwCurrent = document.getElementById("pw-current");
const pwNew = document.getElementById("pw-new");
const pwSave = document.getElementById("pw-save");
const pwMsg = document.getElementById("pw-msg");

function renderMyAvatar() {
  myAvatar.innerHTML = "";
  myAvatar.appendChild(avatarNode(me.username, me.name, me.avatar || 0, "avatar avatar-lg"));
}

const emailInput = document.getElementById("email-input");
const emailSave = document.getElementById("email-save");
const passwordModal = document.getElementById("password-modal");

document.getElementById("settings-btn").addEventListener("click", () => {
  nameInput.value = me.name || "";
  bioInput.value = me.bio || "";
  renderMyAvatar();
  renderNotifySetting();
  openModal(userModal);
});

// Password lives in its own modal, opened from the settings panel.
document.getElementById("pw-open").addEventListener("click", () => {
  pwCurrent.value = "";
  pwNew.value = "";
  pwMsg.textContent = "";
  pwMsg.className = "pw-msg";
  openModal(passwordModal);
  pwCurrent.focus();
});

// Add, change, or clear your own go-live email.
emailSave.addEventListener("click", async () => {
  const email = emailInput.value.trim();
  if (email && !email.includes("@")) {
    emailSave.textContent = "Invalid";
    setTimeout(() => { emailSave.textContent = "Save"; }, 1500);
    return;
  }
  const ok = await saveProfile({ email });
  if (ok) me.email = email;
  emailSave.textContent = ok ? "Saved" : "Error";
  setTimeout(() => { emailSave.textContent = "Save"; }, 1500);
});

nameSave.addEventListener("click", async () => {
  const next = nameInput.value.trim();
  if (!next) { nameSave.textContent = "Empty"; setTimeout(() => { nameSave.textContent = "Save"; }, 1500); return; }
  const ok = await saveProfile({ display_name: next });
  if (ok) {
    me.name = next;
    renderGreeting();
    // If the viewer is the streamer, the card name updates too.
    if (channel && channel.username === me.username) {
      channel.name = next;
      renderChannel();
    }
  }
  nameSave.textContent = ok ? "Saved" : "Error";
  setTimeout(() => { nameSave.textContent = "Save"; }, 1500);
});

// ---- theme (light / dark) ----

const THEME_KEY = "selfstream_theme";
const themeToggle = document.getElementById("theme-toggle");
function applyTheme(theme) {
  document.documentElement.dataset.theme = theme;
  localStorage.setItem(THEME_KEY, theme);
  themeToggle.textContent = theme === "light" ? "Light" : "Dark";
}
applyTheme(localStorage.getItem(THEME_KEY) || "dark");
themeToggle.addEventListener("click", () => {
  applyTheme(document.documentElement.dataset.theme === "light" ? "dark" : "light");
});

async function saveProfile(patch) {
  try {
    const reply = await fetch("/api/profile", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(patch),
    });
    return reply.ok;
  } catch {
    return false;
  }
}

bioSave.addEventListener("click", async () => {
  me.bio = bioInput.value;
  const ok = await saveProfile({ bio: me.bio });
  bioSave.textContent = ok ? "Saved" : "Error";
  setTimeout(() => { bioSave.textContent = "Save"; }, 1500);
});

// ---- go-live email opt-in ----

const notifyToggle = document.getElementById("notify-toggle");

function renderNotifySetting() {
  notifyToggle.checked = me.notify_live !== false;
  emailInput.value = me.email || "";
}

notifyToggle.addEventListener("change", async () => {
  const on = notifyToggle.checked;
  const ok = await saveProfile({ notify_live: on });
  if (ok) me.notify_live = on;
  else notifyToggle.checked = !on;   // revert if the save failed
});

// ---- first-login email prompt ----
// If a viewer has no email on file, nudge them once to add one so they can get
// the go-live alert. "Not now" is remembered so it does not nag every login.

const EMAIL_PROMPT_KEY = "selfstream_email_prompt_dismissed";
const emailModal = document.getElementById("email-modal");
const emailPromptInput = document.getElementById("email-prompt-input");
const emailPromptMsg = document.getElementById("email-prompt-msg");

function maybePromptEmail() {
  if (me.email) return;                                   // already has one
  try { if (localStorage.getItem(EMAIL_PROMPT_KEY)) return; } catch {}
  openModal(emailModal);
  emailPromptInput.focus();
}

document.getElementById("email-ignore").addEventListener("click", () => {
  try { localStorage.setItem(EMAIL_PROMPT_KEY, "1"); } catch {}
});

document.getElementById("email-prompt-save").addEventListener("click", async () => {
  const email = emailPromptInput.value.trim();
  if (!email || !email.includes("@")) {
    emailPromptMsg.textContent = "Enter a valid email, or choose Not now.";
    return;
  }
  const ok = await saveProfile({ email });
  if (ok) {
    me.email = email;
    try { localStorage.removeItem(EMAIL_PROMPT_KEY); } catch {}
    closeModal(emailModal);
    renderNotifySetting();        // keep the Settings panel in sync
  } else {
    emailPromptMsg.textContent = "Could not save. Try again.";
  }
});

function showPwMsg(text, ok) {
  pwMsg.textContent = text;
  pwMsg.className = "pw-msg " + (ok ? "ok" : "bad");
}

pwSave.addEventListener("click", async () => {
  const current = pwCurrent.value;
  const next = pwNew.value;
  if (next.length < 8) { showPwMsg("Use at least 8 characters.", false); return; }
  pwSave.disabled = true;
  try {
    const reply = await fetch("/api/password", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ current_password: current, new_password: next }),
    });
    if (reply.ok) {
      pwCurrent.value = "";
      pwNew.value = "";
      showPwMsg("Password changed.", true);
      setTimeout(() => closeModal(passwordModal), 1200);
    } else {
      const data = await reply.json().catch(() => ({}));
      showPwMsg(data.error || "Could not change password.", false);
    }
  } catch {
    showPwMsg("Could not change password.", false);
  } finally {
    pwSave.disabled = false;
  }
});

// ---- avatar crop (drag + zoom) ----

const cropModal = document.getElementById("crop-modal");
const cropCanvas = document.getElementById("crop-canvas");
const cropZoom = document.getElementById("crop-zoom");
const cropSave = document.getElementById("crop-save");
const cropCtx = cropCanvas.getContext("2d");
const CROP = 256;
let cropImg = null;
let cropScaleBase = 1;
let cropX = 0;
let cropY = 0;

function drawCrop() {
  if (!cropImg) return;
  const scale = cropScaleBase * parseFloat(cropZoom.value);
  const w = cropImg.width * scale;
  const h = cropImg.height * scale;
  cropX = Math.min(0, Math.max(CROP - w, cropX));
  cropY = Math.min(0, Math.max(CROP - h, cropY));
  cropCtx.clearRect(0, 0, CROP, CROP);
  cropCtx.drawImage(cropImg, cropX, cropY, w, h);
}

avatarButton.addEventListener("click", () => avatarInput.click());
avatarInput.addEventListener("change", () => {
  const file = avatarInput.files[0];
  if (!file) return;
  const url = URL.createObjectURL(file);
  cropImg = new Image();
  cropImg.onload = () => {
    URL.revokeObjectURL(url);
    cropScaleBase = Math.max(CROP / cropImg.width, CROP / cropImg.height);
    cropZoom.value = "1";
    cropX = (CROP - cropImg.width * cropScaleBase) / 2;
    cropY = (CROP - cropImg.height * cropScaleBase) / 2;
    drawCrop();
    openModal(cropModal);
  };
  cropImg.src = url;
  avatarInput.value = "";
});

cropZoom.addEventListener("input", drawCrop);

let cropDragging = false;
let cropLastX = 0;
let cropLastY = 0;
cropCanvas.addEventListener("pointerdown", (e) => {
  cropDragging = true;
  cropLastX = e.clientX;
  cropLastY = e.clientY;
  cropCanvas.setPointerCapture(e.pointerId);
});
cropCanvas.addEventListener("pointermove", (e) => {
  if (!cropDragging) return;
  const rect = cropCanvas.getBoundingClientRect();
  cropX += (e.clientX - cropLastX) * (CROP / rect.width);
  cropY += (e.clientY - cropLastY) * (CROP / rect.height);
  cropLastX = e.clientX;
  cropLastY = e.clientY;
  drawCrop();
});
cropCanvas.addEventListener("pointerup", () => { cropDragging = false; });

cropSave.addEventListener("click", () => {
  cropCanvas.toBlob(async (blob) => {
    if (!blob) return;
    const form = new FormData();
    form.append("image", blob, "avatar.png");
    const reply = await fetch("/api/avatar", { method: "POST", body: form });
    if (reply.ok) {
      const data = await reply.json();
      me.avatar = data.avatar;
      renderMyAvatar();
      // If the viewer is the streamer, refresh the card avatar to match.
      if (channel && channel.username === me.username) {
        channel.avatar = data.avatar;
        renderChannel();
      }
      closeModal(cropModal);
    } else {
      const data = await reply.json().catch(() => ({}));
      alert(data.error || "Could not update your avatar.");
    }
  }, "image/png");
});

// ---- modal helpers ----

function openModal(m) { m.hidden = false; }
function closeModal(m) { m.hidden = true; }
document.querySelectorAll(".modal").forEach((m) => {
  m.addEventListener("click", (e) => {
    if (e.target === m || e.target.hasAttribute("data-close")) closeModal(m);
  });
});
document.addEventListener("keydown", (e) => {
  if (e.key === "Escape") document.querySelectorAll(".modal:not([hidden])").forEach(closeModal);
});

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
  setupIdentity();
  maybePromptEmail();
  loadChannel();
  renderLibrary();
  await refreshStatus();
  refreshThumb();
  setInterval(refreshStatus, 10000);
  setInterval(refreshThumb, 15000);
}

boot();
