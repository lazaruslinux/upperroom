// Options page. Everything about your own account: the theme, whether the site
// emails you when the stream starts, your display name, avatar and bio, and
// your password.
//
// This used to be a modal hanging off the shared top bar. It is a page now, so
// the bar carries a link rather than a whole settings panel, and the crop stage
// is the only modal left.

const CROP = 256;
const THEME_KEY = "selfstream_theme";

let me = null;

function avatarColor(seed) {
  let hash = 0;
  for (let i = 0; i < seed.length; i++) hash = (hash * 31 + seed.charCodeAt(i)) % 360;
  return `hsl(${hash}, 55%, 45%)`;
}

function avatarNode(username, name, version, cls) {
  if (version) {
    const img = document.createElement("img");
    img.className = cls;
    img.alt = "";
    img.src = `/api/avatar/${encodeURIComponent(username)}?v=${version}`;
    return img;
  }
  const span = document.createElement("span");
  span.className = cls;
  span.textContent = (name || username || "?").trim().charAt(0).toUpperCase();
  span.style.background = avatarColor(username || "?");
  return span;
}

async function requireAuth() {
  let data;
  try {
    data = await (await fetch("/api/me")).json();
  } catch {
    data = { authed: false };
  }
  if (!data.authed) {
    window.location.href = "/";
    return false;
  }
  // A guest pass buys the stream and chat, nothing else. Nothing here would
  // outlast their pass, so send them where it works.
  if (data.guest) {
    window.location.href = "/watch";
    return false;
  }
  me = data;
  return true;
}

async function saveProfile(patch) {
  try {
    const reply = await fetch("/api/profile", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(patch),
    });
    return reply.ok;
  } catch {
    return false;
  }
}

function flash(button, text) {
  button.textContent = text;
  setTimeout(() => { button.textContent = "Save"; }, 1500);
}

// ---- theme ----

const themeToggle = document.getElementById("theme-toggle");

function applyTheme(theme) {
  document.documentElement.dataset.theme = theme;
  try { localStorage.setItem(THEME_KEY, theme); } catch {}
  themeToggle.textContent = theme === "light" ? "Light" : "Dark";
}

function wireTheme() {
  let saved = "dark";
  try { saved = localStorage.getItem(THEME_KEY) || "dark"; } catch {}
  applyTheme(saved);
  themeToggle.addEventListener("click", () => {
    applyTheme(document.documentElement.dataset.theme === "light" ? "dark" : "light");
  });
}

// ---- profile, notifications, password ----

const myAvatar = document.getElementById("my-avatar");
const nameInput = document.getElementById("name-input");
const nameSave = document.getElementById("name-save");
const bioInput = document.getElementById("bio-input");
const bioSave = document.getElementById("bio-save");
const emailInput = document.getElementById("email-input");
const emailSave = document.getElementById("email-save");
const notifyToggle = document.getElementById("notify-toggle");
const notifySection = document.getElementById("notify-section");
const pwCurrent = document.getElementById("pw-current");
const pwNew = document.getElementById("pw-new");
const pwSave = document.getElementById("pw-save");
const pwMsg = document.getElementById("pw-msg");

function renderMyAvatar() {
  myAvatar.innerHTML = "";
  myAvatar.appendChild(avatarNode(me.username, me.name, me.avatar || 0, "avatar avatar-lg"));
  // The top bar shows the same face, so a change here lands there too instead
  // of waiting for a reload to catch up.
  const inBar = document.getElementById("nav-avatar");
  if (inBar) {
    inBar.innerHTML = "";
    inBar.appendChild(avatarNode(me.username, me.name, me.avatar || 0, "avatar"));
  }
}

function renderSettings() {
  nameInput.value = me.name || "";
  bioInput.value = me.bio || "";
  renderMyAvatar();
  // The recipient query filters admins out, so the server has never emailed a
  // host that their own stream is live. Showing them the address and the opt-in
  // would offer a control that cannot change anything. The stored address is
  // left untouched, so demoting them restores it intact.
  notifySection.hidden = !!me.admin;
  if (me.admin) return;
  notifyToggle.checked = me.notify_live !== false;
  emailInput.value = me.email || "";
}

