// Guest pass redemption.
//
// The one page besides the login page that a visitor reaches with no session.
// Deliberately small: a code, a name, and a question, then straight to /watch.
// It shows the same live/offline badge the login page does, so someone holding
// a pass can tell whether there is anything on before they spend it.

// Already signed in? Nothing here applies. A member should not be redeeming a
// guest pass over the top of their own account, and a guest who comes back to
// this page still has a session, so send both to where they were going.
(async () => {
  try {
    const me = await (await fetch("/api/me")).json();
    if (me.authed) window.location.href = me.guest ? "/watch" : "/home";
  } catch {
    /* if the check fails, leave the form up */
  }
})();

const form = document.getElementById("guest-form");
const errorBox = document.getElementById("g-error");
const questionBox = document.getElementById("g-question");
const answerBox = document.getElementById("g-answer");
const submitButton = document.getElementById("g-submit");

// The signed token that goes back with the answer. Held here rather than in a
// hidden field so a stale one cannot be resubmitted after a refresh.
let challengeToken = "";

function showError(message) {
  errorBox.textContent = message;
  errorBox.hidden = false;
}

async function loadChallenge() {
  answerBox.value = "";
  try {
    const reply = await fetch("/api/guest/challenge");
    if (!reply.ok) {
      questionBox.textContent = "Could not load a question. Reload the page.";
      challengeToken = "";
      return;
    }
    const data = await reply.json();
    questionBox.textContent = data.question;
    challengeToken = data.token;
  } catch {
    questionBox.textContent = "Could not load a question. Reload the page.";
    challengeToken = "";
  }
}

loadChallenge();

form.addEventListener("submit", async (event) => {
  event.preventDefault();
  errorBox.hidden = true;
  if (!challengeToken) {
    showError("Could not load a question. Reload the page.");
    return;
  }
  // A pass is single use, so a double submit could spend it and then report a
  // failure for the second press. Lock the button for the round trip.
  submitButton.disabled = true;
  const body = {
    code: document.getElementById("g-code").value,
    name: document.getElementById("g-name").value,
    challenge: answerBox.value,
    challenge_token: challengeToken,
  };
  let reply;
  try {
    reply = await fetch("/api/guest", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
  } catch {
    submitButton.disabled = false;
    showError("Could not reach the server.");
    return;
  }
  if (reply.ok) {
    window.location.href = "/watch";
    return;
  }
  submitButton.disabled = false;
  const data = await reply.json().catch(() => ({}));
  showError(data.error || "Could not start your guest session.");
  // Every failure burns the question, whether or not it was the question that
  // was wrong: the token is only good for the answer it was issued against, so
  // leaving a spent one on screen would fail the next attempt for the wrong
  // reason.
  loadChallenge();
});

// ---- live status badge ----------------------------------------------------
// The same badge the login page shows, and the same reasoning: the accent and
// the site name ride along on the public status poll so this page paints the
// operator's brand without a session. Kept as a private copy rather than shared,
// following the convention the rest of web/assets uses.

function applyAccent(value) {
  if (!["green", "amber", "blue", "ghost"].includes(value)) return;
  if (document.documentElement.dataset.accent !== value) {
    document.documentElement.dataset.accent = value;
    try { localStorage.setItem("selfstream_accent", value); } catch (e) {}
  }
}

function applySiteName(value) {
  if (!value) return;
  const el = document.getElementById("site-title");
  if (el && el.textContent !== value) el.textContent = value;
  if (document.title !== value) document.title = value;
}

const statusBox = document.getElementById("status");
const statusLabel = document.getElementById("status-label");
const statusTime = document.getElementById("status-time");
let liveSince = null;
let tick = null;

function formatStarted(seconds) {
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
    applySiteName(data.site_name);
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
