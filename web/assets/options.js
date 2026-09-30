// Options. Everything about your own account: how your chat lines look,
// whether this device is notified when the stream starts, your name, picture
// and bio, and your password.
//
// The chat style used to be a panel inside the room. It lives here now, with
// the rest of what belongs to you, so the room is only the room.

const CROP = 256;

let me = null;

function hueOf(seed) {
  let hash = 0;
  for (let i = 0; i < seed.length; i++) hash = (hash * 31 + seed.charCodeAt(i)) % 360;
  return hash;
}
function avatarColor(seed) { return `hsl(${hueOf(seed)}, 34%, 66%)`; }
function nameColor(seed) { return `hsl(${hueOf(seed)}, 45%, 76%)`; }

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

// ---- chat style: your font and your colors ----

// The chat fonts. The same keys the server allows and the room renders.
const FONTS = {
  system: "",
  jetbrains: "'JetBrains Mono', monospace",
  grotesk: "'Space Grotesk', sans-serif",
  plex: "'IBM Plex Sans', sans-serif",
  sora: "'Sora', sans-serif",
};
const FONT_LIST = [
  ["system", "Default"],
  ["jetbrains", "JetBrains Mono"],
  ["grotesk", "Space Grotesk"],
  ["plex", "IBM Plex Sans"],
  ["sora", "Sora"],
];
const DEFAULT_SWATCH = "#efece6";

const fontPicker = document.getElementById("font-picker");
const fontPreview = document.getElementById("font-preview");
const nameColorInput = document.getElementById("name-color");
const msgColorInput = document.getElementById("msg-color");
const colorMsg = document.getElementById("color-msg");

// A one-line mock of your own message, so the font and both colors can be
// seen together the way the room will see them.
function updateFontPreview() {
  fontPreview.textContent = "";
  const name = document.createElement("span");
  name.className = "name";
  name.textContent = me.name || me.username || "You";
  name.style.color = me.name_color || nameColor(me.username || "?");
  const body = document.createElement("span");
  body.className = "body";
  body.textContent = "This is how your messages look.";
  body.style.fontFamily = FONTS[me.font] || "";
  if (me.msg_color) body.style.color = me.msg_color;
  fontPreview.append(name, body);
}

// Each option is drawn in its own face, so it can be judged before it is
// picked. Saved on the server, since it rides on your messages for everyone.
function buildFontPicker() {
  fontPicker.textContent = "";
  FONT_LIST.forEach(([key, label]) => {
    const btn = document.createElement("button");
    btn.type = "button";
    btn.className = "font-option" + (me.font === key ? " selected" : "");
    btn.setAttribute("aria-pressed", me.font === key ? "true" : "false");
    btn.textContent = label;
    btn.style.fontFamily = FONTS[key] || "";
    btn.addEventListener("click", async () => {
      me.font = key;
      fontPicker.querySelectorAll(".font-option").forEach((b) => {
        b.classList.toggle("selected", b === btn);
        b.setAttribute("aria-pressed", b === btn ? "true" : "false");
      });
      updateFontPreview();
      await saveProfile({ font: key });
    });
    fontPicker.appendChild(btn);
  });
  updateFontPreview();
}

function showColorMsg(text, ok) {
  colorMsg.textContent = text || "";
  colorMsg.className = "pw-msg" + (text ? (ok ? " ok" : " bad") : "");
}

// Like saveProfile but keeps the server's words, so a rejected color can say
// why (too dark to read, too close to the lamp's red, malformed).
async function saveColor(patch) {
  try {
    const reply = await fetch("/api/profile", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(patch),
    });
    const data = await reply.json().catch(() => ({}));
    return { ok: reply.ok, error: data.error };
  } catch {
    return { ok: false, error: "Could not reach the server." };
  }
}

