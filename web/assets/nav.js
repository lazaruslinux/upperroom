// The lamp strip: the top edge of every page.
//
// One strip, built in one place: the menu key and the site name on the left,
// the ON AIR lamp and the on-air clock on the right (the centre, on a wide
// screen). The lamp is lit only while the stream is live, and it is the only
// thing on the site in that red.
//
// The menu is the way around a signed-in site: the room, past broadcasts,
// options, the role pages, and signing out. Search lives on the browse page.
//
// This file also owns the one notice that belongs to whichever page a person
// lands on (the one-time "what changed" note), because the room and home both
// are that page, depending on whether anyone is on air.
//
// Usage, once the page has its own /api/me answer:
//   const strip = mountNav(me, { current: "browse" });
//   strip.setStatus(statusFromApi);     // pages that poll /api/status themselves
// Pages with no session (sign-in) use mountStrip({}) instead,
// which draws the strip without a menu.
//
// The dashboard also hands over its sections, [{ key, label, href }], and the
// one it is showing as `section`. They sit on the right of the strip on a wide
// screen and at the top of the menu on anything narrower; strip.setSection()
// moves the mark when the dashboard switches section without a page load.

(function () {
  const ACCENTS = ["green", "amber", "blue", "ghost"];
  const POLL_MS = 15000;

  // Authored icons, one stroke weight (see svg.i in style.css). Static
  // markup with nobody's input in it.
  const ICON = {
    menu: '<path d="M4 7h16M4 12h16M4 17h16"/>',
    room: '<rect x="3" y="5" width="18" height="12" rx="1"/><path d="M8 21h8M12 17v4"/>',
    library: '<rect x="3" y="4" width="18" height="16" rx="1"/><path d="M3 9h18M8 4v5M16 4v5"/>',
    options: '<path d="M4 7h10M18 7h2M4 17h4M12 17h8"/><circle cx="16" cy="7" r="2"/><circle cx="10" cy="17" r="2"/>',
    dashboard: '<rect x="3" y="3" width="8" height="10" rx="1"/><rect x="13" y="3" width="8" height="6" rx="1"/><rect x="13" y="11" width="8" height="10" rx="1"/><rect x="3" y="15" width="8" height="6" rx="1"/>',
    mod: '<path d="M12 3 5 6v5c0 4.5 3 8.4 7 10 4-1.6 7-5.5 7-10V6l-7-3Z"/>',
    stats: '<path d="M4 20V10M10 20V4M16 20v-7M22 20H2"/>',
    signout: '<path d="M15 4h4a1 1 0 0 1 1 1v14a1 1 0 0 1-1 1h-4M10 17l5-5-5-5M15 12H4"/>',
  };

  function icon(name, extra) {
    return `<svg class="i" viewBox="0 0 24 24" aria-hidden="true"${extra || ""}>${ICON[name]}</svg>`;
  }

  // ---- small helpers, kept private like every other page keeps its own ----

  function avatarColor(seed) {
    let hash = 0;
    for (let i = 0; i < seed.length; i++) hash = (hash * 31 + seed.charCodeAt(i)) % 360;
    return `hsl(${hash}, 34%, 66%)`;
  }

  function avatarNode(username, name, version) {
    if (version) {
      const img = document.createElement("img");
      img.className = "avatar";
      img.alt = "";
      img.src = `/api/avatar/${encodeURIComponent(username)}?v=${version}`;
      return img;
    }
    const span = document.createElement("span");
    span.className = "avatar";
    span.textContent = (name || username || "?").trim().charAt(0).toUpperCase();
    span.style.background = avatarColor(username || "?");
    return span;
  }

  // The accent rides every status answer; remember it for head.js's next
  // first paint.
  function applyAccent(value) {
    if (!ACCENTS.includes(value)) return;
    if (document.documentElement.dataset.accent !== value) {
      document.documentElement.dataset.accent = value;
    }
    try { localStorage.setItem("selfstream_accent", value); } catch (e) {}
  }

  function onAirClock(seconds) {
    const s = Math.max(0, Math.floor(seconds));
    const h = Math.floor(s / 3600);
    const m = Math.floor((s % 3600) / 60);
    const pad = (n) => String(n).padStart(2, "0");
    return h > 0 ? `${h}:${pad(m)}:${pad(s % 60)}` : `${m}:${pad(s % 60)}`;
  }

  // ---- the strip ----

  function buildStrip(opts) {
    const host = document.getElementById("site-nav");
    if (!host) return null;
    const strip = document.createElement("header");
    strip.className = "strip";
    strip.id = "strip";
    strip.innerHTML = `
      <div class="strip-left"></div>
      <div class="strip-on">
        <p class="lamp" id="lamp" role="status">Off air</p>
        <span class="clock" id="clock" hidden></span>
      </div>
      <div class="strip-end"></div>`;
    const left = strip.querySelector(".strip-left");
    const name = document.createElement(opts.homeLink ? "a" : "span");
    name.className = "strip-name";
    name.id = "site-title";
    name.textContent = opts.siteName || "upperroom";
    if (opts.homeLink) name.href = "/home";
    left.appendChild(name);
    host.replaceWith(strip);
    return strip;
  }

  function controller(strip, opts) {
    const lamp = strip.querySelector("#lamp");
    const clock = strip.querySelector("#clock");
    let since = null;
    let ticker = null;
    let last = null;
    const listeners = [];
    if (opts.onStatus) listeners.push(opts.onStatus);

    function tick() {
      if (since) clock.textContent = onAirClock(Date.now() / 1000 - since);
    }

    function setStatus(data) {
      if (!data) return;
      last = data;
      applyAccent(data.accent);
      if (data.site_name) {
        const title = strip.querySelector("#site-title");
        if (title && title.textContent !== data.site_name) title.textContent = data.site_name;
        const page = opts.pageName ? `${data.site_name} - ${opts.pageName}` : data.site_name;
        if (document.title !== page) document.title = page;
      }
      const live = !!data.online;
      lamp.classList.toggle("on", live);
      lamp.textContent = live ? "On air" : "Off air";
      since = live ? (data.since || Math.floor(Date.now() / 1000)) : null;
      clock.hidden = !live;
      if (live && !ticker) ticker = setInterval(tick, 1000);
      if (!live && ticker) { clearInterval(ticker); ticker = null; }
      tick();
      listeners.forEach((fn) => { try { fn(data); } catch (e) { /* a page's own bug */ } });
    }

    async function poll() {
      try {
        const reply = await fetch("/api/status");
        if (reply.ok) setStatus(await reply.json());
      } catch {
        /* keep what the lamp last said; the next poll tries again */
      }
    }

    if (opts.poll !== false) {
      poll();
      setInterval(poll, POLL_MS);
    }

    return {
      setStatus,
      poll,
      onStatus(fn) { listeners.push(fn); if (last) fn(last); },
      get status() { return last; },
    };
  }

  // ---- the menu ----

  function menuItems(me, opts) {
    const items = [
      { key: "watch", label: "The room", href: "/watch", icon: "room" },
      { key: "browse", label: "Past broadcasts", href: "/browse", icon: "library" },
      { key: "options", label: "Options", href: "/options", icon: "options" },
    ];
    const roles = [];
    // A page that carries the dashboard's own sections already offers both of
    // these, as Go live and Stats.
    if (me.admin && !opts.sections) {
      roles.push({ key: "dashboard", label: "Dashboard", href: "/admin", icon: "dashboard" });
      roles.push({ key: "analytics", label: "Stats", href: "/analytics", icon: "stats" });
    }
    // An admin already has every moderator power, and the dashboard is a
    // superset of the moderation page, so only a plain moderator needs it.
    if (me.mod && !me.admin) {
      roles.push({ key: "mod", label: "Moderation", href: "/mod", icon: "mod" });
    }
    if (roles.length) items.push("rule", ...roles);
    // On a narrow screen the strip has no room for the dashboard's sections, so
    // they lead the menu instead. On a wide one the CSS hides these rows,
    // because the strip is already showing them.
    if (opts.sections) {
      const sections = opts.sections.map((sec) => ({
        key: `section:${sec.key}`, label: sec.label, href: sec.href, section: true,
      }));
      items.unshift(...sections, "section-rule");
    }
    items.push("rule", { key: "logout", label: "Sign out", icon: "signout" });
    return items;
  }

  function buildMenu(strip, me, opts) {
    const left = strip.querySelector(".strip-left");
    const button = document.createElement("button");
    button.type = "button";
    button.className = "icon-btn";
    button.id = "menu-btn";
    button.setAttribute("aria-label", "Menu");
    button.setAttribute("aria-haspopup", "true");
    button.setAttribute("aria-expanded", "false");
    button.setAttribute("aria-controls", "menu");
    button.innerHTML = icon("menu");
    left.prepend(button);

    const menu = document.createElement("nav");
    menu.className = "menu";
    menu.id = "menu";
    menu.hidden = true;
    menu.setAttribute("aria-label", "Menu");

    const who = document.createElement("div");
    who.className = "menu-who";
    who.appendChild(avatarNode(me.username, me.name, me.avatar || 0));
    const whoText = document.createElement("span");
    const whoName = document.createElement("span");
    whoName.className = "menu-who-name";
    whoName.textContent = me.name || me.username;       // a name somebody typed
    const whoSub = document.createElement("span");
    whoSub.className = "menu-who-sub";
    whoSub.textContent = me.admin ? "Host" : me.mod ? "Moderator" : "";
    whoText.append(whoName, whoSub);
    who.appendChild(whoText);
    menu.appendChild(who);

    menuItems(me, opts).forEach((item) => {
      if (item === "rule" || item === "section-rule") {
        const rule = document.createElement("hr");
        rule.className = item === "rule" ? "menu-rule" : "menu-rule menu-section";
        menu.appendChild(rule);
        return;
      }
      const row = document.createElement(item.href ? "a" : "button");
      row.className = item.section ? "menu-row menu-section" : "menu-row";
      if (item.section) row.dataset.section = item.key.slice(8);
      if (item.href) row.href = item.href;
      else row.type = "button";
      if (item.key === opts.current || (item.section && item.key === `section:${opts.section}`)) {
        row.setAttribute("aria-current", "page");
      }
      // The dashboard's sections are one group under the host's name, so they
      // carry no icon each; the rows below them keep theirs.
      if (!item.section) row.innerHTML = icon(item.icon);
      const label = document.createElement("span");
      label.textContent = item.label;
      row.appendChild(label);
      if (item.key === "logout") {
        row.addEventListener("click", async () => {
          try { await fetch("/api/logout", { method: "POST" }); } catch {}
          window.location.href = "/";
        });
      }
      // A section on the same page only changes the hash, so nothing reloads
      // and the menu would otherwise stay open over the section it opened.
      if (item.section) row.addEventListener("click", () => close(false));
      menu.appendChild(row);
    });
    document.body.appendChild(menu);

    // Only the rows on screen: the dashboard's section rows are hidden on a
    // wide screen, and focus cannot land on something that is not drawn.
    const rows = () => Array.from(menu.querySelectorAll(".menu-row"))
      .filter((row) => row.offsetParent !== null);
    let pointsAsked = false;

    function open() {
      menu.hidden = false;
      button.setAttribute("aria-expanded", "true");
      const current = rows().find((row) => row.getAttribute("aria-current") === "page") || rows()[0];
      if (current) current.focus();
      // The points balance is only worth a request once somebody looks.
      if (!pointsAsked) {
        pointsAsked = true;
        fetch("/api/points")
          .then((r) => (r.ok ? r.json() : null))
          .then((data) => {
            if (!data) return;
            const pts = `${data.points} ${data.points === 1 ? "point" : "points"}`;
            whoSub.textContent = whoSub.textContent ? `${whoSub.textContent} · ${pts}` : pts;
          })
          .catch(() => {});
      }
    }

    function close(returnFocus) {
      if (menu.hidden) return;
      menu.hidden = true;
      button.setAttribute("aria-expanded", "false");
      if (returnFocus) button.focus();
    }

    button.addEventListener("click", (e) => {
      e.stopPropagation();
      if (menu.hidden) open();
      else close(false);
    });
    menu.addEventListener("click", (e) => e.stopPropagation());
    document.addEventListener("click", () => close(false));
    menu.addEventListener("keydown", (e) => {
      const list = rows();
      const at = list.indexOf(document.activeElement);
      if (e.key === "Escape") { e.preventDefault(); close(true); }
      else if (e.key === "ArrowDown") { e.preventDefault(); list[(at + 1) % list.length].focus(); }
      else if (e.key === "ArrowUp") { e.preventDefault(); list[(at - 1 + list.length) % list.length].focus(); }
      else if (e.key === "Home") { e.preventDefault(); list[0].focus(); }
      else if (e.key === "End") { e.preventDefault(); list[list.length - 1].focus(); }
    });
    // Tabbing out of the menu closes it, rather than leaving it open behind.
    menu.addEventListener("focusout", (e) => {
      if (e.relatedTarget && !menu.contains(e.relatedTarget) && e.relatedTarget !== button) close(false);
    });
  }

  // The dashboard's sections, along the right of the strip on a wide screen.
  function buildSections(strip, opts) {
    const nav = document.createElement("nav");
    nav.className = "strip-sections";
    nav.setAttribute("aria-label", "Dashboard");
    opts.sections.forEach((sec) => {
      const link = document.createElement("a");
      link.className = "strip-section";
      link.href = sec.href;
      link.dataset.section = sec.key;
      link.textContent = sec.label;
      if (sec.key === opts.section) link.setAttribute("aria-current", "page");
      nav.appendChild(link);
    });
    strip.classList.add("has-sections");
    strip.querySelector(".strip-end").appendChild(nav);
  }

  function setSection(key) {
    document.querySelectorAll("[data-section]").forEach((el) => {
      if (!el.matches(".strip-section, .menu-section")) return;
      if (el.dataset.section === key) el.setAttribute("aria-current", "page");
      else el.removeAttribute("aria-current");
    });
  }

  // ---- the notices that belong to the landing page ----

  // Shown once, and only for the release actually running. Acknowledging it is
  // what marks it read, so closing the tab instead leaves it for next time.
  function showWhatsNew(info) {
    if (!info || !info.notes || !info.notes.length) return;
    const modal = document.createElement("div");
    modal.className = "modal";
    modal.id = "whats-new";
    modal.innerHTML = `
      <div class="modal-card" role="dialog" aria-modal="true" aria-labelledby="whats-new-title">
        <h3 id="whats-new-title"></h3>
        <ul class="whats-new-list" id="whats-new-list"></ul>
        <div class="modal-actions">
          <button type="button" id="whats-new-ok" class="btn primary">Got it</button>
        </div>
      </div>`;
    modal.querySelector("#whats-new-title").textContent =
      `upperroom has been updated to v${info.version}`;
    const list = modal.querySelector("#whats-new-list");
    info.notes.forEach((note) => {
      const item = document.createElement("li");
      item.textContent = note;
      list.appendChild(item);
    });
    document.body.appendChild(modal);
    const ok = modal.querySelector("#whats-new-ok");
    ok.addEventListener("click", async () => {
      modal.remove();
      // Best effort: a failed acknowledgement means it is offered again, the
      // harmless direction to fail in.
      try { await fetch("/api/whats-new/seen", { method: "POST" }); } catch (e) {}
    });
    // Dismissing it any other way puts it off without marking it read.
    modal.addEventListener("click", (e) => { if (e.target === modal) modal.remove(); });
    modal.addEventListener("keydown", (e) => { if (e.key === "Escape") modal.remove(); });
    ok.focus();
  }

  // Click the dim room around a modal, a [data-close] control inside it, or
  // Escape, to close any modal on the page. Shared because every page that
  // mounts the strip can carry one (the dashboard's people panel, the crop
  // stage on options) and none of them should hand-roll it.
  function wireModalDismissal() {
    document.querySelectorAll(".modal").forEach((m) => {
      m.addEventListener("click", (e) => {
        if (e.target === m || e.target.closest("[data-close]")) m.hidden = true;
      });
    });
    document.addEventListener("keydown", (e) => {
      if (e.key === "Escape") {
        document.querySelectorAll(".modal:not([hidden])").forEach((m) => { m.hidden = true; });
      }
    });
  }

  // ---- entry points ----

  // A signed-in page: the strip with its menu.
  window.mountNav = function (me, options) {
    const opts = options || {};
    const framed = window.top !== window.self;
    const strip = buildStrip({
      siteName: opts.siteName,
      // The site name leads home, except inside the dashboard's frame, where
      // a link home would load the whole site into a panel of itself.
      homeLink: !framed,
    });
    if (!strip) return null;
    buildMenu(strip, me, opts);
    if (opts.sections) buildSections(strip, opts);
    wireModalDismissal();
    const ctl = controller(strip, {
      poll: opts.poll,
      onStatus: opts.onStatus,
      pageName: opts.pageName,
    });
    ctl.setSection = setSection;
    if (opts.siteName) document.title = opts.pageName ? `${opts.siteName} - ${opts.pageName}` : opts.siteName;
    if (opts.landing && !framed) {
      showWhatsNew(me.whats_new);
    }
    return ctl;
  };

  // A page with nobody signed in: the strip, and no menu.
  window.mountStrip = function (options) {
    const opts = options || {};
    const strip = buildStrip({ siteName: opts.siteName, homeLink: false });
    if (!strip) return null;
    return controller(strip, { poll: opts.poll, onStatus: opts.onStatus, pageName: opts.pageName });
  };
})();
