// Home page. The card-style landing shown right after sign in. It confirms the
// viewer is logged in, shows whether the stream is live with a real preview
// thumbnail, sends them into the player when they tap the card, and holds the
// per-account settings (avatar, bio, password) in its own menu.

const greeting = document.getElementById("greeting");
const adminLink = document.getElementById("admin-link");
const logoutButton = document.getElementById("logout");
const card = document.getElementById("stream-card");
const cardAvatar = document.getElementById("card-avatar");
const thumb = document.getElementById("thumb");
const thumbFallback = document.getElementById("thumb-fallback");
const liveBadge = document.getElementById("live-badge");
const statusPill = document.getElementById("status-pill");

let me = null;
let online = false;

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
  me = data;
  return true;
}

function setupIdentity() {
  const name = (me.name || me.username || "there").split(" ")[0];
  greeting.textContent = `Welcome back, ${name}.`;
  if (me.admin) adminLink.hidden = false;
  // The channel badge on the card uses the signed in person's own avatar.
  cardAvatar.replaceWith(avatarNode(me.username, me.name, me.avatar || 0, "card-avatar"));
}

// ---- live status + thumbnail ----

function showLive(isLive, watching) {
  online = isLive;
  liveBadge.hidden = !isLive;          // LIVE badge only when actually live
  card.classList.toggle("is-live", isLive);

  // One status pill, never two: "N watching" while live, "N in chat" while
  // offline (people can hang out in chat between streams). Hidden when offline
  // and nobody is around, so an empty offline card stays clean.
  const count = typeof watching === "number" ? watching : 0;
  if (isLive) {
    statusPill.hidden = false;
    statusPill.textContent = count === 1 ? "1 watching" : `${count} watching`;
  } else if (count > 0) {
    statusPill.hidden = false;
    statusPill.textContent = count === 1 ? "1 in chat" : `${count} in chat`;
  } else {
    statusPill.hidden = true;
  }

  if (!isLive) {
    thumb.hidden = true;
    thumbFallback.hidden = false;
  }
}

function refreshThumb() {
  if (!online) return;
  // Cache-bust so each refresh pulls the freshest captured frame.
  const next = new Image();
  next.onload = () => {
    thumb.src = next.src;
    thumb.hidden = false;
    thumbFallback.hidden = true;
  };
  next.onerror = () => {
    // No frame yet (stream just came up) — keep showing the branded fallback.
    thumb.hidden = true;
    thumbFallback.hidden = false;
  };
  next.src = `/api/thumbnail?t=${Date.now()}`;
}

async function refreshStatus() {
  let data = { online: false };
  try {
    data = await (await fetch("/api/status")).json();
  } catch {
    /* treat a failed poll as offline */
  }
  const wasOnline = online;
  showLive(!!data.online, data.watching);
  if (data.online && !wasOnline) refreshThumb();
}

card.addEventListener("click", () => {
  window.location.href = "/watch";
});

logoutButton.addEventListener("click", async () => {
  try { await fetch("/api/logout", { method: "POST" }); } catch {}
  window.location.href = "/";
});

// ---- your settings (avatar, bio, password) ----

const userModal = document.getElementById("user-modal");
const myAvatar = document.getElementById("my-avatar");
const bioInput = document.getElementById("bio-input");
const bioSave = document.getElementById("bio-save");
const avatarButton = document.getElementById("avatar-button");
const avatarInput = document.getElementById("avatar-input");
const pwCurrent = document.getElementById("pw-current");
const pwNew = document.getElementById("pw-new");
const pwSave = document.getElementById("pw-save");
const pwMsg = document.getElementById("pw-msg");

function renderMyAvatar() {
  myAvatar.innerHTML = "";
  myAvatar.appendChild(avatarNode(me.username, me.name, me.avatar || 0, "avatar avatar-lg"));
}

document.getElementById("settings-btn").addEventListener("click", () => {
  bioInput.value = me.bio || "";
  pwCurrent.value = "";
  pwNew.value = "";
  pwMsg.textContent = "";
  renderMyAvatar();
  openModal(userModal);
});

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

