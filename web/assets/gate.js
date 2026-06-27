// Login page. A plain username and password sign in. On success the gate sets
// a session cookie and we send the viewer to the watch page.

const form = document.getElementById("login-form");
const submitButton = form.querySelector("button[type=submit]");
const errorBox = document.getElementById("error");

function showError(message) {
  errorBox.textContent = message;
  errorBox.hidden = false;
}

function clearError() {
  errorBox.hidden = true;
}

form.addEventListener("submit", async (event) => {
  event.preventDefault();
  clearError();
  const username = document.getElementById("username").value;
  const password = document.getElementById("password").value;
  const reply = await fetch("/api/auth", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ username, password }),
  });
  if (reply.ok) {
    window.location.href = "/watch";
  } else {
    const data = await reply.json().catch(() => ({}));
    showError(data.error || "Could not sign you in.");
  }
});

// ---- live status badge ----------------------------------------------------
// Polls the public status endpoint and shows whether the stream is live. When
// it is, the badge counts up from the moment the stream started, ticking
// locally so we do not have to poll just to keep the duration fresh.

const statusBox = document.getElementById("status");
const statusLabel = document.getElementById("status-label");
const statusTime = document.getElementById("status-time");
let liveSince = null;
let tick = null;

function formatStarted(seconds) {
  // Keep a friendly "just started" for the first ten minutes, then count up.
  if (seconds < 600) return "just started";
  const hours = Math.floor(seconds / 3600);
  const minutes = Math.floor((seconds % 3600) / 60);
  if (hours > 0) return `started ${hours}h ${minutes}m ago`;
  return `started ${minutes} minutes ago`;
}

function renderLive() {
  const elapsed = Math.floor(Date.now() / 1000) - liveSince;
  statusTime.textContent = formatStarted(elapsed);
}

async function refreshStatus() {
  let online = false;
  let since = null;
  try {
    const data = await (await fetch("/api/status")).json();
    online = !!data.online;
    since = data.since;
  } catch {
    online = false;
  }

  if (online) {
    statusBox.className = "status status-live";
    statusLabel.textContent = "Live";
    liveSince = since || Math.floor(Date.now() / 1000);
    renderLive();
    if (!tick) tick = setInterval(renderLive, 30000);
  } else {
    statusBox.className = "status status-offline";
    statusLabel.textContent = "Offline";
    statusTime.textContent = "";
    liveSince = null;
    if (tick) { clearInterval(tick); tick = null; }
  }
}

refreshStatus();
setInterval(refreshStatus, 20000);
