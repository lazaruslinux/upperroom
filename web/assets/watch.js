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
const clipBtn = document.getElementById("clip-btn");

let me = null;
let hls = null;
let socket = null;
let streamOnline = false;          // tracks live state so the count can reword
let lastViewerCount = 0;
const MAX_VISIBLE_MESSAGES = 50;  // keep the last 50 lines on screen, no more
// True once the initial history batch has rendered, so only genuinely live
// lines animate in - the backlog on connect/reconnect appears instantly.
let chatLive = false;

// The header count is "watching" while live, "in chat" while offline (people
// can still hang out in chat between streams).
function setViewerLabel() {
  const noun = streamOnline ? "watching" : "in chat";
  viewerCount.textContent = `${lastViewerCount} ${noun}`;
}

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
  streamOnline = !isOffline;
  // Clipping only makes sense while the stream is live (and being recorded).
  clipBtn.hidden = isOffline;
  setViewerLabel();
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

// The channel accent rides along on the status poll (the head bootstrap already
// painted the last-seen value from localStorage); keep it in sync with the
// server and remember it for the next no-flash paint.
function applyAccent(value) {
  if (!["green", "amber", "blue", "ghost"].includes(value)) return;
  if (document.documentElement.dataset.accent !== value) {
    document.documentElement.dataset.accent = value;
    try { localStorage.setItem("selfstream_accent", value); } catch (e) {}
  }
}

