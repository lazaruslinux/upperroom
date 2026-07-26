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
  // Name the browser tab after the operator's site, not the platform.
  if (data.site_name && document.title !== data.site_name) document.title = data.site_name;
  applyClipLength(data.clip_seconds);
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

// Role marks sit just to the right of the avatar, in place of any "(admin)"
// text. The host (admin) shows a bright-red video-camera icon that reads as
// "broadcaster"; a moderator shows a small blue "mod" tag. An admin keeps every
// moderator power, so an admin shows only the camera.
const CAMERA_SVG =
  '<svg viewBox="0 0 24 24" width="13" height="13" fill="currentColor" aria-hidden="true">' +
  '<path d="M3 7.5A1.5 1.5 0 0 1 4.5 6h9A1.5 1.5 0 0 1 15 7.5v9A1.5 1.5 0 0 1 13.5 18h-9' +
  'A1.5 1.5 0 0 1 3 16.5v-9Zm14 3 3.25-2.17a.6.6 0 0 1 .95.5v6.34a.6.6 0 0 1-.95.5L17 13.5v-3Z"/></svg>';

function roleBadgeNode(admin, mod, big) {
  if (!admin && !mod) return null;
  const span = document.createElement("span");
  if (admin) {
    span.className = "role-tag host" + (big ? " role-tag-lg" : "");
    span.title = "Broadcaster";
    span.innerHTML = CAMERA_SVG;   // a static, trusted icon; no user data
  } else {
    span.className = "role-tag mod" + (big ? " role-tag-lg" : "");
    span.title = "Moderator";
    span.textContent = "mod";
  }
  return span;
}

function formatTimestamp(ts) {
  // 24-hour local time, no date, e.g. "17:51". Chat is ephemeral, so the day
  // only matters on saved VODs and clips, where it is shown on the media page.
  const d = ts ? new Date(ts * 1000) : new Date();
  const h = String(d.getHours()).padStart(2, "0");
  const m = String(d.getMinutes()).padStart(2, "0");
  return `${h}:${m}`;
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
  // Each person's chosen name color, if any, overrides the theme default (and the
  // admin amber). Colors are guarded server-side for readability.
  if (msg.name_color) name.style.color = msg.name_color;
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
    // Each person's own font and message color ride along on their messages for
    // everyone to see.
    body.style.fontFamily = FONTS[msg.font] || "";
    if (msg.msg_color) body.style.color = msg.msg_color;
  }
  bodyWrap.append(head, body);
  line.appendChild(bodyWrap);
  // Host and moderators get a hover delete button on every line, removing that
  // one message for everyone by its id.
  if (me && (me.admin || me.mod) && msg.id != null) {
    const del = document.createElement("button");
    del.type = "button";
    del.className = "msg-del";
    del.title = "Delete message";
    del.setAttribute("aria-label", "Delete message");
    del.textContent = "×";
    del.addEventListener("click", (e) => {
      e.stopPropagation();
      if (socket && socket.readyState === WebSocket.OPEN) {
        socket.send(JSON.stringify({ type: "moddelete", id: msg.id }));
      }
    });
    line.appendChild(del);
  }
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

// A highlight reads as a spotlighted chat line: the viewer's display name and the
// message they spent points on, inside an accent border. textContent only, so a
// crafted message can never inject markup.
function renderHighlight(msg) {
  const line = document.createElement("div");
  line.className = "msg highlight";
  const name = document.createElement("span");
  name.className = "highlight-name";
  name.textContent = msg.user;
  const body = document.createElement("span");
  body.className = "highlight-body";
  body.textContent = msg.message;
  line.append(name, body);
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
    if (viewer.name_color) label.style.color = viewer.name_color;
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
    else if (msg.type === "highlight") renderHighlight(msg);
    else if (msg.type === "presence") renderPresence(msg);
    else if (msg.type === "delete") applyDelete(msg.id);
    else if (msg.type === "wipe") messages.innerHTML = "";
    else if (msg.type === "hello") {
      me = me || msg.you;
      msg.history.forEach(renderChat);
      chatLive = true;   // everything after the backlog is live
    }
  });

  // If the connection drops, wait a moment and reconnect. Not, however, when
  // the server closed it because this session is no longer welcome: 4401 (no
  // valid account behind the cookie, which includes a guest whose pass ran out)
  // and 4403 (country) are answers, not blips, and retrying every three seconds
  // forever would be a loop that only stops when the tab closes.
  socket.addEventListener("close", (event) => {
    if (event.code === 4401 || event.code === 4403) {
      if (me && me.guest) endGuestSession();
      return;
    }
    setTimeout(connectChat, 3000);
  });

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

