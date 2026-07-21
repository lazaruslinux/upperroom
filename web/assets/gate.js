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
  const reply = await fetch("/api/auth", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ username, password }),
  });
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
    applyAccent(data.accent);
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
