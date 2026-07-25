// Accounts page. Everything about who can get in: the account list with
// rolled-up activity, active bans, and invite codes. Creating, editing and
// removing accounts lives here too. Channel and stream settings are on /admin.
// Every action is gated server side as well; this page just drives those
// endpoints.

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
  loadBans();
}

// ---- bans (shared with the mod dashboard via /api/mod/*) ----

let bans = [];

async function loadBans() {
  try { bans = (await (await fetch("/api/mod/bans")).json()).bans || []; }
  catch { bans = []; }
  renderBans();
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
    btn.addEventListener("click", () => unban(b.username, btn));
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
    if (reply.ok) { loadBans(); return; }
    const data = await reply.json().catch(() => ({}));
    alert(data.error || "Could not lift the ban.");
  } catch { alert("Could not lift the ban."); }
  btn.disabled = false;
}

// ---- invites (generate, copy, revoke) ----

let invites = [];

async function loadInvites() {
  try { invites = (await (await fetch("/api/admin/invites")).json()).invites || []; }
  catch { invites = []; }
  renderInvites();
}

function inviteStatus(inv) {
  if (inv.redeemed_at) {
    const who = inv.redeemed_by_name || inv.redeemed_by || "someone";
    const when = new Date(inv.redeemed_at * 1000).toLocaleDateString();
    return { text: `redeemed by ${who} · ${when}`, cls: "redeemed" };
  }
  if (inv.revoked_at) return { text: "revoked", cls: "revoked" };
  return { text: "active", cls: "active" };
}

function renderInvites() {
  const list = document.getElementById("invite-list");
  document.getElementById("invite-empty").hidden = invites.length > 0;
  list.innerHTML = "";
  invites.forEach((inv) => {
    const status = inviteStatus(inv);
    const row = document.createElement("div");
    row.className = "activity-row ban-row";

    const left = document.createElement("span");
    const code = document.createElement("span");
    code.className = "invite-code";
    code.textContent = inv.code;
    const meta = document.createElement("span");
    meta.className = "invite-status " + status.cls;
    meta.textContent = (inv.label ? `${inv.label} · ` : "") + status.text;
    left.append(code, document.createElement("br"), meta);

    const actions = document.createElement("span");
    actions.className = "invite-actions";
    const copyBtn = document.createElement("button");
    copyBtn.type = "button";
    copyBtn.className = "chip-btn";
    copyBtn.textContent = "Copy";
    copyBtn.addEventListener("click", () => copyInvite(inv.code, copyBtn));
    actions.appendChild(copyBtn);
    if (status.cls === "active") {
      const revokeBtn = document.createElement("button");
      revokeBtn.type = "button";
      revokeBtn.className = "chip-btn danger-chip";
      revokeBtn.textContent = "Revoke";
      revokeBtn.addEventListener("click", () => revokeInvite(inv.code, revokeBtn));
      actions.appendChild(revokeBtn);
    }

    row.append(left, actions);
    list.appendChild(row);
  });
}

async function copyInvite(code, btn) {
  try {
    await navigator.clipboard.writeText(code);
    const was = btn.textContent;
    btn.textContent = "Copied";
    setTimeout(() => { btn.textContent = was; }, 1200);
  } catch {
    alert(code);
  }
}

async function revokeInvite(code, btn) {
  if (!confirm(`Revoke ${code}? It can no longer be redeemed.`)) return;
  btn.disabled = true;
  try {
    const reply = await fetch(`/api/admin/invites/${encodeURIComponent(code)}`, { method: "DELETE" });
    if (reply.ok) { loadInvites(); return; }
    const data = await reply.json().catch(() => ({}));
    alert(data.error || "Could not revoke the code.");
  } catch { alert("Could not revoke the code."); }
  btn.disabled = false;
}

document.getElementById("invite-new").addEventListener("click", async (e) => {
  const btn = e.currentTarget;
  const label = document.getElementById("invite-label").value;
  btn.disabled = true;
  try {
    const reply = await fetch("/api/admin/invites", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ label }),
    });
    if (reply.ok) {
      document.getElementById("invite-label").value = "";
      loadInvites();
    } else {
      const data = await reply.json().catch(() => ({}));
      alert(data.error || "Could not generate a code.");
    }
  } catch { alert("Could not reach the server."); }
  btn.disabled = false;
});

// ---- stats ----

function renderStats() {
  const admins = users.filter((u) => u.is_admin).length;
  const watch = users.reduce((sum, u) => sum + (u.watch_seconds || 0), 0);
  const messages = users.reduce((sum, u) => sum + (u.messages || 0), 0);
  const strip = document.getElementById("stat-strip");
  const cards = [
    ["Accounts", users.length, "accounts"],
    ["Admins", admins, "admins"],
    ["Watch time", formatDuration(watch), "watch"],
    ["Messages (7d)", messages, "messages"],
  ];
  strip.innerHTML = "";
  cards.forEach(([label, value, kind]) => {
    const card = document.createElement("button");
    card.type = "button";
    card.className = "stat-card";
    card.innerHTML = `<span class="stat-value"></span><span class="stat-label"></span>`;
    card.querySelector(".stat-value").textContent = value;
    card.querySelector(".stat-label").textContent = label;
    card.addEventListener("click", () => openStat(kind));
    strip.appendChild(card);
  });
}

// ---- stat detail (tap a stat card) ----

const statModal = document.getElementById("stat-modal");
const statTitle = document.getElementById("stat-title");
const statBody = document.getElementById("stat-body");