// ---- chat colors (name + message text) ----
// Each viewer can color their own display name and message text. The choice is
// saved on the server so it rides along on their messages for everyone, and the
// server guards it for readability (rejecting invisible or reserved-red picks).
const nameColorInput = document.getElementById("name-color");
const msgColorInput = document.getElementById("msg-color");
const colorReset = document.getElementById("color-reset");
const colorMsg = document.getElementById("color-msg");
const DEFAULT_SWATCH = "#e6e8e2";

function showColorMsg(text, ok) {
  colorMsg.textContent = text || "";
  colorMsg.className = "settings-note pw-msg" + (text ? (ok ? " ok" : " bad") : "");
}

// Like saveProfile but returns the server's error text, so a rejected color can
// explain why (too dark, reserved red, malformed).
async function saveColor(patch) {
  try {
    const reply = await fetch("/api/profile", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(patch),
    });
    const data = await reply.json().catch(() => ({}));
    return { ok: reply.ok, error: data.error };
  } catch {
    return { ok: false, error: "Could not reach the server." };
  }
}

function setupColorPickers() {
  nameColorInput.value = me.name_color || DEFAULT_SWATCH;
  msgColorInput.value = me.msg_color || DEFAULT_SWATCH;
  nameColorInput.addEventListener("change", async () => {
    const r = await saveColor({ name_color: nameColorInput.value });
    if (r.ok) { me.name_color = nameColorInput.value; showColorMsg("Name color saved.", true); }
    else showColorMsg(r.error || "That color was rejected.", false);
  });
  msgColorInput.addEventListener("change", async () => {
    const r = await saveColor({ msg_color: msgColorInput.value });
    if (r.ok) { me.msg_color = msgColorInput.value; showColorMsg("Text color saved.", true); }
    else showColorMsg(r.error || "That color was rejected.", false);
  });
  colorReset.addEventListener("click", async () => {
    const r = await saveColor({ name_color: "", msg_color: "" });
    if (r.ok) {
      me.name_color = ""; me.msg_color = "";
      nameColorInput.value = DEFAULT_SWATCH;
      msgColorInput.value = DEFAULT_SWATCH;
      showColorMsg("Colors reset to default.", true);
    } else showColorMsg(r.error || "Could not reset.", false);
  });
}

// Reflect the signed in account's saved chat font and colors in the settings
// panel. Avatar, bio, and password now live in the home page's "Your settings".
function loadMyProfile() {
  if (!me) return;
  buildFontPicker();
  setupColorPickers();
}

// ---- profile popup (tap any avatar) ----

const profileModal = document.getElementById("profile-modal");
const profileAvatar = document.getElementById("profile-avatar");
const profileName = document.getElementById("profile-name");
const profileBio = document.getElementById("profile-bio");
const profileJoined = document.getElementById("profile-joined");
const profilePoints = document.getElementById("profile-points");
const profileModActions = document.getElementById("profile-modactions");

function sendChatCommand(text) {
  if (socket && socket.readyState === WebSocket.OPEN) {
    socket.send(JSON.stringify({ type: "chat", text }));
  }
}

// Host and moderators get quick moderation actions inside a viewer's profile
// popup: timeout, purge, ban, and (admin only) promote/demote. Each sends the
// matching chat command over the socket; the server authorizes it against a
// fresh role read and replies privately with the outcome.
function buildModActions(data) {
  profileModActions.hidden = true;
  profileModActions.innerHTML = "";
  if (!me || !(me.admin || me.mod)) return;           // plain viewers see none
  if (data.username === me.username) return;           // not on yourself
  if (data.admin && !me.admin) return;                 // a mod can't act on an admin
  const u = data.username;
  const actions = [
    ["Timeout 5m", () => sendChatCommand(`/timeout ${u} 300`)],
    ["Timeout 1h", () => sendChatCommand(`/timeout ${u} 3600`)],
    ["Delete all", () => { if (confirm(`Delete all of ${data.name}'s messages?`)) sendChatCommand(`/purge ${u}`); }],
    ["Ban", () => { if (confirm(`Ban ${data.name} from chat?`)) sendChatCommand(`/ban ${u}`); }],
  ];
  if (me.admin) {
    actions.push(data.mod
      ? ["Remove mod", () => sendChatCommand(`/unmod ${u}`)]
      : ["Make mod", () => sendChatCommand(`/mod ${u}`)]);
  }
  actions.forEach(([label, fn]) => {
    const btn = document.createElement("button");
    btn.type = "button";
    btn.className = "pill mod-action";
    btn.textContent = label;
    btn.addEventListener("click", () => { fn(); closeModal(profileModal); });
    profileModActions.appendChild(btn);
  });
  profileModActions.hidden = false;
}

