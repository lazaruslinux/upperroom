// The site header and the personal settings that hang off it.
//
// Every signed-in page shows the same nav in the same order, so this builds it
// in one place instead of each page hand-rolling its own. The settings modal
// comes with it: settings is a nav item on every page, so it has to work on
// every page, and duplicating this much markup and behaviour per page would be
// a maintenance trap. That makes this the one genuinely shared component in
// web/assets, a deliberate exception to the copy-per-page convention the small
// helpers here still follow.
//
// The watch page is not covered: its bar is icon-only and lives inside the chat
// column because the video needs the room.
//
// Usage, after the page has its own /api/me result:
//   mountNav(me, { current: "dashboard", onProfileChange, promptEmail: true })

(function () {
  const CROP = 256;
  const THEME_KEY = "selfstream_theme";
  const EMAIL_PROMPT_KEY = "selfstream_email_prompt_dismissed";

  // Nav items in fixed order. Every page renders all of them; `show` decides
  // who sees which, and the item matching `current` is marked rather than
  // dropped, so the bar never changes shape as you move around.
  const ITEMS = [
    { key: "home", label: "home", href: "/home" },
    { key: "dashboard", label: "dashboard", href: "/admin", show: (me) => me.admin },
    { key: "analytics", label: "analytics", href: "/analytics", show: (me) => me.admin },
    // An admin already has every moderator power and the dashboard is a
    // superset, so only a plain moderator needs this.
    { key: "mod", label: "mod", href: "/mod", show: (me) => me.mod && !me.admin },
    { key: "settings", label: "settings", action: "settings" },
    { key: "logout", label: "sign out", action: "logout" },
  ];

  let me = null;
  let opts = {};

  // ---- small helpers (private copies, same as every other page keeps) ----

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

  function openModal(m) { m.hidden = false; }
  function closeModal(m) { m.hidden = true; }

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

  // ---- markup ----

  function headerMarkup() {
    const links = ITEMS
      .filter((item) => !item.show || item.show(me))
      .map((item) => {
        const current = item.key === opts.current ? " is-current" : "";
        if (item.action) {
          return `<button type="button" class="ghost-btn${current}" data-nav="${item.action}">${item.label}</button>`;
        }
        return `<a href="${item.href}" class="ghost-btn${current}">${item.label}</a>`;
      })
      .join("\n      ");
    return `
    <div class="brand brand-column">
      <span id="site-title">upperroom</span>
      <span class="powered">powered by <span class="wordmark">upper<span class="wm-accent">room</span></span></span>
    </div>
    <nav class="home-actions">
      ${links}
    </nav>`;
  }

  const MODALS = `
  <div id="user-modal" class="modal" hidden>
    <div class="modal-card admin-modal">
      <button class="modal-close" type="button" data-close aria-label="Close">&times;</button>
      <h3>Your settings</h3>
      <div class="settings-rows">
        <div class="setting">
          <span>Theme</span>
          <button id="theme-toggle" type="button" class="pill">Dark</button>
        </div>
        <!-- Go-live email is a viewer setting. The server never emails admins
             that their own stream is live, so this whole block is hidden for
             them rather than offering a control that cannot do anything. -->
        <div id="notify-settings" class="setting-group">
          <div class="setting setting-field">
            <span>Go-live email</span>
            <div class="field-edit">
              <input id="email-input" type="email" placeholder="name@example.com" autocomplete="email">
              <button id="email-save" type="button" class="pill">Save</button>
            </div>
          </div>
          <div class="setting setting-check">
            <label class="switch">
              <input id="notify-toggle" type="checkbox">
              Receive an email when streams start
            </label>
          </div>
        </div>
        <div class="setting setting-field">
          <span>Display name</span>
          <div class="field-edit">
            <input id="name-input" type="text" maxlength="40" placeholder="Shown in chat" autocomplete="off">
            <button id="name-save" type="button" class="pill">Save</button>
          </div>
        </div>
        <div class="setting">
          <span>Avatar</span>
          <span class="avatar-edit">
            <span id="my-avatar"></span>
            <button id="avatar-button" type="button" class="pill">Change</button>
            <input id="avatar-input" type="file" accept="image/*" hidden>
          </span>
        </div>
        <div class="setting setting-field">
          <span>Bio</span>
          <div class="field-edit">
            <textarea id="bio-input" maxlength="200" rows="2" placeholder="A short bio others see when they tap your avatar"></textarea>
            <button id="bio-save" type="button" class="pill">Save</button>
          </div>
        </div>
        <div class="setting">
          <span>Password</span>
          <button id="pw-open" type="button" class="pill">Change password</button>
        </div>
      </div>
    </div>
  </div>

  <div id="password-modal" class="modal" hidden>
    <div class="modal-card">
      <button class="modal-close" type="button" data-close aria-label="Close">&times;</button>
      <h3>Change password</h3>
      <div class="pw-edit">
        <input id="pw-current" type="password" placeholder="Current password" autocomplete="current-password">
        <input id="pw-new" type="password" placeholder="New password (8+ characters)" autocomplete="new-password">
        <div class="pw-row">
          <button id="pw-save" type="button" class="pill primary">Change password</button>
          <span id="pw-msg" class="pw-msg"></span>
        </div>
      </div>
    </div>
  </div>

  <div id="email-modal" class="modal" hidden>
    <div class="modal-card">
      <h3>Get a heads-up when the stream goes live?</h3>
      <p class="muted">Add your email and we'll let you know when the channel goes live. You can change or remove it anytime in Settings.</p>
      <input id="email-prompt-input" type="email" placeholder="name@example.com" autocomplete="email">
      <p id="email-prompt-msg" class="pw-msg"></p>
      <label class="check-line"><input id="email-dont-show" type="checkbox" checked> Don't show this again</label>
      <div class="crop-actions">
        <button id="email-ignore" type="button" class="pill" data-close>Not now</button>
        <button id="email-prompt-save" type="button" class="pill primary">Save</button>
      </div>
    </div>
  </div>

  <div id="crop-modal" class="modal" hidden>
    <div class="modal-card crop-card">
      <div class="crop-stage">
        <canvas id="crop-canvas" width="256" height="256"></canvas>
        <div class="crop-ring"></div>
      </div>
      <input id="crop-zoom" type="range" min="1" max="3" step="0.01" value="1" aria-label="Zoom">
      <div class="crop-actions">
        <button type="button" class="pill" data-close>Cancel</button>
        <button id="crop-save" type="button" class="pill primary">Save</button>
      </div>
    </div>
  </div>`;

  // ---- wiring ----

  function wireTheme() {
    const themeToggle = document.getElementById("theme-toggle");
    function applyTheme(theme) {
      document.documentElement.dataset.theme = theme;
      try { localStorage.setItem(THEME_KEY, theme); } catch {}
      themeToggle.textContent = theme === "light" ? "Light" : "Dark";
    }
    let saved = "dark";
    try { saved = localStorage.getItem(THEME_KEY) || "dark"; } catch {}
    applyTheme(saved);
    themeToggle.addEventListener("click", () => {
      applyTheme(document.documentElement.dataset.theme === "light" ? "dark" : "light");
    });
  }

  function wireSettings() {
    const userModal = document.getElementById("user-modal");
    const passwordModal = document.getElementById("password-modal");
    const myAvatar = document.getElementById("my-avatar");
    const nameInput = document.getElementById("name-input");
    const nameSave = document.getElementById("name-save");
    const bioInput = document.getElementById("bio-input");
    const bioSave = document.getElementById("bio-save");
    const emailInput = document.getElementById("email-input");
    const emailSave = document.getElementById("email-save");
    const notifyToggle = document.getElementById("notify-toggle");
    const notifySettings = document.getElementById("notify-settings");
    const pwCurrent = document.getElementById("pw-current");
    const pwNew = document.getElementById("pw-new");
    const pwSave = document.getElementById("pw-save");
    const pwMsg = document.getElementById("pw-msg");

    function renderMyAvatar() {
      myAvatar.innerHTML = "";
      myAvatar.appendChild(avatarNode(me.username, me.name, me.avatar || 0, "avatar avatar-lg"));
    }

    function renderNotifySetting() {
      // The recipient query filters admins out, so the server has never emailed
      // a host that their own stream is live. Showing them the address and the
      // opt-in would offer a control that cannot change anything. The stored
      // address is left untouched, so demoting them restores it intact.
      notifySettings.hidden = !!me.admin;
      if (me.admin) return;
      notifyToggle.checked = me.notify_live !== false;
      emailInput.value = me.email || "";
    }

    document.querySelector('[data-nav="settings"]').addEventListener("click", () => {
      nameInput.value = me.name || "";
      bioInput.value = me.bio || "";
      renderMyAvatar();
      renderNotifySetting();
      openModal(userModal);
    });

    document.getElementById("pw-open").addEventListener("click", () => {
      pwCurrent.value = "";
      pwNew.value = "";
      pwMsg.textContent = "";
      pwMsg.className = "pw-msg";
      openModal(passwordModal);
      pwCurrent.focus();
    });

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
      if (ok) {
        me.name = next;
        if (opts.onProfileChange) opts.onProfileChange("name", next);
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
          setTimeout(() => closeModal(passwordModal), 1200);
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

    return { renderMyAvatar, renderNotifySetting };
  }

  function wireCrop(renderMyAvatar) {
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
        openModal(cropModal);
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
          if (opts.onProfileChange) opts.onProfileChange("avatar", data.avatar);
          closeModal(cropModal);
        } else {
          const data = await reply.json().catch(() => ({}));
          alert(data.error || "Could not update your avatar.");
        }
      }, "image/png");
    });
  }

  // The first-login nudge for viewers with no address on file. Only the page
  // that asks for it runs this, so it fires once at the landing page rather
  // than on every navigation.
  function wireEmailPrompt(renderNotifySetting) {
    const emailModal = document.getElementById("email-modal");
    const input = document.getElementById("email-prompt-input");
    const msg = document.getElementById("email-prompt-msg");

    function remember() {
      const dontShow = document.getElementById("email-dont-show");
      if (dontShow && dontShow.checked) {
        try { localStorage.setItem(EMAIL_PROMPT_KEY, "1"); } catch {}
      }
    }

    document.getElementById("email-ignore").addEventListener("click", remember);
    emailModal.addEventListener("click", (e) => {
      if (e.target === emailModal || e.target.hasAttribute("data-close")) remember();
    });
    document.addEventListener("keydown", (e) => {
      if (e.key === "Escape" && !emailModal.hidden) remember();
    });

    document.getElementById("email-prompt-save").addEventListener("click", async () => {
      const email = input.value.trim();
      if (!email || !email.includes("@")) {
        msg.textContent = "Enter a valid email, or choose Not now.";
        return;
      }
      const ok = await saveProfile({ email });
      if (ok) {
        me.email = email;
        try { localStorage.removeItem(EMAIL_PROMPT_KEY); } catch {}
        closeModal(emailModal);
        renderNotifySetting();        // keep the settings panel in sync
      } else {
        msg.textContent = "Could not save. Try again.";
      }
    });

    if (me.admin) return;                                 // the host runs the stream
    if (me.email) return;                                 // already has one
    try { if (localStorage.getItem(EMAIL_PROMPT_KEY)) return; } catch {}
    openModal(emailModal);
    input.focus();
  }

  function wireModalDismissal() {
    document.querySelectorAll(".modal").forEach((m) => {
      m.addEventListener("click", (e) => {
        if (e.target === m || e.target.hasAttribute("data-close")) closeModal(m);
      });
    });
    document.addEventListener("keydown", (e) => {
      if (e.key === "Escape") document.querySelectorAll(".modal:not([hidden])").forEach(closeModal);
    });
  }

  async function applySiteName() {
    // The operator's site name leads the bar and names the tab. Pages that
    // already know it can pass it in; the rest ask, cheaply.
    let name = opts.siteName;
    if (!name) {
      try { name = (await (await fetch("/api/status")).json()).site_name; } catch {}
    }
    if (!name) return;
    const el = document.getElementById("site-title");
    if (el) el.textContent = name;
    document.title = opts.current && opts.current !== "home"
      ? `${name} - ${opts.current}`
      : name;
  }

  window.mountNav = function (identity, options) {
    me = identity;
    opts = options || {};

    const host = document.getElementById("site-nav");
    if (!host) return;
    host.className = "home-bar";
    host.innerHTML = headerMarkup();

    const modals = document.createElement("div");
    modals.innerHTML = MODALS;
    while (modals.firstChild) document.body.appendChild(modals.firstChild);

    document.querySelector('[data-nav="logout"]').addEventListener("click", async () => {
      try { await fetch("/api/logout", { method: "POST" }); } catch {}
      window.location.href = "/";
    });

    wireTheme();
    const { renderMyAvatar, renderNotifySetting } = wireSettings();
    wireCrop(renderMyAvatar);
    wireModalDismissal();
    if (opts.promptEmail) wireEmailPrompt(renderNotifySetting);
    applySiteName();
  };
})();
