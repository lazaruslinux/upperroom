// OBS chat overlay. A display-only page OBS adds as a browser source: it shows
// recent chat, join notices, and clip alerts over the broadcast video. It cannot
// sign in, so it authenticates with the bearer key in its own URL (?key=...) and
// connects to the chat socket read-only. It never sends chat and is never counted
// as a viewer.

const overlay = document.getElementById("overlay");

// The host (admin) shows a red video-camera icon; a moderator shows a "mod" tag.
const CAMERA_SVG =
  '<svg viewBox="0 0 24 24" width="16" height="16" fill="currentColor" aria-hidden="true">' +
  '<path d="M3 7.5A1.5 1.5 0 0 1 4.5 6h9A1.5 1.5 0 0 1 15 7.5v9A1.5 1.5 0 0 1 13.5 18h-9' +
  'A1.5 1.5 0 0 1 3 16.5v-9Zm14 3 3.25-2.17a.6.6 0 0 1 .95.5v6.34a.6.6 0 0 1-.95.5L17 13.5v-3Z"/></svg>';

// The bearer key rides in the URL. Without it there is nothing to authenticate,
// so the page just sits blank rather than hammering the socket.
const KEY = new URLSearchParams(location.search).get("key") || "";

// How long each kind of item lingers. The overlay is ambient, not an archive, so
// everything clears itself after a while; chat also caps at a fixed count.
const CHAT_TTL = 45000;
const JOIN_TTL = 10000;
const CLIP_TTL = 12000;
const HIGHLIGHT_TTL = 15000;
const MAX_CHAT = 8;

// ---- accent flavor ----
// Match the channel's brand color once at load, the same data-accent hook the
// rest of the site uses. Best effort: if the poll fails the default green shows.
function applyAccent(value) {
  if (["green", "amber", "blue", "ghost"].includes(value)) {
    document.documentElement.dataset.accent = value;
  }
}

async function loadAccent() {
  try {
    const data = await (await fetch("/api/status")).json();
    applyAccent(data.accent);
  } catch (e) {
    /* leave the default accent in place */
  }
}

// ---- rendering ----

function autoRemove(node, ttl) {
  setTimeout(() => node.remove(), ttl);
}

function append(node, ttl) {
  overlay.appendChild(node);
  autoRemove(node, ttl);
}

function renderChat(msg) {
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
  const item = document.createElement("div");
  item.className = "ov-item ov-join";
  item.textContent = text;
  append(item, JOIN_TTL);
}

function renderClip(msg) {
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
  const item = document.createElement("div");
  item.className = "ov-item ov-highlight";
  const who = document.createElement("span");
  who.className = "ov-highlight-who";
  who.textContent = (msg.user || "someone") + " highlighted:";
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
    // Any other type (presence, hello, ...) is ignored.
  });

  socket.addEventListener("close", () => setTimeout(connect, 5000));
  // An error will be followed by a close, which schedules the retry.
  socket.addEventListener("error", () => { try { socket.close(); } catch (e) {} });
}

loadAccent();
if (KEY) connect();
