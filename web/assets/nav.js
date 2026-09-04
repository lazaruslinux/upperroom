// The site header shared by every signed-in page.
//
// One bar, built in one place, so the pages do not each hand-roll their own:
// brand and page links on the left, a search field in the middle, and the
// personal cluster (notifications, messages, points, you) on the right.
//
// Account settings used to hang off this file as a modal. They are a page now,
// /options, so all that lives in options.js instead. What is left here is the
// bar itself plus the first-login email nudge, which has to fire on the page
// people land on and nowhere else.
//
// The watch page is not covered: its bar is icon-only and lives inside the chat
// column because the video needs the room.
//
// Usage, after the page has its own /api/me result:
//   mountNav(me, { current: "browse", siteName, promptEmail: true })

(function () {
  const EMAIL_PROMPT_KEY = "selfstream_email_prompt_dismissed";
  const SEARCH_MIN = 2;
  const SEARCH_DEBOUNCE = 200;
  const SEARCH_LIMIT = 8;

  // The three destinations everyone has. Role pages are not here: they live in
  // the avatar menu, where an admin looks for them once and a viewer never has
  // to read past them.
  const LINKS = [
    { key: "home", label: "Home", href: "/home" },
    { key: "browse", label: "Browse", href: "/browse" },
    { key: "options", label: "Options", href: "/options" },
  ];

  let me = null;
  let opts = {};
  let openPanel = null;

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

  // ---- icons ----

  const ICON = {
    bell: `<svg viewBox="0 0 24 24" width="17" height="17" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M18 8A6 6 0 0 0 6 8c0 7-3 9-3 9h18s-3-2-3-9"></path><path d="M13.7 21a2 2 0 0 1-3.4 0"></path></svg>`,
    inbox: `<svg viewBox="0 0 24 24" width="17" height="17" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M3 5h18v14H3z"></path><polyline points="3 6 12 13 21 6"></polyline></svg>`,
    search: `<svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><circle cx="11" cy="11" r="7"></circle><line x1="21" y1="21" x2="16" y2="16"></line></svg>`,
    menu: `<svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><line x1="4" y1="7" x2="20" y2="7"></line><line x1="4" y1="12" x2="20" y2="12"></line><line x1="4" y1="17" x2="20" y2="17"></line></svg>`,
  };

  // ---- markup ----

  function barMarkup() {
    // A guest never reaches a page that mounts this bar; every one of them
    // redirects to /watch first. If one ever does, they get the way out and
    // nothing that would 401 on them.
    const links = me.guest ? "" : LINKS.map((item) => {
      const current = item.key === opts.current ? " is-current" : "";
      return `<a href="${item.href}" class="nav-link${current}">${item.label}</a>`;
    }).join("\n      ");

    const menuRows = [];
    if (!me.guest) {
      // The three bar links again, for the phone layout where the bar has no
      // room for them. Hidden by CSS on anything wider.
      LINKS.forEach((item) => {
        menuRows.push(`<a href="${item.href}" class="nav-menu-row nav-menu-narrow">${item.label}</a>`);
      });
      menuRows.push(`<a href="/options" class="nav-menu-row nav-menu-wide">Options</a>`);
      if (me.admin) {
        menuRows.push(`<a href="/admin" class="nav-menu-row">Dashboard</a>`);
        menuRows.push(`<a href="/analytics" class="nav-menu-row">Analytics</a>`);
      }
      // An admin already has every moderator power and the dashboard is a
      // superset, so only a plain moderator needs this.
      if (me.mod && !me.admin) menuRows.push(`<a href="/mod" class="nav-menu-row">Mod</a>`);
    }
    menuRows.push(`<button type="button" class="nav-menu-row" data-nav="logout">Sign out</button>`);

    const search = me.guest ? "" : `
    <div class="nav-search">
      <button type="button" class="icon-btn nav-search-toggle" aria-label="Search" aria-expanded="false">${ICON.search}</button>
      <div class="nav-search-field">
        <span class="nav-search-icon" aria-hidden="true">${ICON.search}</span>
        <input id="nav-search" type="search" maxlength="64" autocomplete="off"
               role="combobox" aria-expanded="false" aria-controls="nav-search-results"
               placeholder="Search broadcasts and clips" aria-label="Search broadcasts and clips">
      </div>
      <div id="nav-search-results" class="nav-pop nav-pop-search" role="listbox" hidden></div>
    </div>`;

    const points = me.guest ? "" : `
    <div class="nav-item">
      <button type="button" id="nav-points" class="points-chip nav-points" aria-expanded="false" aria-label="Your points" hidden>pts 0</button>
      <div class="nav-pop" hidden>
        <p class="nav-pop-title" id="nav-points-balance">pts 0</p>
        <p class="nav-pop-note">Points are earned by watching and chatting while the stream is live.</p>
      </div>
    </div>`;

    const bells = me.guest ? "" : `
    <div class="nav-item">
      <button type="button" class="icon-btn nav-icon" aria-label="Notifications" aria-expanded="false">${ICON.bell}</button>
      <div class="nav-pop" hidden><p class="nav-pop-note">No notifications yet.</p></div>
    </div>
    <div class="nav-item">
      <button type="button" class="icon-btn nav-icon" aria-label="Messages" aria-expanded="false">${ICON.inbox}</button>
      <div class="nav-pop" hidden><p class="nav-pop-note">Messages are coming soon.</p></div>
    </div>`;

    return `
    <a class="nav-brand" href="/home">
      <img class="nav-glyph" src="/assets/icons/icon.svg?v=1" alt="">
      <span id="site-title">upperroom</span>
    </a>
    <nav class="nav-links">
      ${links}
    </nav>
    ${search}
    <div class="nav-right">
      ${bells}
      ${points}
      <div class="nav-item nav-account">
        <button type="button" id="nav-avatar" class="nav-avatar-btn" aria-label="Your account" aria-expanded="false"><span class="nav-menu-glyph" aria-hidden="true">${ICON.menu}</span></button>
        <div class="nav-pop nav-pop-menu" hidden>
          <div class="nav-menu-who"></div>
          ${menuRows.join("\n          ")}
        </div>
      </div>
    </div>`;
  }

  // The one modal the bar still owns: the first-login nudge for viewers with no
  // address on file. Only the page that asks for it runs this, so it fires once
  // at the landing page rather than on every navigation.
  const EMAIL_MODAL = `
  <div id="email-modal" class="modal" hidden>
    <div class="modal-card">
      <h3>Get a heads-up when the stream goes live?</h3>
      <p class="muted">Add your email and we'll let you know when the channel goes live. You can change or remove it anytime on the options page.</p>
      <input id="email-prompt-input" type="email" placeholder="name@example.com" autocomplete="email">
      <p id="email-prompt-msg" class="pw-msg"></p>
      <label class="check-line"><input id="email-dont-show" type="checkbox" checked> Don't show this again</label>
      <div class="crop-actions">
        <button id="email-ignore" type="button" class="pill" data-close>Not now</button>
        <button id="email-prompt-save" type="button" class="pill primary">Save</button>
      </div>
    </div>
  </div>`;

  // ---- popover primitive ----
  // Every popover in the bar is a trigger button followed by a hidden panel.
  // One open at a time, closed by Escape or a click anywhere else.

  function closePanel() {
    if (!openPanel) return;
    openPanel.panel.hidden = true;
    openPanel.trigger.setAttribute("aria-expanded", "false");
    openPanel = null;
  }

  function showPanel(trigger, panel) {
    closePanel();
    panel.hidden = false;
    trigger.setAttribute("aria-expanded", "true");
    openPanel = { trigger, panel };
  }

  function wirePopover(trigger, panel, onOpen) {
    trigger.addEventListener("click", (e) => {
      e.stopPropagation();
      if (openPanel && openPanel.panel === panel) return closePanel();
      showPanel(trigger, panel);
      if (onOpen) onOpen();
    });
    panel.addEventListener("click", (e) => e.stopPropagation());
  }

  function wireDismissal() {
    document.addEventListener("click", closePanel);
    document.addEventListener("keydown", (e) => {
      if (e.key === "Escape") closePanel();
    });
  }

  // ---- search ----
  // Titles only, matched in the browser. The listings are small and already
  // fetched wholesale by the browse page, so a search endpoint would be a
  // backend for something the client can do without one.

  function wireSearch(host) {
    const wrap = host.querySelector(".nav-search");
    if (!wrap) return;
    const input = host.querySelector("#nav-search");
    const results = host.querySelector("#nav-search-results");
    const toggle = host.querySelector(".nav-search-toggle");
    let items = null;
    let loading = null;
    let timer = null;

    function load() {
      if (items) return Promise.resolve(items);
      if (loading) return loading;
      loading = Promise.all([
        fetch("/api/vods").then((r) => (r.ok ? r.json() : { vods: [] })).catch(() => ({ vods: [] })),
        fetch("/api/clips").then((r) => (r.ok ? r.json() : { clips: [] })).catch(() => ({ clips: [] })),
      ]).then(([v, c]) => {
        items = []
          .concat((v.vods || []).map((x) => ({ id: x.id, kind: "vod", title: x.title || "" })))
          .concat((c.clips || []).map((x) => ({ id: x.id, kind: "clip", title: x.name || "" })));
        return items;
      });
      return loading;
    }

    function render(list) {
      results.innerHTML = "";
      if (!list.length) {
        const empty = document.createElement("p");
        empty.className = "nav-pop-note";
        empty.textContent = "Nothing matches that.";
        results.appendChild(empty);
        return;
      }
      list.forEach((item) => {
        const row = document.createElement("a");
        row.className = "nav-result";
        row.setAttribute("role", "option");
        row.href = `/media?type=${item.kind}&id=${item.id}`;
        const title = document.createElement("span");
        title.className = "nav-result-title";
        title.textContent = item.title || "(untitled)";
        const tag = document.createElement("span");
        tag.className = "nav-result-tag";
        tag.textContent = item.kind;
        row.append(title, tag);
        results.appendChild(row);
      });
    }

    async function search() {
      const q = input.value.trim().toLowerCase();
      if (q.length < SEARCH_MIN) return closePanel();
      const all = await load();
      const hits = all.filter((x) => x.title.toLowerCase().includes(q)).slice(0, SEARCH_LIMIT);
      render(hits);
      showPanel(input, results);
    }

    input.addEventListener("focus", load, { once: true });
    input.addEventListener("click", (e) => e.stopPropagation());
    input.addEventListener("input", () => {
      clearTimeout(timer);
      timer = setTimeout(search, SEARCH_DEBOUNCE);
    });
    input.addEventListener("keydown", (e) => {
      if (e.key === "Enter") {
        e.preventDefault();
        const first = results.querySelector(".nav-result");
        if (first) window.location.href = first.href;
      }
      if (e.key === "Escape") {
        closePanel();
        input.blur();
      }
    });
    results.addEventListener("click", (e) => e.stopPropagation());

    // On a phone the field is collapsed to its magnifier until asked for.
    toggle.addEventListener("click", (e) => {
      e.stopPropagation();
      const open = wrap.classList.toggle("is-open");
      toggle.setAttribute("aria-expanded", open ? "true" : "false");
      if (open) input.focus();
      else closePanel();
    });
  }

  // ---- points ----

  async function wirePoints(host) {
    const chip = host.querySelector("#nav-points");
    if (!chip) return;
    const panel = chip.parentElement.querySelector(".nav-pop");
    const balance = host.querySelector("#nav-points-balance");
    let data = null;
    try {
      const reply = await fetch("/api/points");
      if (!reply.ok) return;                // guests and signed-out: no balance
      data = await reply.json();
    } catch {
      return;
    }
    const text = `pts ${data.points}`;
    chip.textContent = text;
    balance.textContent = text;
    chip.hidden = false;
    wirePopover(chip, panel);
  }

  // ---- email nudge ----

  function wireEmailPrompt() {
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

  // Click the backdrop or press Escape to close any modal on the page. Shared
  // because every page that mounts the bar can carry one (the email nudge here,
  // the release notice on home) and none of them should hand-roll this.
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
    host.className = "site-bar";
    host.innerHTML = barMarkup();

    const holder = document.createElement("div");
    holder.innerHTML = EMAIL_MODAL;
    while (holder.firstChild) document.body.appendChild(holder.firstChild);

    // The avatar leads and the menu glyph follows it, so the pill reads as a
    // button rather than as a picture of you.
    const avatarBtn = host.querySelector("#nav-avatar");
    avatarBtn.prepend(avatarNode(me.username, me.name, me.avatar || 0, "avatar"));
    // The first row of the menu says who you are. Set as text: it is a name the
    // person typed.
    const who = host.querySelector(".nav-menu-who");
    if (who) who.textContent = me.name || me.username;
    wirePopover(avatarBtn, avatarBtn.parentElement.querySelector(".nav-pop"));

    host.querySelectorAll(".nav-icon").forEach((btn) => {
      wirePopover(btn, btn.parentElement.querySelector(".nav-pop"));
    });

    host.querySelector('[data-nav="logout"]').addEventListener("click", async () => {
      try { await fetch("/api/logout", { method: "POST" }); } catch {}
      window.location.href = "/";
    });

    wireSearch(host);
    wirePoints(host);
    wireDismissal();
    wireModalDismissal();
    if (opts.promptEmail) wireEmailPrompt();
    applySiteName();
  };
})();
