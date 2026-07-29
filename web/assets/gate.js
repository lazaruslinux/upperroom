// Login page. A plain username and password sign in, plus an invite-code sign up
// revealed by the "have an invite?" toggle. On success the gate sets a session
// cookie and we send the viewer to the home page.

// On a brand new install no account exists yet; send the visitor to the one-time
// setup wizard instead of showing a login they cannot pass.
(async () => {
  try {
    const data = await (await fetch("/api/setup")).json();
    if (data.needs_setup) { window.location.href = "/setup"; }
  } catch {
    /* if the check fails, leave the login page up */
  }
})();

const form = document.getElementById("login-form");
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
  let reply;
  try {
    reply = await fetch("/api/auth", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ username, password }),
    });
  } catch {
    showError("Could not reach the server.");
    return;
  }
  if (reply.ok) {
    window.location.href = "/home";
  } else {
    const data = await reply.json().catch(() => ({}));
    showError(data.error || "Could not sign you in.");
  }
});

// ---- invite registration ---------------------------------------------------

const registerForm = document.getElementById("register-form");
const rError = document.getElementById("r-error");

document.getElementById("show-register").addEventListener("click", () => {
  form.hidden = true;
  document.getElementById("invite-alt").hidden = true;
  registerForm.hidden = false;
  document.getElementById("r-code").focus();
});

document.getElementById("show-login").addEventListener("click", () => {
  registerForm.hidden = true;
  document.getElementById("invite-alt").hidden = false;
  form.hidden = false;
});

registerForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  rError.hidden = true;
  const body = {
    code: document.getElementById("r-code").value,
    username: document.getElementById("r-username").value,
    display_name: document.getElementById("r-name").value,
    password: document.getElementById("r-password").value,
  };
  let reply;
  try {
    reply = await fetch("/api/register", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
  } catch {
    rError.textContent = "Could not reach the server.";
    rError.hidden = false;
    return;
  }
  if (reply.ok) {
    window.location.href = "/home";
  } else {
    const data = await reply.json().catch(() => ({}));
    rError.textContent = data.error || "Could not create your account.";
    rError.hidden = false;
  }
});

// ---- live status badge ----------------------------------------------------
// Polls the public status endpoint and shows whether the stream is live. When
// it is, the badge counts up from the moment the stream started, ticking
// locally so we do not have to poll just to keep the duration fresh.

// The channel-wide accent flavor rides along on the public status poll, so the
// login page paints the brand color even before anyone signs in. The head
// bootstrap already applied the last-seen value from localStorage; this keeps it
// in sync with the server and remembers it for the next no-flash paint.
function applyAccent(value) {
  if (!["green", "amber", "blue", "ghost"].includes(value)) return;
  if (document.documentElement.dataset.accent !== value) {
    document.documentElement.dataset.accent = value;
    try { localStorage.setItem("selfstream_accent", value); } catch (e) {}
  }
}

// The operator's site name also rides the public status poll, so the login page
// shows their brand (leading "livestream powered by upperroom") before anyone
// signs in. Falls back to the static "upperroom" already in the markup.
function applySiteName(value) {
  if (!value) return;
  const el = document.getElementById("site-title");
  if (el && el.textContent !== value) el.textContent = value;
  if (document.title !== value) document.title = value;
}

const statusBox = document.getElementById("status");
const statusLabel = document.getElementById("status-label");
const statusTime = document.getElementById("status-time");
const statusWatching = document.getElementById("status-watching");
let liveSince = null;
let tick = null;

// While live, the status row also shows how many people are watching, taken from
// the public status poll. Hidden entirely when offline so the row stays clean.
function renderWatching(count) {
  const n = typeof count === "number" ? count : 0;
  statusWatching.textContent = n === 1 ? "1 watching" : `${n} watching`;
  statusWatching.hidden = false;
}

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

// The next announced broadcast. Its time rides the public status poll, so the
// login page counts down to it before anyone signs in; the note that goes with
// it deliberately does not, and shows on the home page after sign in instead.
const nextBox = document.getElementById("next-stream");
const nextCount = document.getElementById("next-count");
const nextWhen = document.getElementById("next-when");
let nextAt = null;
let nextTick = null;

function formatCountdown(seconds) {
  if (seconds <= 0) return "starting soon";
  const days = Math.floor(seconds / 86400);
  const hours = Math.floor((seconds % 86400) / 3600);
  const minutes = Math.floor((seconds % 3600) / 60);
  if (days > 0) return `in ${days}d ${hours}h`;
  if (hours > 0) return `in ${hours}h ${minutes}m`;
  return `in ${Math.max(1, minutes)}m`;
}

function renderNext() {
  if (!nextAt) {
    nextBox.hidden = true;
    if (nextTick) { clearInterval(nextTick); nextTick = null; }
    return;
  }
  const away = nextAt - Math.floor(Date.now() / 1000);
  nextCount.textContent = `Next stream ${formatCountdown(away)}`;
  // The server stores UTC; every viewer reads it in their own time.
  nextWhen.textContent = new Date(nextAt * 1000).toLocaleString([], {
    weekday: "short", month: "short", day: "numeric",
    hour: "numeric", minute: "2-digit",
  });
  nextBox.hidden = false;
  if (!nextTick) nextTick = setInterval(renderNext, 30000);
}

async function refreshStatus() {
  let online = false;
  let since = null;
  let watching = 0;
  try {
    const data = await (await fetch("/api/status")).json();
    online = !!data.online;
    since = data.since;
    watching = data.watching;
    applyAccent(data.accent);
    applySiteName(data.site_name);
    // Once the stream is actually on, a countdown to it is just noise.
    nextAt = online ? null : (data.next_stream_at || null);
    renderNext();
  } catch {
    online = false;
  }

  if (online) {
    statusBox.className = "status status-live";
    statusLabel.textContent = "Live";
    liveSince = since || Math.floor(Date.now() / 1000);
    renderLive();
    renderWatching(watching);
    if (!tick) tick = setInterval(renderLive, 30000);
  } else {
    statusBox.className = "status status-offline";
    statusLabel.textContent = "Offline";
    statusTime.textContent = "";
    statusWatching.hidden = true;
    liveSince = null;
    if (tick) { clearInterval(tick); tick = null; }
  }
}

refreshStatus();
setInterval(refreshStatus, 20000);

// Register the pass-through service worker. It caches nothing; it exists so
// Chrome will offer to install the site to a phone's home screen.
if ("serviceWorker" in navigator) {
  window.addEventListener("load", () => {
    navigator.serviceWorker.register("/sw.js").catch(() => {});
  });
}
