// First-run setup. Makes the first account, as the admin, and names the site.
// On success the gate signs the account in and this sends it home. If setup
// is already done (any account exists) the page goes to sign in instead: the
// wizard is a one-time bootstrap.

const form = document.getElementById("setup-form");
const errorBox = document.getElementById("error");

// The lamp strip, with no menu: nobody is signed in yet. It also brings the
// channel's accent in from the public status poll.
mountStrip({ siteName: "upperroom", pageName: "setup" });

function showError(message) {
  errorBox.textContent = message;
  errorBox.hidden = false;
}

// On load, confirm setup is still needed. The gate is the real gate; this only
// keeps the wizard from showing once an account exists.
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
    site_name: document.getElementById("site-name").value,
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
