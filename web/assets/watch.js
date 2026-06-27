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
  const name = document.createElement("span");
  name.className = msg.admin ? "name admin" : "name";
  name.textContent = msg.name;
  const body = document.createElement("span");
  body.className = "body";
  body.textContent = msg.text;        // textContent keeps any HTML inert
  line.append(name, document.createTextNode(" "), body);
  addLine(line);
}

function renderSystem(msg) {
  const line = document.createElement("div");
  line.className = "msg system";
  line.textContent = msg.text;
  addLine(line);
}

function renderPresence(msg) {
  viewerCount.textContent = msg.count === 1 ? "1 watching" : `${msg.count} watching`;
  viewerList.innerHTML = "";
  msg.viewers.forEach((viewer) => {
    const item = document.createElement("li");
    const youSuffix = me && viewer.username === me.username ? " (you)" : "";
    item.textContent = viewer.name + youSuffix;
    viewerList.appendChild(item);
  });
}

function connectChat() {
  const scheme = location.protocol === "https:" ? "wss" : "ws";
  const socket = new WebSocket(`${scheme}://${location.host}/ws`);

  socket.addEventListener("message", (event) => {
    const msg = JSON.parse(event.data);
    if (msg.type === "chat") renderChat(msg);
    else if (msg.type === "system") renderSystem(msg);
    else if (msg.type === "presence") renderPresence(msg);
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
    if (!text || socket.readyState !== WebSocket.OPEN) return;
    socket.send(JSON.stringify({ type: "chat", text }));
    chatInput.value = "";
  };
}

document.getElementById("viewer-toggle").addEventListener("click", () => {
  viewerList.hidden = !viewerList.hidden;
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
  connectChat();
  checkStream();
}

boot();
