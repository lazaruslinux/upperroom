// OBS chat overlay. A display-only page OBS adds as a browser source: it shows
// recent chat, join notices, and clip alerts over the broadcast video. It cannot
// sign in, so it authenticates with the bearer key in its own URL (?key=...) and
// connects to the chat socket read-only. It never sends chat and is never counted
// as a viewer.

const overlay = document.getElementById("overlay");

// The bearer key rides in the URL. Without it there is nothing to authenticate,
// so the page just sits blank rather than hammering the socket.
const KEY = new URLSearchParams(location.search).get("key") || "";

// How long each kind of item lingers. The overlay is ambient, not an archive, so
// everything clears itself after a while; chat also caps at a fixed count.
const CHAT_TTL = 45000;
const JOIN_TTL = 10000;
const CLIP_TTL = 12000;
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

  // Role tag chip (op for admin, mod for moderator), like the site.
  if (msg.admin || msg.mod) {
    const tag = document.createElement("span");
    tag.className = "ov-tag " + (msg.admin ? "op" : "mod");
    tag.textContent = msg.admin ? "op" : "mod";
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
