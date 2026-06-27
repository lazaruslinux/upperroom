// Login page. If a Turnstile sitekey is configured the bot check is loaded and
// required; otherwise this is a plain username and password sign in. The
// Cloudflare script is only loaded when the bot check is actually enabled, so
// nothing third party is fetched when it is turned off.

let turnstileToken = "";
let botCheckRequired = false;

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

function setupTurnstile(sitekey) {
  botCheckRequired = true;
  submitButton.disabled = true;
  // Cloudflare invokes this once its API is ready, so render only then.
  window.onloadTurnstileCallback = function () {
    window.turnstile.render("#turnstile", {
      sitekey: sitekey,
      callback: (token) => {
        turnstileToken = token;
        submitButton.disabled = false;
        clearError();
      },
      "error-callback": () => { turnstileToken = ""; },
      "expired-callback": () => { turnstileToken = ""; submitButton.disabled = true; },
    });
  };
  const script = document.createElement("script");
  script.src = "https://challenges.cloudflare.com/turnstile/v0/api.js" +
    "?onload=onloadTurnstileCallback&render=explicit";
  script.async = true;
  script.defer = true;
  document.head.appendChild(script);
}

async function init() {
  const config = await (await fetch("/api/config")).json();
  if (config.turnstile_sitekey) {
    setupTurnstile(config.turnstile_sitekey);
  }
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
    turnstileToken = "";
    if (window.turnstile && window.turnstile.reset) {
      window.turnstile.reset();
      submitButton.disabled = true;
    }
  }
});

init();
