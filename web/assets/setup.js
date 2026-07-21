// First-run setup wizard. Creates the first account as admin and names the
// channel. On success the gate signs the account in and sends it to the home
// page. If setup is already done (any account exists) this page redirects to the
// login page: the wizard is a one-time bootstrap.

const form = document.getElementById("setup-form");
const errorBox = document.getElementById("error");

// Sync the channel accent from the public status endpoint (the head bootstrap
// already painted the last-seen value from localStorage). A fresh install is
// green, but this keeps the wizard consistent with the rest of the site.
function applyAccent(value) {
  if (!["green", "amber", "blue", "ghost"].includes(value)) return;
  if (document.documentElement.dataset.accent !== value) {
    document.documentElement.dataset.accent = value;
    try { localStorage.setItem("selfstream_accent", value); } catch (e) {}
  }
}
(async () => {
  try { applyAccent((await (await fetch("/api/status")).json()).accent); } catch (e) {}
})();

function showError(message) {
  errorBox.textContent = message;
  errorBox.hidden = false;
}

// On load, confirm setup is still needed. The gate is the real gate; this check
// only keeps the wizard from showing after an account already exists.
(async () => {
  try {
    const data = await (await fetch("/api/setup")).json();
    if (!data.needs_setup) { window.location.href = "/"; }
  } catch {
    /* if the check fails, leave the form up; the POST is still guarded */
  }
})();

form.addEventListener("submit", async (event) => {
  event.preventDefault();
  errorBox.hidden = true;
  const body = {
    username: document.getElementById("username").value,
    display_name: document.getElementById("display-name").value,
    password: document.getElementById("password").value,
    channel_name: document.getElementById("channel-name").value,
  };
  let reply;
  try {
    reply = await fetch("/api/setup", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
  } catch {
    showError("Could not reach the server.");
    return;
  }
  if (reply.ok) {
    window.location.href = "/home";
  } else {
    const data = await reply.json().catch(() => ({}));
    showError(data.error || "Could not complete setup.");
  }
});