function wireSettings() {
  emailSave.addEventListener("click", async () => {
    const email = emailInput.value.trim();
    if (email && !email.includes("@")) return flash(emailSave, "Invalid");
    const ok = await saveProfile({ email });
    if (ok) me.email = email;
    flash(emailSave, ok ? "Saved" : "Error");
  });

  notifyToggle.addEventListener("change", async () => {
    const on = notifyToggle.checked;
    const ok = await saveProfile({ notify_live: on });
    if (ok) me.notify_live = on;
    else notifyToggle.checked = !on;   // revert if the save failed
  });

  nameSave.addEventListener("click", async () => {
    const next = nameInput.value.trim();
    if (!next) return flash(nameSave, "Empty");
    const ok = await saveProfile({ display_name: next });
    if (ok) me.name = next;
    flash(nameSave, ok ? "Saved" : "Error");
  });

  bioSave.addEventListener("click", async () => {
    me.bio = bioInput.value;
    const ok = await saveProfile({ bio: me.bio });
    flash(bioSave, ok ? "Saved" : "Error");
  });

  function showPwMsg(text, ok) {
    pwMsg.textContent = text;
    pwMsg.className = "pw-msg " + (ok ? "ok" : "bad");
  }

  pwSave.addEventListener("click", async () => {
    const next = pwNew.value;
    if (next.length < 8) return showPwMsg("Use at least 8 characters.", false);
    pwSave.disabled = true;
    try {
      const reply = await fetch("/api/password", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ current_password: pwCurrent.value, new_password: next }),
      });
      if (reply.ok) {
        pwCurrent.value = "";
        pwNew.value = "";
        showPwMsg("Password changed.", true);
      } else {
        const data = await reply.json().catch(() => ({}));
        showPwMsg(data.error || "Could not change password.", false);
      }
    } catch {
      showPwMsg("Could not change password.", false);
    } finally {
      pwSave.disabled = false;
    }
  });
}

// ---- avatar crop ----

function wireCrop() {
  const cropModal = document.getElementById("crop-modal");
  const cropCanvas = document.getElementById("crop-canvas");
  const cropZoom = document.getElementById("crop-zoom");
  const cropSave = document.getElementById("crop-save");
  const avatarButton = document.getElementById("avatar-button");
  const avatarInput = document.getElementById("avatar-input");
  const ctx = cropCanvas.getContext("2d");
  let img = null;
  let scaleBase = 1;
  let x = 0;
  let y = 0;

  function draw() {
    if (!img) return;
    const scale = scaleBase * parseFloat(cropZoom.value);
    const w = img.width * scale;
    const h = img.height * scale;
    x = Math.min(0, Math.max(CROP - w, x));
    y = Math.min(0, Math.max(CROP - h, y));
    ctx.clearRect(0, 0, CROP, CROP);
    ctx.drawImage(img, x, y, w, h);
  }

  avatarButton.addEventListener("click", () => avatarInput.click());
  avatarInput.addEventListener("change", () => {
    const file = avatarInput.files[0];
    if (!file) return;
    const url = URL.createObjectURL(file);
    img = new Image();
    img.onload = () => {
      URL.revokeObjectURL(url);
      scaleBase = Math.max(CROP / img.width, CROP / img.height);
      cropZoom.value = "1";
      x = (CROP - img.width * scaleBase) / 2;
      y = (CROP - img.height * scaleBase) / 2;
      draw();
      cropModal.hidden = false;
    };
    img.src = url;
    avatarInput.value = "";
  });

  cropZoom.addEventListener("input", draw);

  let dragging = false;
  let lastX = 0;
  let lastY = 0;
  cropCanvas.addEventListener("pointerdown", (e) => {
    dragging = true;
    lastX = e.clientX;
    lastY = e.clientY;
    cropCanvas.setPointerCapture(e.pointerId);
  });
  cropCanvas.addEventListener("pointermove", (e) => {
    if (!dragging) return;
    const rect = cropCanvas.getBoundingClientRect();
    x += (e.clientX - lastX) * (CROP / rect.width);
    y += (e.clientY - lastY) * (CROP / rect.height);
    lastX = e.clientX;
    lastY = e.clientY;
    draw();
  });
  cropCanvas.addEventListener("pointerup", () => { dragging = false; });

  cropSave.addEventListener("click", () => {
    cropCanvas.toBlob(async (blob) => {
      if (!blob) return;
      const form = new FormData();
      form.append("image", blob, "avatar.png");
      const reply = await fetch("/api/avatar", { method: "POST", body: form });
      if (reply.ok) {
        const data = await reply.json();
        me.avatar = data.avatar;
        renderMyAvatar();
        cropModal.hidden = true;
      } else {
        const data = await reply.json().catch(() => ({}));
        alert(data.error || "Could not update your avatar.");
      }
    }, "image/png");
  });
}

async function boot() {
  if (!(await requireAuth())) return;
  mountNav(me, { current: "options" });
  wireTheme();
  renderSettings();
  wireSettings();
  wireCrop();
}

boot();