function formatJoined(ts) {
  // Date only, in a compact M.D.YY style (chat timestamps are time-only now).
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
    buildModActions(data);
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

// ---- clipping the recent stream ----

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

// The instant the viewer was actually looking at when they pressed Clip.
//
// This is the whole of clip accuracy. The old code let the server use its own
// clock at the moment the SAVE request arrived, which is wrong by however long
// the viewer spent typing a name, plus however far behind the live edge their
// player happens to be. Both of those are seconds, and the second one varies per
// viewer, so no fixed correction can fix it.
//
// MediaMTX stamps its playlist with EXT-X-PROGRAM-DATE-TIME, so hls.js can tell
// us the exact wall-clock time of the frame on screen via playingDate. When that
// is unavailable (Safari playing HLS natively, or a source without the stamp) we
// fall back to now minus the measured latency, and failing that send nothing at
// all and let the server use its own estimate.
// The clip length is a channel setting, so the button and the modal heading are
// labelled from what the server reports rather than from a number written into
// the markup. Called by the status poll.
function applyClipLength(seconds) {
  if (!seconds || seconds === clipLength) return;
  clipLength = seconds;
  const label = seconds % 60 === 0 && seconds >= 60
    ? `Clip the last ${seconds / 60} minute${seconds === 60 ? "" : "s"}`
    : `Clip the last ${seconds} seconds`;
  if (clipBtn) {
    clipBtn.setAttribute("aria-label", label);
    clipBtn.setAttribute("title", label);
  }
  const heading = document.getElementById("clip-heading");
  if (heading) heading.textContent = label;
}

let clipLength = 0;

function currentFrameInstant() {
  try {
    if (hls && hls.playingDate) return hls.playingDate.getTime() / 1000;
    if (hls && typeof hls.latency === "number" && hls.latency > 0) {
      return Date.now() / 1000 - hls.latency;
    }
  } catch (e) {
    /* fall through and let the server estimate */
  }
  return null;
}

// Captured on Clip, sent on Save, so typing a name cannot move the window.
let clipInstant = null;

clipBtn.addEventListener("click", () => {
  clipInstant = currentFrameInstant();
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
      body: JSON.stringify({ name: clipName.value.trim(), at: clipInstant }),
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

// ---- channel points and the highlight redemption ----

const pointsChip = document.getElementById("points-chip");
const highlightModal = document.getElementById("highlight-modal");
const highlightBalance = document.getElementById("highlight-balance");
const highlightCostEl = document.getElementById("highlight-cost");
const highlightInput = document.getElementById("highlight-input");
const highlightSend = document.getElementById("highlight-send");
const highlightMsg = document.getElementById("highlight-msg");

let myPoints = 0;
let highlightCost = 50;

function setPoints(n) {
  myPoints = n;
  pointsChip.textContent = `pts ${n}`;
  pointsChip.hidden = false;
  highlightBalance.textContent = `pts ${n}`;
  updateHighlightSend();
}

// Send stays disabled until the balance covers the cost and there is something
// to say.
function updateHighlightSend() {
  highlightSend.disabled = myPoints < highlightCost || !highlightInput.value.trim();
}

async function loadPoints() {
  try {
    const data = await (await fetch("/api/points")).json();
    if (typeof data.cost === "number") highlightCost = data.cost;
    highlightCostEl.textContent = `pts ${highlightCost}`;
    setPoints(data.points || 0);
  } catch {
    /* leave the chip as it is */
  }
}

async function sendHighlight() {
  const message = highlightInput.value.trim();
  if (!message) return;
  highlightMsg.hidden = true;
  highlightSend.disabled = true;
  try {
    const reply = await fetch("/api/redeem", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ message }),
    });
    const data = await reply.json().catch(() => ({}));
    if (reply.ok) {
      highlightInput.value = "";
      setPoints(data.points);   // updates the chip and the balance line
      closeModal(highlightModal);
    } else {
      showHighlightMsg(data.detail || data.error || "Could not highlight that.", false);
      updateHighlightSend();
    }
  } catch {
    showHighlightMsg("Could not reach the server.", false);
    updateHighlightSend();
  }
}

