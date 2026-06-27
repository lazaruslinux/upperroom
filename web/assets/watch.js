// Watch page. Confirms the viewer is signed in, plays the low latency stream,
// and runs the chat and watching list over a WebSocket.

const STREAM_URL = "/live/index.m3u8";

const video = document.getElementById("video");
const offline = document.getElementById("offline");
const messages = document.getElementById("messages");
const viewerCount = document.getElementById("viewer-count");
const viewerList = document.getElementById("viewer-list");
const chatForm = document.getElementById("chat-form");
const chatInput = document.getElementById("chat-input");
const unmuteButton = document.getElementById("unmute");

let me = null;
let hls = null;
let socket = null;
const MESSAGE_TTL_MS = 60000;  // chat messages disappear after a minute

async function requireAuth() {
  const reply = await fetch("/api/me");
  const data = await reply.json();
  if (!data.authed) {
    window.location.href = "/";
    return false;
  }
  me = data;
  return true;
}

// ---- video ----

function showOffline(isOffline) {
  offline.hidden = !isOffline;
  video.style.visibility = isOffline ? "hidden" : "visible";
}

function startVideo() {
  if (window.Hls && Hls.isSupported()) {
    hls = new Hls({ lowLatencyMode: true, backBufferLength: 30 });
    hls.loadSource(STREAM_URL);
    hls.attachMedia(video);
    // Counts consecutive fatal errors with no clean playback in between. A
    // healthy frame resets it, so this only trips when the stream is really
    // down, not on a momentary blip.
    let recoverAttempts = 0;
    hls.on(Hls.Events.MANIFEST_PARSED, () => {
      showOffline(false);
      video.play().catch(() => {});
    });
    hls.on(Hls.Events.FRAG_BUFFERED, () => {
      recoverAttempts = 0;
      showOffline(false);
    });
    hls.on(Hls.Events.ERROR, (event, data) => {
      if (!data.fatal) return;
      // A brief source hiccup (a muxer restart, a dropped segment) should not
      // blank straight to the offline card. Try to resume a few times first.
      if (recoverAttempts < 3) {
        recoverAttempts++;
        if (data.type === Hls.ErrorTypes.MEDIA_ERROR) {
          hls.recoverMediaError();
        } else {
          hls.startLoad();
        }
        return;
      }
      // Recovery did not take, so the stream is probably actually down. Show
      // the offline card and let the status poll bring it back when it returns.
      hls.destroy();
      hls = null;
      showOffline(true);
      setTimeout(checkStream, 5000);
    });
  } else if (video.canPlayType("application/vnd.apple.mpegurl")) {
    // Safari on iOS plays HLS natively without hls.js.
    video.src = STREAM_URL;
    video.addEventListener("loadedmetadata", () => showOffline(false));
    video.addEventListener("error", () => {
      showOffline(true);
      setTimeout(checkStream, 5000);
    });
  }
}

async function checkStream() {
  const reply = await fetch("/api/status");
  const data = await reply.json();
  if (data.online && !hls) {
    startVideo();
  } else if (!data.online) {
    showOffline(true);
    setTimeout(checkStream, 5000);
  }
}

// ---- chat and presence ----

function avatarColor(seed) {
  let hash = 0;
  for (let i = 0; i < seed.length; i++) hash = (hash * 31 + seed.charCodeAt(i)) % 360;
  return `hsl(${hash}, 55%, 45%)`;
}

function makeClickable(node, username) {
  node.classList.add("avatar-clickable");
  node.addEventListener("click", () => openProfile(username));
  return node;
}

function initialsNode(username, name, big) {
  const span = document.createElement("span");
  span.className = big ? "avatar avatar-lg" : "avatar";
  span.textContent = ((name || username || "?").trim().charAt(0) || "?").toUpperCase();
  span.style.background = avatarColor(username || "?");
  return span;
}

function avatarNode(username, name, version, big, clickable) {
  let node;
  if (!version) {
    node = initialsNode(username, name, big);
  } else {
    const img = document.createElement("img");
    img.className = big ? "avatar avatar-lg" : "avatar";
    img.alt = "";
    img.src = `/api/avatar/${encodeURIComponent(username)}?v=${version}`;
    // If the image cannot load, fall back to the initials bubble.
    img.addEventListener("error", () => {
      const fallback = initialsNode(username, name, big);
      if (clickable) makeClickable(fallback, username);
      img.replaceWith(fallback);
    });
    node = img;
  }
  if (clickable) makeClickable(node, username);
  return node;
}

function scheduleExpiry(node, ts) {
  // Remove a line a minute after it was posted, so chat stays ephemeral.
  if (!ts) return;
  const remaining = ts * 1000 + MESSAGE_TTL_MS - Date.now();
  if (remaining <= 0) { node.remove(); return; }
  setTimeout(() => node.remove(), remaining);
}

function atBottom() {
  return messages.scrollHeight - messages.scrollTop - messages.clientHeight < 40;
}

function addLine(node) {
  const stick = atBottom();
  messages.appendChild(node);
  if (stick) messages.scrollTop = messages.scrollHeight;
}

function renderChat(msg) {
  const line = document.createElement("div");
  line.className = "msg";
  line.appendChild(avatarNode(msg.user, msg.name, msg.avatar || 0, false, true));
  const bodyWrap = document.createElement("span");
  bodyWrap.className = "msg-body";
  const name = document.createElement("span");
  name.className = msg.admin ? "name admin" : "name";
  name.textContent = msg.name;
  const body = document.createElement("span");
  body.className = "body";
  body.textContent = msg.text;        // textContent keeps any HTML inert
  // Each person's own font rides along on their messages for everyone to see.
  body.style.fontFamily = FONTS[msg.font] || "";
  bodyWrap.append(name, document.createTextNode(" "), body);
  line.appendChild(bodyWrap);
  addLine(line);
  scheduleExpiry(line, msg.ts);
}

