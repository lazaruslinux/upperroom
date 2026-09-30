// The sign-in page. A username and password, plus an invite-code sign up
// behind "Have an invite code?". On success the gate sets a session cookie and
// the viewer goes where the channel is: the room if somebody is on air, home
// if nobody is. The same page is an invite link at /join#<code>.

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
const registerForm = document.getElementById("register-form");
const rError = document.getElementById("r-error");
const showRegister = document.getElementById("show-register");
const showLogin = document.getElementById("show-login");
const doorName = document.getElementById("door-name");
const doorSub = document.getElementById("door-sub");

let live = false;

// The lamp and the name on the door both ride the public status poll, so the
// page wears the operator's brand before anyone has signed in.
mountStrip({
  onStatus: (data) => {
    live = !!data.online;
    if (data.site_name) doorName.textContent = data.site_name;
    doorSub.textContent = live ? "is on air" : "is off air";
  },
});

// Where a fresh session goes: the room while live, home while not. Asked
// again at the moment of signing in rather than trusted from the last poll,
// which can be up to a quarter of a minute old.
async function landing() {
  try {
    const data = await (await fetch("/api/status")).json();
    return data.online ? "/watch" : "/home";
  } catch {
    return live ? "/watch" : "/home";
  }
}

function showError(box, message) {
  box.textContent = message;
  box.hidden = false;
}

form.addEventListener("submit", async (event) => {
  event.preventDefault();
  errorBox.hidden = true;
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
    showError(errorBox, "Could not reach the server.");
    return;
  }
  if (reply.ok) {
    window.location.href = await landing();
  } else {
    const data = await reply.json().catch(() => ({}));
    showError(errorBox, data.error || "Could not sign you in.");
  }
});

// ---- invite registration ---------------------------------------------------

function showForm(register) {
  form.hidden = register;
  registerForm.hidden = !register;
  showRegister.hidden = register;
  showLogin.hidden = !register;
  document.getElementById(register ? "r-code" : "username").focus();
}

showRegister.addEventListener("click", () => showForm(true));
showLogin.addEventListener("click", () => showForm(false));

// An invite link. The code rides after the #, which a browser never sends, so
// no server, log or link preview sees it; it goes into the form and then out
// of the address bar and the history. Somebody already signed in goes home.
if (window.location.pathname === "/join") {
  let code = "";
  try { code = decodeURIComponent(window.location.hash.slice(1)).trim(); } catch {}
  showForm(true);
  if (code) {
    document.getElementById("r-code").value = code;
    document.getElementById("r-username").focus();
  }
  if (window.location.hash) history.replaceState(null, "", "/join");
  (async () => {
    try {
      const me = await (await fetch("/api/me")).json();
      if (me.authed) window.location.href = "/home";
    } catch { /* stay on the form */ }
  })();
}

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
    showError(rError, "Could not reach the server.");
    return;
  }
  if (reply.ok) {
    window.location.href = await landing();
  } else {
    const data = await reply.json().catch(() => ({}));
    showError(rError, data.error || "Could not create your account.");
  }
});

// Register the service worker. It caches nothing; it shows the go-live push,
// and it is why Chrome will offer to install the site to a phone's home screen.
if ("serviceWorker" in navigator) {
  window.addEventListener("load", () => {
    navigator.serviceWorker.register("/sw.js").catch(() => {});
  });
}
