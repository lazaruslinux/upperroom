// Moderator dashboard. A moderator can review watch and chat history and lift
// bans they set, but cannot add or edit accounts and never sees admin accounts.
// Admins may open this page too, but their own dashboard at /admin is fuller.
// Every action is gated server side as well; this page just drives the
// /api/mod/* endpoints.
let me = null;               // this browser's identity, for the shared nav


let users = [];
let bans = [];

// ---- shared helpers -------------------------------------------------------

function avatarColor(seed) {
  let hash = 0;
  for (let i = 0; i < seed.length; i++) hash = (hash * 31 + seed.charCodeAt(i)) % 360;
  return `hsl(${hash}, 55%, 45%)`;
}

function relativeTime(epoch) {
  if (!epoch) return "never";
  const secs = Math.floor(Date.now() / 1000) - epoch;
  if (secs < 60) return "just now";
  const mins = Math.floor(secs / 60);
  if (mins < 60) return `${mins}m ago`;
  const hours = Math.floor(mins / 60);
  if (hours < 24) return `${hours}h ago`;
  const days = Math.floor(hours / 24);
  if (days < 30) return `${days}d ago`;
  return new Date(epoch * 1000).toLocaleDateString();
}

function formatDuration(secs) {
  if (!secs || secs < 60) return `${secs || 0}s`;
  const hours = Math.floor(secs / 3600);
  const mins = Math.floor((secs % 3600) / 60);
  if (hours > 0) return `${hours}h ${mins}m`;
  return `${mins}m`;
}

function formatStamp(epoch) {
  return new Date(epoch * 1000).toLocaleString([], {
    month: "short", day: "numeric", hour: "numeric", minute: "2-digit",
  });
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

// The channel-wide accent flavor (the brand color) is server-driven. The head
// bootstrap paints the last-seen value from localStorage; this syncs it with the
// server on load and remembers it for the next no-flash paint.
function applyAccent(value) {
  if (!["green", "amber", "blue", "ghost"].includes(value)) return;
  if (document.documentElement.dataset.accent !== value) {
    document.documentElement.dataset.accent = value;
    try { localStorage.setItem("selfstream_accent", value); } catch (e) {}
  }
}
(async () => {
  try { applyAccent((await (await fetch("/api/status")).json()).accent); } catch (e) {}
})();

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

// ---- load + render --------------------------------------------------------

async function requireMod() {
  let data;
  try { data = await (await fetch("/api/me")).json(); } catch { data = { authed: false }; }
  if (!data.authed) { window.location.href = "/"; return false; }
  // A guest pass buys the stream and chat, nothing else on the site.
  // Send them where their pass actually works rather than rendering a
  // page whose every request will 401.
  if (data.guest) { window.location.href = "/watch"; return false; }
  if (!data.admin && !data.mod) { window.location.href = "/home"; return false; }
  me = data;
  return true;
}

async function loadAll() {
  const [uReply, bReply] = await Promise.all([
    fetch("/api/mod/users"),
    fetch("/api/mod/bans"),
  ]);
  if (!uReply.ok) { window.location.href = "/home"; return; }
  users = (await uReply.json()).users || [];
  bans = bReply.ok ? ((await bReply.json()).bans || []) : [];
  renderStats();
  renderUsers();
  renderBans();
}

function renderStats() {
  const messages = users.reduce((sum, u) => sum + (u.messages || 0), 0);
  const strip = document.getElementById("stat-strip");
  const cards = [
    ["Viewers", users.length, null],
    ["Active bans", bans.length, null],
    ["Messages (7d)", messages, "chat"],
  ];
  strip.innerHTML = "";
  cards.forEach(([label, value, kind]) => {
    const card = document.createElement("button");
    card.type = "button";
    card.className = "stat-card";
    card.innerHTML = `<span class="stat-value"></span><span class="stat-label"></span>`;
    card.querySelector(".stat-value").textContent = value;
    card.querySelector(".stat-label").textContent = label;
    if (kind === "chat") card.addEventListener("click", openChat);
    else card.style.cursor = "default";
    strip.appendChild(card);
  });
}

function renderUsers() {
  const grid = document.getElementById("user-grid");
  document.getElementById("empty").hidden = users.length > 0;
  grid.innerHTML = "";
  users.forEach((u) => {
    const card = document.createElement("article");
    card.className = "user-card";

    const head = document.createElement("div");
    head.className = "user-head";
    head.appendChild(avatarNode(u.username, u.display_name, u.avatar_version, "avatar avatar-xl"));
    const ident = document.createElement("div");
    ident.className = "user-ident";
    const nameRow = document.createElement("div");
    nameRow.className = "user-name";
    nameRow.textContent = u.display_name;
    if (u.is_moderator) {
      const badge = document.createElement("span");
      badge.className = "role-badge mod";
      badge.textContent = "mod";
      nameRow.appendChild(badge);
    }
    const handle = document.createElement("div");
    handle.className = "user-handle muted";
    handle.textContent = "@" + u.username;
    ident.append(nameRow, handle);
    head.appendChild(ident);
    card.appendChild(head);

    const stats = document.createElement("div");
    stats.className = "user-stats";
    stats.innerHTML = `
      <span><b></b><i>last seen</i></span>
      <span><b></b><i>watch time</i></span>
      <span><b></b><i>messages</i></span>`;
    const cells = stats.querySelectorAll("b");
    cells[0].textContent = relativeTime(u.last_seen);
    cells[1].textContent = formatDuration(u.watch_seconds);
    cells[2].textContent = u.messages;
    card.appendChild(stats);

    const actions = document.createElement("div");
    actions.className = "user-actions";
    const actBtn = document.createElement("button");
    actBtn.type = "button";
    actBtn.className = "chip-btn";
    actBtn.textContent = "Activity";
    actBtn.addEventListener("click", () => openActivity(u));
    actions.appendChild(actBtn);
    card.appendChild(actions);

    grid.appendChild(card);
  });
}

function renderBans() {
  const list = document.getElementById("ban-list");
  document.getElementById("ban-empty").hidden = bans.length > 0;
  list.innerHTML = "";
  bans.forEach((b) => {
    const row = document.createElement("div");
    row.className = "activity-row ban-row";
    const left = document.createElement("span");
    const name = b.display_name || b.username;
    const by = b.banned_by_name || b.banned_by;
    left.innerHTML = `<b></b> <span class="muted"></span>`;
    left.querySelector("b").textContent = `${name} @${b.username}`;
    left.querySelector(".muted").textContent =
      `banned by ${by}${b.reason ? ` · ${b.reason}` : ""}`;
    const btn = document.createElement("button");
    btn.type = "button";
    btn.className = "chip-btn";
    btn.textContent = "Un-ban";
    if (!b.can_lift) {
      btn.disabled = true;
      btn.title = "Only the moderator who set this ban, or an admin, can lift it.";
    } else {
      btn.addEventListener("click", () => unban(b.username, btn));
    }
    row.append(left, btn);
    list.appendChild(row);
  });
}

async function unban(username, btn) {
  btn.disabled = true;
  try {
    const reply = await fetch("/api/mod/unban", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ username }),
    });
    if (reply.ok) { loadAll(); return; }
    const data = await reply.json().catch(() => ({}));
    alert(data.error || "Could not lift the ban.");
  } catch {
    alert("Could not lift the ban.");
  }
  btn.disabled = false;
}