function wireColors() {
  nameColorInput.value = me.name_color || DEFAULT_SWATCH;
  msgColorInput.value = me.msg_color || DEFAULT_SWATCH;
  nameColorInput.addEventListener("change", async () => {
    const r = await saveColor({ name_color: nameColorInput.value });
    if (r.ok) {
      me.name_color = nameColorInput.value;
      showColorMsg("Name color saved.", true);
      updateFontPreview();
    } else showColorMsg(r.error || "That color was rejected.", false);
  });
  msgColorInput.addEventListener("change", async () => {
    const r = await saveColor({ msg_color: msgColorInput.value });
    if (r.ok) {
      me.msg_color = msgColorInput.value;
      showColorMsg("Text color saved.", true);
      updateFontPreview();
    } else showColorMsg(r.error || "That color was rejected.", false);
  });
  document.getElementById("color-reset").addEventListener("click", async () => {
    const r = await saveColor({ name_color: "", msg_color: "" });
    if (r.ok) {
      me.name_color = "";
      me.msg_color = "";
      nameColorInput.value = DEFAULT_SWATCH;
      msgColorInput.value = DEFAULT_SWATCH;
      showColorMsg("Colors are back to the default.", true);
      updateFontPreview();
    } else showColorMsg(r.error || "Could not reset.", false);
  });
}

// ---- profile, notifications, password ----

const myAvatar = document.getElementById("my-avatar");
const nameInput = document.getElementById("name-input");
const nameSave = document.getElementById("name-save");
const bioInput = document.getElementById("bio-input");
const bioSave = document.getElementById("bio-save");
const pushToggle = document.getElementById("push-toggle");
const pushStatus = document.getElementById("push-status");
const pwCurrent = document.getElementById("pw-current");
const pwNew = document.getElementById("pw-new");
const pwSave = document.getElementById("pw-save");
const pwMsg = document.getElementById("pw-msg");

function renderMyAvatar() {
  myAvatar.textContent = "";
  myAvatar.appendChild(avatarNode(me.username, me.name, me.avatar || 0, "avatar avatar-lg"));
  // The menu shows the same face, so a change here lands there too instead of
  // waiting for a reload to catch up.
  const inMenu = document.querySelector(".menu-who .avatar");
  if (inMenu) inMenu.replaceWith(avatarNode(me.username, me.name, me.avatar || 0, "avatar"));
}

function renderSettings() {
  nameInput.value = me.name || "";
  bioInput.value = me.bio || "";
  renderMyAvatar();
}

// The switch shows this device's state; it can only be used where push can
// work, and the line under it says why when it cannot.
function renderPush(state, line) {
  pushToggle.setAttribute("aria-checked", state === "on" ? "true" : "false");
  pushToggle.disabled = state !== "on" && state !== "off";
  pushStatus.textContent = line || pushNotify.explain(state);
}

function wirePush() {
  pushNotify.state().then((s) => renderPush(s));
  pushToggle.addEventListener("click", () => {
    const turningOn = pushToggle.getAttribute("aria-checked") !== "true";
    pushToggle.disabled = true;
    // turnOn runs first thing in the tap, so the browser may ask.
    const change = turningOn ? pushNotify.turnOn() : pushNotify.turnOff();
    change.then((s) => renderPush(s)).catch(async (e) => {
      const failed = turningOn ? "Could not turn it on here." : "Could not turn it off here.";
      renderPush(await pushNotify.state(), e && e.readable ? e.message : failed);
    });
  });
}

function wireSettings() {
  wirePush();

  nameSave.addEventListener("click", async () => {
    const next = nameInput.value.trim();
    if (!next) return flash(nameSave, "Empty");
    const ok = await saveProfile({ display_name: next });
    if (ok) {
      me.name = next;
      updateFontPreview();
      const inMenu = document.querySelector(".menu-who-name");
      if (inMenu) inMenu.textContent = next;
    }
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
  mountNav(me, { current: "options", pageName: "options" });
  buildFontPicker();
  wireColors();
  renderSettings();
  wireSettings();
  wireCrop();
}

boot();