bioSave.addEventListener("click", async () => {
  me.bio = bioInput.value;
  const ok = await saveProfile({ bio: me.bio });
  bioSave.textContent = ok ? "Saved" : "Error";
  setTimeout(() => { bioSave.textContent = "Save"; }, 1500);
});

function showPwMsg(text, ok) {
  pwMsg.textContent = text;
  pwMsg.className = "pw-msg " + (ok ? "ok" : "bad");
}

pwSave.addEventListener("click", async () => {
  const current = pwCurrent.value;
  const next = pwNew.value;
  if (next.length < 8) { showPwMsg("Use at least 8 characters.", false); return; }
  pwSave.disabled = true;
  try {
    const reply = await fetch("/api/password", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ current_password: current, new_password: next }),
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

// ---- avatar crop (drag + zoom) ----

const cropModal = document.getElementById("crop-modal");
const cropCanvas = document.getElementById("crop-canvas");
const cropZoom = document.getElementById("crop-zoom");
const cropSave = document.getElementById("crop-save");
const cropCtx = cropCanvas.getContext("2d");
const CROP = 256;
let cropImg = null;
let cropScaleBase = 1;
let cropX = 0;
let cropY = 0;

function drawCrop() {
  if (!cropImg) return;
  const scale = cropScaleBase * parseFloat(cropZoom.value);
  const w = cropImg.width * scale;
  const h = cropImg.height * scale;
  cropX = Math.min(0, Math.max(CROP - w, cropX));
  cropY = Math.min(0, Math.max(CROP - h, cropY));
  cropCtx.clearRect(0, 0, CROP, CROP);
  cropCtx.drawImage(cropImg, cropX, cropY, w, h);
}

avatarButton.addEventListener("click", () => avatarInput.click());
avatarInput.addEventListener("change", () => {
  const file = avatarInput.files[0];
  if (!file) return;
  const url = URL.createObjectURL(file);
  cropImg = new Image();
  cropImg.onload = () => {
    URL.revokeObjectURL(url);
    cropScaleBase = Math.max(CROP / cropImg.width, CROP / cropImg.height);
    cropZoom.value = "1";
    cropX = (CROP - cropImg.width * cropScaleBase) / 2;
    cropY = (CROP - cropImg.height * cropScaleBase) / 2;
    drawCrop();
    openModal(cropModal);
  };
  cropImg.src = url;
  avatarInput.value = "";
});

cropZoom.addEventListener("input", drawCrop);

let cropDragging = false;
let cropLastX = 0;
let cropLastY = 0;
cropCanvas.addEventListener("pointerdown", (e) => {
  cropDragging = true;
  cropLastX = e.clientX;
  cropLastY = e.clientY;
  cropCanvas.setPointerCapture(e.pointerId);
});
cropCanvas.addEventListener("pointermove", (e) => {
  if (!cropDragging) return;
  const rect = cropCanvas.getBoundingClientRect();
  cropX += (e.clientX - cropLastX) * (CROP / rect.width);
  cropY += (e.clientY - cropLastY) * (CROP / rect.height);
  cropLastX = e.clientX;
  cropLastY = e.clientY;
  drawCrop();
});
cropCanvas.addEventListener("pointerup", () => { cropDragging = false; });

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
      // Refresh the card badge too, so the new avatar shows on the lobby.
      document.querySelector(".card-avatar")
        .replaceWith(avatarNode(me.username, me.name, me.avatar, "card-avatar"));
      closeModal(cropModal);
    } else {
      const data = await reply.json().catch(() => ({}));
      alert(data.error || "Could not update your avatar.");
    }
  }, "image/png");
});

// ---- modal helpers ----

function openModal(m) { m.hidden = false; }
function closeModal(m) { m.hidden = true; }
document.querySelectorAll(".modal").forEach((m) => {
  m.addEventListener("click", (e) => {
    if (e.target === m || e.target.hasAttribute("data-close")) closeModal(m);
  });
});
document.addEventListener("keydown", (e) => {
  if (e.key === "Escape") document.querySelectorAll(".modal:not([hidden])").forEach(closeModal);
});

async function boot() {
  if (!(await requireAuth())) return;
  setupIdentity();
  await refreshStatus();
  refreshThumb();
  setInterval(refreshStatus, 10000);
  setInterval(refreshThumb, 15000);
}

boot();
