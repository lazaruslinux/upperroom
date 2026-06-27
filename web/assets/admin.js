// Admin dashboard. Lists every account with rolled-up activity, and lets the
// admin create, edit, and remove accounts, and review watch and chat history.
// Every action is gated server side too; this page just drives those endpoints.

let users = [];
let editing = null;   // username currently open in the edit modal

// ---- small helpers ----

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

// ---- load + render ----

async function requireAdmin() {
  let data;
  try { data = await (await fetch("/api/me")).json(); } catch { data = { authed: false }; }
  if (!data.authed) { window.location.href = "/"; return false; }
  if (!data.admin) { window.location.href = "/home"; return false; }
  return true;
}

async function loadUsers() {
  const reply = await fetch("/api/admin/users");
  if (!reply.ok) { window.location.href = "/home"; return; }
  users = (await reply.json()).users || [];
  renderStats();
  renderUsers();
}

function renderStats() {
  const admins = users.filter((u) => u.is_admin).length;
  const watch = users.reduce((sum, u) => sum + (u.watch_seconds || 0), 0);
  const messages = users.reduce((sum, u) => sum + (u.messages || 0), 0);
  const strip = document.getElementById("stat-strip");
  const cards = [
    ["Accounts", users.length],
    ["Admins", admins],
    ["Watch time", formatDuration(watch)],
    ["Messages (7d)", messages],
  ];
  strip.innerHTML = "";
  cards.forEach(([label, value]) => {
    const card = document.createElement("div");
    card.className = "stat-card";
    card.innerHTML = `<span class="stat-value"></span><span class="stat-label"></span>`;
    card.querySelector(".stat-value").textContent = value;
    card.querySelector(".stat-label").textContent = label;
    strip.appendChild(card);
  });
}

function renderUsers() {
  const grid = document.getElementById("user-grid");
  document.getElementById("empty").hidden = users.length > 0;
  grid.innerHTML = "";
  users.forEach((u) => {
    const card = document.createElement("article");
    card.className = "user-card" + (u.is_admin ? " is-admin" : "");

    const head = document.createElement("div");
    head.className = "user-head";
    head.appendChild(avatarNode(u.username, u.display_name, u.avatar_version, "avatar avatar-xl"));
    const ident = document.createElement("div");
    ident.className = "user-ident";
    const nameRow = document.createElement("div");
    nameRow.className = "user-name";
    nameRow.textContent = u.display_name;
    if (u.is_admin) {
      const badge = document.createElement("span");
      badge.className = "role-badge";
      badge.textContent = "admin";
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
    const editBtn = document.createElement("button");
    editBtn.type = "button";
    editBtn.className = "chip-btn";
    editBtn.textContent = "Edit";
    editBtn.addEventListener("click", () => openEdit(u));
    const actBtn = document.createElement("button");
    actBtn.type = "button";
    actBtn.className = "chip-btn";
    actBtn.textContent = "Activity";
    actBtn.addEventListener("click", () => openActivity(u));
    actions.append(editBtn, actBtn);
    card.appendChild(actions);

    grid.appendChild(card);
  });
}

// ---- create ----

const createModal = document.getElementById("create-modal");
const createForm = document.getElementById("create-form");
const cError = document.getElementById("c-error");

document.getElementById("new-user").addEventListener("click", () => {
  createForm.reset();
  cError.hidden = true;
  openModal(createModal);
  document.getElementById("c-username").focus();
});

createForm.addEventListener("submit", async (e) => {
  e.preventDefault();
  cError.hidden = true;
  const body = {
    username: document.getElementById("c-username").value,
    display_name: document.getElementById("c-name").value,
    password: document.getElementById("c-password").value,
    is_admin: document.getElementById("c-admin").checked,
  };
  const reply = await fetch("/api/admin/users", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  if (reply.ok) {
    closeModal(createModal);
    loadUsers();
  } else {
    const data = await reply.json().catch(() => ({}));
    cError.textContent = data.error || "Could not create the account.";
    cError.hidden = false;
  }
});

// ---- edit ----

const editModal = document.getElementById("edit-modal");
const editForm = document.getElementById("edit-form");
const eError = document.getElementById("e-error");

function openEdit(user) {
  editing = user.username;
  document.getElementById("e-title").textContent = `Edit @${user.username}`;
  document.getElementById("e-name").value = user.display_name;
  document.getElementById("e-password").value = "";
  document.getElementById("e-admin").checked = !!user.is_admin;
  eError.hidden = true;
  openModal(editModal);
}

editForm.addEventListener("submit", async (e) => {
  e.preventDefault();
  eError.hidden = true;
  const body = {
    display_name: document.getElementById("e-name").value,
    is_admin: document.getElementById("e-admin").checked,
  };
  const pw = document.getElementById("e-password").value;
  if (pw) body.password = pw;
  const reply = await fetch(`/api/admin/users/${encodeURIComponent(editing)}`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  if (reply.ok) {
    closeModal(editModal);
    loadUsers();
  } else {
    const data = await reply.json().catch(() => ({}));
    eError.textContent = data.error || "Could not save changes.";
    eError.hidden = false;
  }
});

document.getElementById("e-delete").addEventListener("click", async () => {
  if (!confirm(`Delete @${editing}? This removes their account, watch history, and chat.`)) return;
  const reply = await fetch(`/api/admin/users/${encodeURIComponent(editing)}`, { method: "DELETE" });
  if (reply.ok) {
    closeModal(editModal);
    loadUsers();
  } else {
    const data = await reply.json().catch(() => ({}));
    eError.textContent = data.error || "Could not delete the account.";
    eError.hidden = false;
  }
});

// ---- activity ----

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
    data = await (await fetch(`/api/admin/users/${encodeURIComponent(user.username)}/activity`)).json();
  } catch { /* show empties */ }

  aWatch.innerHTML = "";
  if (!data.watch_sessions.length) {
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
  if (!data.chat.length) {
    aChat.innerHTML = `<p class="muted">No chat messages in the last 7 days.</p>`;
  } else {
    data.chat.forEach((m) => {
      const row = document.createElement("div");
      row.className = "activity-row chat-row";
      row.innerHTML = `<span class="act-when"></span><span class="act-text"></span>`;
      row.querySelector(".act-when").textContent = formatStamp(m.ts);
      row.querySelector(".act-text").textContent = m.text;
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
  if (!(await requireAdmin())) return;
  loadUsers();
}

boot();