// ---- activity drawer ------------------------------------------------------

const activityModal = document.getElementById("activity-modal");
const aWatch = document.getElementById("a-watch");
const aChat = document.getElementById("a-chat");

async function openActivity(user) {
  document.getElementById("a-title").textContent = `Activity · @${user.username}`;
  aWatch.innerHTML = `<p class="muted">Loading…</p>`;
  aChat.innerHTML = "";
  switchTab("watch");
  openModal(activityModal);

  let data = { watch_sessions: [], chat: [] };
  try {
    data = await (await fetch(`/api/mod/users/${encodeURIComponent(user.username)}/activity`)).json();
  } catch { /* show empties */ }

  aWatch.innerHTML = "";
  if (!data.watch_sessions || !data.watch_sessions.length) {
    aWatch.innerHTML = `<p class="muted">No watch sessions recorded yet.</p>`;
  } else {
    data.watch_sessions.forEach((s) => {
      const row = document.createElement("div");
      row.className = "activity-row";
      const dur = s.left_at ? formatDuration(s.left_at - s.joined_at) : "still watching";
      row.innerHTML = `<span class="act-when"></span><span class="act-dur"></span>`;
      row.querySelector(".act-when").textContent = formatStamp(s.joined_at);
      row.querySelector(".act-dur").textContent = dur;
      aWatch.appendChild(row);
    });
  }

  aChat.innerHTML = "";
  if (!data.chat || !data.chat.length) {
    aChat.innerHTML = `<p class="muted">No chat messages in the last 7 days.</p>`;
  } else {
    data.chat.forEach((m) => {
      const row = document.createElement("div");
      row.className = "activity-row chat-row";
      row.innerHTML = `<span class="act-when"></span><span class="act-text"></span>`;
      row.querySelector(".act-when").textContent = formatStamp(m.ts);
      row.querySelector(".act-text").textContent =
        m.text + (m.deleted_by ? "  (deleted)" : "");
      aChat.appendChild(row);
    });
  }
}

function switchTab(which) {
  document.querySelectorAll(".activity-tabs .tab").forEach((t) => {
    t.classList.toggle("selected", t.dataset.tab === which);
  });
  aWatch.hidden = which !== "watch";
  aChat.hidden = which !== "chat";
}
document.querySelectorAll(".activity-tabs .tab").forEach((t) => {
  t.addEventListener("click", () => switchTab(t.dataset.tab));
});

// ---- global recent chat ---------------------------------------------------

const chatModal = document.getElementById("chat-modal");
const chatBody = document.getElementById("chat-body");

async function openChat() {
  chatBody.innerHTML = `<p class="muted">Loading…</p>`;
  openModal(chatModal);
  let msgs = [];
  try { msgs = (await (await fetch("/api/mod/chat")).json()).messages || []; } catch { /* empty */ }
  chatBody.innerHTML = "";
  if (!msgs.length) { chatBody.innerHTML = `<p class="muted">No messages in the last 7 days.</p>`; return; }
  msgs.forEach((m) => {
    const row = document.createElement("div");
    row.className = "activity-row chat-row";
    row.innerHTML = `<span class="act-when"></span><span class="act-text"></span>`;
    row.querySelector(".act-when").textContent = formatStamp(m.ts);
    row.querySelector(".act-text").textContent =
      `${m.display_name}: ${m.text}` + (m.deleted_by ? "  (deleted)" : "");
    chatBody.appendChild(row);
  });
}

async function boot() {
  if (!(await requireMod())) return;
  mountNav(me, { current: "mod" });
  loadAll();
}

boot();