async function checkStream() {
  const reply = await fetch("/api/status");
  const data = await reply.json();
  applyAccent(data.accent);
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

// Role badges sit just to the right of the avatar, in place of any "(admin)"
// text: small square mono text tags, amber "op" for an admin, blue "mod" for a
// moderator. An admin keeps every moderator power, so an admin shows "op".
function roleBadgeNode(admin, mod, big) {
  if (!admin && !mod) return null;
  const span = document.createElement("span");
  span.className = "role-tag " + (admin ? "op" : "mod") + (big ? " role-tag-lg" : "");
  span.title = admin ? "Admin" : "Moderator";
  span.textContent = admin ? "op" : "mod";
  return span;
}

function formatTimestamp(ts) {
  // Compact local stamp shown in tiny print on each line, e.g. "6.26.26 4:32pm".
  const d = ts ? new Date(ts * 1000) : new Date();
  const yr = String(d.getFullYear()).slice(-2);
  let hour = d.getHours();
  const min = String(d.getMinutes()).padStart(2, "0");
  const ampm = hour >= 12 ? "pm" : "am";
  hour = hour % 12 || 12;
  return `${d.getMonth() + 1}.${d.getDate()}.${yr} ${hour}:${min}${ampm}`;
}

function atBottom() {
  return messages.scrollHeight - messages.scrollTop - messages.clientHeight < 40;
}

function addLine(node) {
  const stick = atBottom();
  if (chatLive) node.classList.add("enter");   // fade+rise only for live lines
  messages.appendChild(node);
  // Keep only the most recent lines so chat stays static but bounded.
  while (messages.children.length > MAX_VISIBLE_MESSAGES) {
    messages.removeChild(messages.firstChild);
  }
  if (stick) messages.scrollTop = messages.scrollHeight;
}

function renderChat(msg) {
  const line = document.createElement("div");
  line.className = "msg";
  if (msg.id != null) line.dataset.msgid = msg.id;
  line.appendChild(avatarNode(msg.user, msg.name, msg.avatar || 0, false, true));
  const badge = roleBadgeNode(msg.admin, msg.mod, false);
  if (badge) line.appendChild(badge);
  const bodyWrap = document.createElement("span");
  bodyWrap.className = "msg-body";
  const head = document.createElement("span");
  head.className = "msg-head";
  const name = document.createElement("span");
  name.className = msg.admin ? "name admin" : "name";
  name.textContent = msg.name;
  const time = document.createElement("span");
  time.className = "msg-time";
  time.textContent = formatTimestamp(msg.ts);
  head.append(name, time);
  const body = document.createElement("span");
  body.className = "body";
  if (msg.deleted) {
    markBodyDeleted(body);
  } else {
    body.textContent = msg.text;        // textContent keeps any HTML inert
    // Each person's own font rides along on their messages for everyone to see.
    body.style.fontFamily = FONTS[msg.font] || "";
  }
  bodyWrap.append(head, body);
  line.appendChild(bodyWrap);
  addLine(line);
}

// A line removed by a moderator stays in place but its text is replaced, so the
// conversation does not visibly reflow and everyone sees it was moderated.
function markBodyDeleted(body) {
  body.textContent = "deleted by a moderator";
  body.classList.add("deleted");
  body.style.fontFamily = "";
}

function applyDelete(id) {
  if (id == null) return;
  const line = messages.querySelector(`[data-msgid="${id}"]`);
  if (line) {
    const body = line.querySelector(".body");
    if (body) markBodyDeleted(body);
  }
}

function renderSystem(msg) {
  const line = document.createElement("div");
  line.className = "msg system";
  line.textContent = msg.text;
  addLine(line);
}

// A redemption reads as a system-style notice line, e.g. "Sam redeemed hydrate
// (50)". textContent only, so a crafted reward label can never inject markup.
function renderRedeem(msg) {
  const line = document.createElement("div");
  line.className = "msg system";
  line.textContent = `${msg.user} redeemed ${msg.label} (${msg.cost})`;
  addLine(line);
}

function renderPresence(msg) {
  lastViewerCount = msg.count;
  setViewerLabel();
  viewerList.innerHTML = "";
  msg.viewers.forEach((viewer) => {
    const item = document.createElement("li");
    item.appendChild(avatarNode(viewer.username, viewer.name, viewer.avatar || 0, false, true));
    const badge = roleBadgeNode(viewer.admin, viewer.mod, false);
    if (badge) item.appendChild(badge);
    const youSuffix = me && viewer.username === me.username ? " (you)" : "";
    const label = document.createElement("span");
    label.textContent = viewer.name + youSuffix;
    item.appendChild(label);
    viewerList.appendChild(item);
  });
}

function connectChat() {
  chatLive = false;   // the reconnect backlog should not animate either
  const scheme = location.protocol === "https:" ? "wss" : "ws";
  socket = new WebSocket(`${scheme}://${location.host}/ws`);

  socket.addEventListener("message", (event) => {
    const msg = JSON.parse(event.data);
    if (msg.type === "chat") renderChat(msg);
    else if (msg.type === "system") renderSystem(msg);
    else if (msg.type === "redeem") renderRedeem(msg);
    else if (msg.type === "presence") renderPresence(msg);
    else if (msg.type === "delete") applyDelete(msg.id);
    else if (msg.type === "wipe") messages.innerHTML = "";
    else if (msg.type === "hello") {
      me = me || msg.you;
      msg.history.forEach(renderChat);
      chatLive = true;   // everything after the backlog is live
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
  caveat: "'Caveat', cursive",
};
const FONT_LIST = [
  ["system", "Default"],
  ["mono", "Roboto Mono"],
  ["comic", "Comic Neue"],
  ["retro", "VT323"],
  ["caveat", "Caveat"],
];

const settingsPanel = document.getElementById("settings-panel");
const themeToggle = document.getElementById("theme-toggle");
const fontPicker = document.getElementById("font-picker");

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
// is saved on the server (not just this browser). Each option is rendered in
// its own typeface so you can preview it before picking.
function buildFontPicker() {
  fontPicker.innerHTML = "";
  FONT_LIST.forEach(([key, label]) => {
    const btn = document.createElement("button");
    btn.type = "button";
    btn.className = "font-option" + (me && me.font === key ? " selected" : "");
    btn.textContent = label;
    btn.style.fontFamily = FONTS[key] || "";
    btn.addEventListener("click", async () => {
      me.font = key;
      fontPicker.querySelectorAll(".font-option").forEach((b) => b.classList.remove("selected"));
      btn.classList.add("selected");
      await saveProfile({ font: key });
    });
    fontPicker.appendChild(btn);
  });
}

// Reflect the signed in account's saved chat font in the settings panel. Avatar,
// bio, and password now live in the home page's "Your settings" menu.
function loadMyProfile() {
  if (me) buildFontPicker();
}

// ---- profile popup (tap any avatar) ----

const profileModal = document.getElementById("profile-modal");
const profileAvatar = document.getElementById("profile-avatar");
const profileName = document.getElementById("profile-name");
const profileBio = document.getElementById("profile-bio");
const profileJoined = document.getElementById("profile-joined");
const profilePoints = document.getElementById("profile-points");

function formatJoined(ts) {
  // Date only, in the same compact M.D.YY style as chat timestamps.
  if (!ts) return "";
  const d = new Date(ts * 1000);
  const yr = String(d.getFullYear()).slice(-2);
  return `joined ${d.getMonth() + 1}.${d.getDate()}.${yr}`;
}

async function openProfile(username) {
  try {
    const data = await (await fetch(`/api/profile/${encodeURIComponent(username)}`)).json();
    profileAvatar.innerHTML = "";
    profileAvatar.appendChild(avatarNode(data.username, data.name, data.avatar || 0, true, false));
    const badge = roleBadgeNode(data.admin, data.mod, true);
    if (badge) profileAvatar.appendChild(badge);
    profileName.textContent = data.name;
    profileBio.textContent = data.bio || "No bio yet.";
    profileJoined.textContent = formatJoined(data.joined);
    profilePoints.textContent = data.points != null ? `pts ${data.points}` : "";
    openModal(profileModal);
  } catch {
    /* a failed lookup just does nothing */
  }
}

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

// Keep the page exactly as tall as the visible viewport so the video, the last
// message, and the send box stay on screen on mobile as the address bar or the
// keyboard slides in and out. When the keyboard opens the ONLY thing that should
// change is the messages list shrinking - the video stays put and the input
// stays pinned above the keyboard. The body is overflow:hidden, but iOS still
// scrolls the visual viewport and displaces the whole layout, so we re-measure
// --vvh and force the window back to the top on every viewport change.
function lockHeight() {
  const vv = window.visualViewport;
  const height = (vv && vv.height) || window.innerHeight;
  document.documentElement.style.setProperty("--vvh", height + "px");
  // Counteract any page displacement the keyboard caused. offsetTop is the
  // visual viewport's own shift; pageX/YOffset is the layout scroll. Zero both.
  if (window.pageYOffset !== 0 || window.pageXOffset !== 0) window.scrollTo(0, 0);
}
if (window.visualViewport) {
  // Some mobile browsers only fire "scroll" (not "resize") when the bottom
  // toolbar or the keyboard slides in or out, which changes the visible height,
  // so listen to both. Otherwise the send box can end up hidden behind them.
  window.visualViewport.addEventListener("resize", lockHeight);
  window.visualViewport.addEventListener("scroll", lockHeight);
}
window.addEventListener("resize", lockHeight);
window.addEventListener("orientationchange", lockHeight);
// Re-measure shortly after load too; the first value can be taken before the
// browser chrome has settled.
window.addEventListener("load", () => setTimeout(lockHeight, 200));
lockHeight();

// Focusing the chat input opens the keyboard. Animate the layout height change
// (so the video resizes smoothly, not in a jump) and, once the viewport has
// settled, re-measure and pin the newest message to the bottom so it stays in
// view above the keyboard. Blur reverses it.
function settleAfterKeyboard() {
  // 300ms covers the keyboard slide-in on both iOS and Android; re-measure a
  // couple of times because the viewport height arrives in stages.
  [120, 300].forEach((t) => setTimeout(() => {
    lockHeight();
    messages.scrollTop = messages.scrollHeight;
  }, t));
}
chatInput.addEventListener("focus", () => {
  document.body.classList.add("kb-anim");
  settleAfterKeyboard();
});
chatInput.addEventListener("blur", () => {
  settleAfterKeyboard();
  setTimeout(() => document.body.classList.remove("kb-anim"), 320);
});

// ---- clip the last 30 seconds ----

const clipModal = document.getElementById("clip-modal");
const clipName = document.getElementById("clip-name");
const clipSave = document.getElementById("clip-save");
const clipMsg = document.getElementById("clip-msg");

function showClipMsg(text, ok, link) {
  clipMsg.className = "pw-msg " + (ok ? "ok" : "bad");
  clipMsg.textContent = "";
  clipMsg.append(document.createTextNode(text));
  if (link) {
    clipMsg.append(document.createTextNode(" "));
    const a = document.createElement("a");
    a.href = link;
    a.textContent = "View clip";
    clipMsg.appendChild(a);
  }
}

clipBtn.addEventListener("click", () => {
  clipName.value = "";
  clipMsg.textContent = "";
  clipMsg.className = "pw-msg";
  clipSave.disabled = false;
  openModal(clipModal);
  clipName.focus();
});

clipSave.addEventListener("click", async () => {
  clipSave.disabled = true;
  showClipMsg("Saving…", true);
  try {
    const reply = await fetch("/api/clip", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ name: clipName.value.trim() }),
    });
    const data = await reply.json().catch(() => ({}));
    if (reply.ok) {
      showClipMsg("Clip saved.", true, `/media?type=clip&id=${data.id}`);
    } else {
      showClipMsg(data.error || "Could not make the clip.", false);
      clipSave.disabled = false;
    }
  } catch {
    showClipMsg("Could not make the clip.", false);
    clipSave.disabled = false;
  }
});

// ---- channel points and rewards ----

const pointsChip = document.getElementById("points-chip");
const rewardsModal = document.getElementById("rewards-modal");
const rewardsList = document.getElementById("rewards-list");
const rewardsBalance = document.getElementById("rewards-balance");
const rewardsEmpty = document.getElementById("rewards-empty");
const rewardsMsg = document.getElementById("rewards-msg");

let myPoints = 0;
let rewards = [];

function setPoints(n) {
  myPoints = n;
  pointsChip.textContent = `pts ${n}`;
  pointsChip.hidden = false;
  rewardsBalance.textContent = `pts ${n}`;
  renderRewards();
}

// Redeem buttons dim (and disable) when the balance will not cover the cost.
function renderRewards() {
  rewardsList.innerHTML = "";
  rewardsEmpty.hidden = rewards.length > 0;
  rewards.forEach((r) => {
    const row = document.createElement("div");
    row.className = "reward-row";
    const info = document.createElement("span");
    info.className = "reward-info";
    const label = document.createElement("span");
    label.className = "reward-label";
    label.textContent = r.label;
    const cost = document.createElement("span");
    cost.className = "reward-cost muted";
    cost.textContent = `pts ${r.cost}`;
    info.append(label, cost);
    const btn = document.createElement("button");
    btn.type = "button";
    btn.className = "chip-btn";
    btn.textContent = "Redeem";
    btn.disabled = myPoints < r.cost;
    btn.addEventListener("click", () => redeem(r, btn));
    row.append(info, btn);
    rewardsList.appendChild(row);
  });
}

async function loadRewards() {
  try {
    const data = await (await fetch("/api/rewards")).json();
    rewards = data.rewards || [];
    setPoints(data.points || 0);
  } catch {
    /* leave the chip as it is */
  }
}

async function redeem(reward, btn) {
  rewardsMsg.hidden = true;
  btn.disabled = true;
  try {
    const reply = await fetch("/api/redeem", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ reward_id: reward.id }),
    });
    const data = await reply.json().catch(() => ({}));
    if (reply.ok) {
      setPoints(data.points);   // renderRewards re-runs, re-enabling what fits
      showRewardsMsg(`Redeemed ${reward.label}.`, true);
    } else {
      showRewardsMsg(data.detail || "Could not redeem that.", false);
      renderRewards();
    }
  } catch {
    showRewardsMsg("Could not reach the server.", false);
    renderRewards();
  }
}

function showRewardsMsg(text, ok) {
  rewardsMsg.className = "pw-msg " + (ok ? "ok" : "bad");
  rewardsMsg.textContent = text;
  rewardsMsg.hidden = false;
}

pointsChip.addEventListener("click", () => {
  rewardsMsg.hidden = true;
  openModal(rewardsModal);
  loadRewards();   // fetch fresh balance and rewards each time it opens
});

async function boot() {
  if (!(await requireAuth())) return;
  // Let moderators and admins know the commands exist, without cluttering chat
  // for everyone else.
  if (me && (me.admin || me.mod)) {
    chatInput.placeholder = "say something, or /help";
  }
  loadMyProfile();
  loadRewards();
  connectChat();
  checkStream();
}

boot();