function showHighlightMsg(text, ok) {
  highlightMsg.className = "pw-msg " + (ok ? "ok" : "bad");
  highlightMsg.textContent = text;
  highlightMsg.hidden = false;
}

highlightInput.addEventListener("input", updateHighlightSend);
highlightInput.addEventListener("keydown", (e) => {
  if (e.key === "Enter" && !highlightSend.disabled) sendHighlight();
});
highlightSend.addEventListener("click", sendHighlight);

pointsChip.addEventListener("click", () => {
  highlightMsg.hidden = true;
  openModal(highlightModal);
  loadPoints();   // fetch a fresh balance and cost each time it opens
});

// ---- guest passes ---------------------------------------------------------
// A guest watches and chats and does nothing else, so the controls that need an
// account are removed rather than left to fail on a 403. The countdown is drawn
// from the absolute expiry the server sent, so it does not drift while the page
// sits open and does not care whether the visitor's clock is right.

const guestTimer = document.getElementById("guest-timer");
const guestOver = document.getElementById("guest-over");
let guestTick = null;

function formatRemaining(seconds) {
  const minutes = Math.floor(seconds / 60);
  const rest = seconds % 60;
  if (minutes > 0) return `${minutes}:${String(rest).padStart(2, "0")} left`;
  return `${rest}s left`;
}

function endGuestSession() {
  if (guestTick) { clearInterval(guestTick); guestTick = null; }
  guestTimer.textContent = "pass ended";
  guestTimer.classList.add("is-out");
  // Stop the video rather than let it stall on its own: the next segment would
  // be refused by /api/verify anyway, and a spinner reads as a broken stream
  // instead of a pass that ran out.
  try {
    video.pause();
    if (hls) { hls.destroy(); hls = null; }
  } catch (e) { /* nothing to stop */ }
  video.hidden = true;
  offline.hidden = true;
  guestOver.hidden = false;
  // Chat goes with it. The socket is closed from the server side by the reaper,
  // but do not leave a live-looking composer behind in the meantime.
  chatInput.disabled = true;
  chatInput.placeholder = "your guest pass has ended";
}

function renderGuestTimer() {
  const left = me.guest_expires_at - Math.floor(Date.now() / 1000);
  if (left <= 0) {
    endGuestSession();
    return;
  }
  guestTimer.textContent = formatRemaining(left);
  // The last five minutes get a warning look, so the end is not a surprise.
  guestTimer.classList.toggle("is-low", left <= 300);
}

function setUpGuest() {
  if (!me || !me.guest) return;
  // Clipping, points and the highlight composer all need an account.
  clipBtn.remove();
  pointsChip.remove();
  const note = document.querySelector("#settings-panel .settings-note.muted");
  if (note) {
    note.textContent =
      "You are watching as a guest. Sign in or use an invite code for an account.";
  }
  // The home link goes nowhere useful for a guest; point it at the way in.
  const homeLink = document.querySelector(".chat-head a[href='/home']");
  if (homeLink) {
    homeLink.setAttribute("href", "/");
    homeLink.setAttribute("aria-label", "Sign in");
  }
  guestTimer.hidden = false;
  renderGuestTimer();
  guestTick = setInterval(renderGuestTimer, 1000);
}

async function boot() {
  if (!(await requireAuth())) return;
  // Let moderators and admins know the commands exist, without cluttering chat
  // for everyone else.
  if (me && (me.admin || me.mod)) {
    chatInput.placeholder = "say something, or /help";
  }
  setUpGuest();
  loadMyProfile();
  // A guest has no balance and the endpoint refuses them, so do not ask.
  if (!me.guest) loadPoints();
  connectChat();
  checkStream();
}

boot();
