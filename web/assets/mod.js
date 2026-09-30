// The moderation page. A moderator can read watch and chat history and lift
// the bans they set, but cannot add or change accounts and never sees admin
// accounts. Admins may open it too, though the dashboard is a superset.
// Every action is gated server side as well; this page only drives the
// /api/mod/* endpoints.

let me = null;               // this browser's identity, for the strip
let users = [];
let bans = [];

// ---- small helpers (private copies, as every page keeps its own) ----------

function $(id) { return document.getElementById(id); }

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

function textNode(tag, cls, text) {
  const el = document.createElement(tag);
  if (cls) el.className = cls;
  el.textContent = text;
  return el;
}

function rowNode(cls) {
  const row = document.createElement("li");
  row.className = `row${cls ? ` ${cls}` : ""}`;
  const main = document.createElement("div");
  main.className = "row-main";
  const tools = document.createElement("div");
  tools.className = "row-tools";
  row.append(main, tools);
  return { row, main, tools };
}

function activityRow(when, text, cls) {
  const row = document.createElement("li");
  row.className = "row activity-row";
  row.append(textNode("span", "act-when", when), textNode("span", cls || "act-text", text));
  return row;
}

// ---- load + render --------------------------------------------------------

async function requireMod() {
  let data;
  try { data = await (await fetch("/api/me")).json(); } catch { data = { authed: false }; }
  if (!data.authed) { window.location.href = "/"; return false; }
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
  renderMeters();
  renderUsers();
  renderBans();
}

function renderMeters() {
  $("m-viewers").textContent = String(users.length);
  $("m-bans").textContent = String(bans.length);
  $("m-messages").textContent = String(users.reduce((sum, u) => sum + (u.messages || 0), 0));
}

function renderUsers() {
  const list = $("user-grid");
  $("empty").hidden = users.length > 0;
  list.textContent = "";
  users.forEach((u) => {
    // A moderator never sees admin accounts and cannot edit, so the one
    // action here is reading what somebody has been doing.
    const { row, main, tools } = rowNode("person-row");
    row.insertBefore(avatarNode(u.username, u.display_name, u.avatar_version), main);
    const name = textNode("span", "row-title", u.display_name);
    if (u.is_moderator) name.appendChild(textNode("span", "role-badge", "mod"));
    main.append(name, textNode("span", "row-meta",
      `@${u.username} · seen ${relativeTime(u.last_seen)} · ${formatDuration(u.watch_seconds)} watched · ${u.messages} msg`));
    const btn = document.createElement("button");
    btn.type = "button";
    btn.className = "chip";
    btn.textContent = "Activity";
    btn.addEventListener("click", () => openActivity(u));
    tools.appendChild(btn);
    list.appendChild(row);
  });
}

function renderBans() {
  const list = $("ban-list");
  $("ban-empty").hidden = bans.length > 0;
  list.textContent = "";
  bans.forEach((b) => {
    const { row, main, tools } = rowNode();
    const by = b.banned_by_name || b.banned_by;
    main.append(
      textNode("span", "row-title", `${b.display_name || b.username} @${b.username}`),
      textNode("span", "row-meta", `banned by ${by}${b.reason ? ` · ${b.reason}` : ""}`),
    );
    const btn = document.createElement("button");
    btn.type = "button";
    btn.className = "chip";
    btn.textContent = "Lift the ban";
    if (!b.can_lift) {
      btn.disabled = true;
      btn.title = "Only the moderator who set this ban, or an admin, can lift it.";
    } else {
      btn.addEventListener("click", () => unban(b.username, btn));
    }
    tools.appendChild(btn);
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

// ---- one viewer's activity ------------------------------------------------

const activityModal = $("activity-modal");
const aWatch = $("a-watch");
const aChat = $("a-chat");

function switchTab(which) {
  document.querySelectorAll(".activity-tabs .tab").forEach((t) => {
    const on = t.dataset.tab === which;
    t.classList.toggle("selected", on);
    t.setAttribute("aria-pressed", on ? "true" : "false");
  });
  aWatch.hidden = which !== "watch";
  aChat.hidden = which !== "chat";
}
document.querySelectorAll(".activity-tabs .tab").forEach((t) => {
  t.addEventListener("click", () => switchTab(t.dataset.tab));
});

async function openActivity(user) {
  $("a-title").textContent = `Activity · @${user.username}`;
  aWatch.textContent = "";
  aWatch.appendChild(textNode("li", "empty", "Loading…"));
  aChat.textContent = "";
  switchTab("watch");
  activityModal.hidden = false;

  let data = { watch_sessions: [], chat: [] };
  try {
    data = await (await fetch(`/api/mod/users/${encodeURIComponent(user.username)}/activity`)).json();
  } catch { /* show the empties */ }

  aWatch.textContent = "";
  if (!data.watch_sessions || !data.watch_sessions.length) {
    aWatch.appendChild(textNode("li", "empty", "No watch sessions recorded yet."));
  } else {
    data.watch_sessions.forEach((s) => {
      const dur = s.left_at ? formatDuration(s.left_at - s.joined_at) : "still watching";
      aWatch.appendChild(activityRow(formatStamp(s.joined_at), dur, "act-dur"));
    });
  }
  aChat.textContent = "";
  if (!data.chat || !data.chat.length) {
    aChat.appendChild(textNode("li", "empty", "No chat messages in the last 7 days."));
  } else {
    data.chat.forEach((m) => {
      aChat.appendChild(activityRow(formatStamp(m.ts), m.text + (m.deleted_by ? "  (deleted)" : "")));
    });
  }
}

// ---- the whole room's recent chat -----------------------------------------

const chatModal = $("chat-modal");
const chatBody = $("chat-body");

async function openChat() {
  chatBody.textContent = "";
  chatBody.appendChild(textNode("li", "empty", "Loading…"));
  chatModal.hidden = false;
  let msgs = [];
  try { msgs = (await (await fetch("/api/mod/chat")).json()).messages || []; } catch { /* empty */ }
  chatBody.textContent = "";
  if (!msgs.length) {
    chatBody.appendChild(textNode("li", "empty", "No messages in the last 7 days."));
    return;
  }
  msgs.forEach((m) => {
    chatBody.appendChild(activityRow(formatStamp(m.ts),
      `${m.display_name}: ${m.text}` + (m.deleted_by ? "  (deleted)" : "")));
  });
}

$("m-chat").addEventListener("click", openChat);
$("chat-open").addEventListener("click", openChat);

async function boot() {
  if (!(await requireMod())) return;
  mountNav(me, { current: "mod", pageName: "Moderation" });
  loadAll();
}

boot();