function renderSystem(msg) {
  const line = document.createElement("div");
  line.className = "msg system";
  line.textContent = msg.text;
  addLine(line);
  scheduleExpiry(line, msg.ts);
}

function renderPresence(msg) {
  viewerCount.textContent = msg.count === 1 ? "1 watching" : `${msg.count} watching`;
  viewerList.innerHTML = "";
  msg.viewers.forEach((viewer) => {
    const item = document.createElement("li");
    item.appendChild(avatarNode(viewer.username, viewer.name, viewer.avatar || 0, false, true));
    const youSuffix = me && viewer.username === me.username ? " (you)" : "";
    const label = document.createElement("span");
    label.textContent = viewer.name + youSuffix;
    item.appendChild(label);
    viewerList.appendChild(item);
  });
}

function connectChat() {
  const scheme = location.protocol === "https:" ? "wss" : "ws";
  socket = new WebSocket(`${scheme}://${location.host}/ws`);

  socket.addEventListener("message", (event) => {
    const msg = JSON.parse(event.data);
    if (msg.type === "chat") renderChat(msg);
    else if (msg.type === "system") renderSystem(msg);
    else if (msg.type === "presence") renderPresence(msg);
    else if (msg.type === "wipe") messages.innerHTML = "";
    else if (msg.type === "hello") {
      me = me || msg.you;
      msg.history.forEach(renderChat);
    }
  });

  // If the connection drops, wait a moment and reconnect.
  socket.addEventListener("close", () => setTimeout(connectChat, 3000));

  chatForm.onsubmit = (event) => {
    event.preventDefault();
    const text = chatInput.value.trim();
    if (!text || !socket || socket.readyState !== WebSocket.OPEN) return;
    socket.send(JSON.stringify({ type: "chat", text }));
    chatInput.value = "";
  };
}

document.getElementById("viewer-toggle").addEventListener("click", () => {
  viewerList.hidden = !viewerList.hidden;
});

// ---- settings: theme, chat font, avatar, bio ----

const THEME_KEY = "selfstream_theme";
const FONTS = {
  system: "",
  mono: "'Roboto Mono', monospace",
  comic: "'Comic Neue', cursive",
  retro: "'VT323', monospace",
};

const settingsPanel = document.getElementById("settings-panel");
const themeToggle = document.getElementById("theme-toggle");
const fontSelect = document.getElementById("font-select");
const bioInput = document.getElementById("bio-input");
const bioSave = document.getElementById("bio-save");
const avatarButton = document.getElementById("avatar-button");
const avatarInput = document.getElementById("avatar-input");
const myAvatar = document.getElementById("my-avatar");

document.getElementById("settings-toggle").addEventListener("click", () => {
  settingsPanel.hidden = !settingsPanel.hidden;
});

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

// Your chosen font rides along on your own messages for everyone to see, so it
// is saved on the server, not just in this browser.
fontSelect.addEventListener("change", async () => {
  me.font = fontSelect.value;
  await saveProfile({ font: me.font });
});

bioSave.addEventListener("click", async () => {
  me.bio = bioInput.value;
  const ok = await saveProfile({ bio: me.bio });
  bioSave.textContent = ok ? "Saved" : "Error";
  setTimeout(() => { bioSave.textContent = "Save"; }, 1500);
});

function renderMyAvatar() {
  if (!me) return;
  myAvatar.innerHTML = "";
  myAvatar.appendChild(avatarNode(me.username, me.name, me.avatar || 0, true, false));
}

// Reflect the signed in account's saved profile in the settings panel.
function loadMyProfile() {
  if (!me) return;
  fontSelect.value = me.font || "system";
  bioInput.value = me.bio || "";
  renderMyAvatar();
}

// ---- profile popup (tap any avatar) ----

const profileModal = document.getElementById("profile-modal");
const profileAvatar = document.getElementById("profile-avatar");
const profileName = document.getElementById("profile-name");
const profileBio = document.getElementById("profile-bio");

async function openProfile(username) {
  try {
    const data = await (await fetch(`/api/profile/${encodeURIComponent(username)}`)).json();
    profileAvatar.innerHTML = "";
    profileAvatar.appendChild(avatarNode(data.username, data.name, data.avatar || 0, true, false));
    profileName.textContent = data.name + (data.admin ? " (admin)" : "");
    profileBio.textContent = data.bio || "No bio yet.";
    openModal(profileModal);
  } catch {
    /* a failed lookup just does nothing */
  }
}

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
  // Keep the image covering the square so there is never a blank edge.
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
  if (e.key === "Escape") {
    document.querySelectorAll(".modal:not([hidden])").forEach(closeModal);
  }
});

// Browsers only allow autoplay when the video starts muted. The stream still
// carries your audio, so once it is playing we offer a button to turn sound on.
video.addEventListener("playing", () => {
  unmuteButton.hidden = !video.muted;
});
video.addEventListener("volumechange", () => {
  if (!video.muted) unmuteButton.hidden = true;
});
unmuteButton.addEventListener("click", () => {
  video.muted = false;
  video.play().catch(() => {});
  unmuteButton.hidden = true;
});

async function boot() {
  if (!(await requireAuth())) return;
  loadMyProfile();
  connectChat();
  checkStream();
}

boot();