function userRow(u, showWatch) {
  const row = document.createElement("div");
  row.className = "activity-row";
  const left = document.createElement("span");
  left.textContent = `${u.display_name} @${u.username}${u.is_admin ? " · admin" : ""}`;
  const right = document.createElement("span");
  right.className = "act-dur";
  if (showWatch) right.textContent = formatDuration(u.watch_seconds);
  row.append(left, right);
  return row;
}

async function openStat(kind) {
  const titles = {
    accounts: "All accounts",
    admins: "Admins",
    watch: "Watch time (live only)",
    messages: "Recent chat (7 days)",
  };
  statTitle.textContent = titles[kind] || "Details";
  statBody.innerHTML = "";
  openModal(statModal);

  if (kind === "messages") {
    statBody.innerHTML = `<p class="muted">Loading…</p>`;
    let msgs = [];
    try { msgs = (await (await fetch("/api/admin/chat")).json()).messages || []; } catch { /* empty */ }
    statBody.innerHTML = "";
    if (!msgs.length) { statBody.innerHTML = `<p class="muted">No messages in the last 7 days.</p>`; return; }
    msgs.forEach((m) => {
      const row = document.createElement("div");
      row.className = "activity-row chat-row";
      row.innerHTML = `<span class="act-when"></span><span class="act-text"></span>`;
      row.querySelector(".act-when").textContent = formatStamp(m.ts);
      row.querySelector(".act-text").textContent = `${m.display_name}: ${m.text}`;
      statBody.appendChild(row);
    });
    return;
  }

  let list = users;
  if (kind === "admins") list = users.filter((u) => u.is_admin);
  if (kind === "watch") {
    list = users.filter((u) => u.watch_seconds > 0)
                .sort((a, b) => b.watch_seconds - a.watch_seconds);
  }
  if (!list.length) {
    statBody.innerHTML = `<p class="muted">Nothing here yet.</p>`;
    return;
  }
  list.forEach((u) => statBody.appendChild(userRow(u, kind === "watch")));
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
    email: document.getElementById("c-email").value,
    password: document.getElementById("c-password").value,
    is_admin: document.getElementById("c-admin").checked,
    is_moderator: document.getElementById("c-mod").checked,
    notify_live: document.getElementById("c-notify").checked,
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
// The display name is deliberately absent. An admin picks the starting name
// when creating the account; after that it belongs to the account holder, who
// changes it from their own settings.

const editModal = document.getElementById("edit-modal");
const editForm = document.getElementById("edit-form");
const eError = document.getElementById("e-error");

function openEdit(user) {
  editing = user.username;
  document.getElementById("e-title").textContent = `Edit @${user.username}`;
  document.getElementById("e-email").value = user.email || "";
  document.getElementById("e-password").value = "";
  document.getElementById("e-admin").checked = !!user.is_admin;
  document.getElementById("e-mod").checked = !!user.is_moderator;
  document.getElementById("e-notify").checked = user.notify_live !== 0;
  eError.hidden = true;
  openModal(editModal);
}

editForm.addEventListener("submit", async (e) => {
  e.preventDefault();
  eError.hidden = true;
  const body = {
    email: document.getElementById("e-email").value,
    is_admin: document.getElementById("e-admin").checked,
    is_moderator: document.getElementById("e-mod").checked,
    notify_live: document.getElementById("e-notify").checked,
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

// ---- delete ----
// Deleting takes an account, its watch history and its chat with it, and there
// is no undo, so the only way through is to type the username. The server asks
// for the same thing, so a mis-wired button cannot delete anyone either.

const deleteModal = document.getElementById("delete-modal");
const deleteForm = document.getElementById("delete-form");
const dConfirm = document.getElementById("d-confirm");
const dGo = document.getElementById("d-go");
const dError = document.getElementById("d-error");

// Held separately from `editing`, and read only from here, so that whatever the
// edit modal does afterwards this flow can only ever delete the account it was
// opened on.
let deleting = null;

document.getElementById("e-delete").addEventListener("click", () => {
  deleting = editing;
  document.getElementById("d-blurb").textContent =
    `This removes @${deleting}, their watch history and their chat. It cannot be undone.`;
  dConfirm.value = "";
  dGo.disabled = true;
  dError.hidden = true;
  closeModal(editModal);
  openModal(deleteModal);
  dConfirm.focus();
});

// The server normalises the same way, so the button and the endpoint agree on
// what counts as a match.
dConfirm.addEventListener("input", () => {
  dGo.disabled = dConfirm.value.trim().toLowerCase() !== (deleting || "").toLowerCase();
});

deleteForm.addEventListener("submit", async (e) => {
  e.preventDefault();
  if (dGo.disabled || !deleting) return;
  dError.hidden = true;
  dGo.disabled = true;
  const url = `/api/admin/users/${encodeURIComponent(deleting)}`
    + `?confirm=${encodeURIComponent(dConfirm.value.trim())}`;
  try {
    const reply = await fetch(url, { method: "DELETE" });
    if (reply.ok) {
      closeModal(deleteModal);
      deleting = null;
      loadUsers();
      return;
    }
    const data = await reply.json().catch(() => ({}));
    dError.textContent = data.error || "Could not delete the account.";
  } catch {
    dError.textContent = "Could not reach the server.";
  }
  dError.hidden = false;
  dGo.disabled = false;
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

// ---- boot ----

(async () => {
  if (!(await requireAdmin())) return;
  loadUsers();
  loadInvites();
})();
