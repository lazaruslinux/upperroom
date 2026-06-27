// Login page. Renders the Turnstile bot check, keeps the Sign in button
// disabled until it passes, then submits the username and password. On success
// the server sets a session cookie and we move to the player.
//
// Turnstile readiness is signaled by window.onloadTurnstileCallback, which is
// defined in the page head and fires only once Cloudflare's API is fully ready.
// We render as soon as both that callback has fired and we have the sitekey,
// in whichever order they happen.

let turnstileToken = "";
let botCheckRequired = false;
let sitekey = null;

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

function renderTurnstile() {
  // Only proceed once Turnstile is ready and we know the sitekey.
  if (!window.turnstileReady || !sitekey) return;
  window.turnstile.render("#turnstile", {
    sitekey: sitekey,
    callback: (token) => {
      turnstileToken = token;
      submitButton.disabled = false;
      clearError();
    },
    "error-callback": (code) => {
      turnstileToken = "";
      showError("Bot check error (" + code + "). Reload the page and try again.");
    },
    "expired-callback": () => {
      turnstileToken = "";
      submitButton.disabled = true;
    },
  });
}

// Called by the head script if Turnstile becomes ready after this file has run.
window.onTurnstileReady = renderTurnstile;

async function init() {
  const config = await (await fetch("/api/config")).json();
  sitekey = config.turnstile_sitekey || "";
  if (!sitekey) {
    // No sitekey means the bot check is turned off (for example local testing).
    return;
  }
  botCheckRequired = true;
  submitButton.disabled = true;
  // If Turnstile is already ready, render now; otherwise the head callback will.
  renderTurnstile();
}

form.addEventListener("submit", async (event) => {
  event.preventDefault();
  if (botCheckRequired && !turnstileToken) {
    showError("The bot check has not finished. Give it a second, then try again.");
    return;
  }
  const username = document.getElementById("username").value;
  const password = document.getElementById("password").value;
  const reply = await fetch("/api/auth", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ username, password, turnstile_token: turnstileToken }),
  });
  if (reply.ok) {
    window.location.href = "/watch";
  } else {
    const data = await reply.json().catch(() => ({}));
    showError(data.error || "Could not sign you in.");
    // Each token is single use, so reset the widget for another attempt.
    turnstileToken = "";
    if (window.turnstile) {
      window.turnstile.reset();
      submitButton.disabled = true;
    }
  }
});

init();
