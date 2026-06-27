// Login page. Loads the Turnstile widget, then handles the sign in form. On
// success the server sets a session cookie and we move to the player.

let turnstileToken = "";

async function loadConfig() {
  const reply = await fetch("/api/config");
  const config = await reply.json();
  if (window.turnstile && config.turnstile_sitekey) {
    window.turnstile.render("#turnstile", {
      sitekey: config.turnstile_sitekey,
      callback: (token) => { turnstileToken = token; },
    });
  }
}

function showError(message) {
  const box = document.getElementById("error");
  box.textContent = message;
  box.hidden = false;
}

document.getElementById("login-form").addEventListener("submit", async (event) => {
  event.preventDefault();
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
    if (window.turnstile) window.turnstile.reset();
  }
});

// Turnstile loads asynchronously, so wait for it before rendering the widget.
window.addEventListener("load", () => {
  const wait = setInterval(() => {
    if (window.turnstile) {
      clearInterval(wait);
      loadConfig();
    }
  }, 100);
});
