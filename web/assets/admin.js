// Admin dashboard: the channel itself. Branding, the schedule, chat rules, the
// stream key, the overlay, notifications, storage and the recorded library.
// Accounts, bans and invites live on their own page, /accounts.
// Every action is gated server side too; this page just drives those endpoints.
let me = null;               // this browser's identity, for the shared nav


// ---- small helpers ----

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

// ---- load + render ----

async function requireAdmin() {
  let data;
  try { data = await (await fetch("/api/me")).json(); } catch { data = { authed: false }; }
  if (!data.authed) { window.location.href = "/"; return false; }
  if (!data.admin) { window.location.href = "/home"; return false; }
  me = data;
  return true;
}

// ---- content (VODs + clips: review and delete) ----

let contentTab = "vods";

function durationClock(secs) {
  secs = Math.max(0, Math.round(secs || 0));
  const h = Math.floor(secs / 3600);
  const m = Math.floor((secs % 3600) / 60);
  const s = secs % 60;
  const pad = (n) => String(n).padStart(2, "0");
  return h > 0 ? `${h}:${pad(m)}:${pad(s)}` : `${m}:${pad(s)}`;
}

async function loadContent() {
  const kind = contentTab === "vods" ? "vod" : "clip";
  let items = [];
  try { items = (await (await fetch(`/api/${contentTab}`)).json())[contentTab] || []; }
  catch { items = []; }
  const list = document.getElementById("content-list");
  document.getElementById("content-empty").hidden = items.length > 0;
  list.innerHTML = "";
  items.forEach((item) => {
    const row = document.createElement("div");
    row.className = "activity-row ban-row";
    const left = document.createElement("span");
    const title = kind === "vod" ? item.title : item.name;
    const when = new Date((kind === "vod" ? item.started_at : item.created_at) * 1000)
      .toLocaleDateString();
    left.innerHTML = `<a></a> <span class="muted"></span>`;
    const link = left.querySelector("a");
    link.href = `/media?type=${kind}&id=${item.id}`;
    link.textContent = title;
    left.querySelector(".muted").textContent =
      `${durationClock(item.duration)} · ${item.views} views · ${when}` +
      (kind === "clip" && item.creator ? ` · @${item.creator}` : "");
    const pin = document.createElement("button");
    pin.type = "button";
    pin.className = "chip-btn" + (item.keep ? " pinned-chip" : "");
    pin.textContent = item.keep ? "Pinned" : "Pin";
    pin.title = item.keep
      ? "Retention never removes this. Click to unpin."
      : "Keep this no matter what retention says.";
    pin.addEventListener("click", () => togglePin(kind, item.id, !item.keep, pin));
    const btn = document.createElement("button");
    btn.type = "button";
    btn.className = "chip-btn danger-chip";
    btn.textContent = "Delete";
    btn.addEventListener("click", () => deleteContent(kind, item.id, title, btn));
    const actions = document.createElement("span");
    actions.className = "row-actions";
    actions.append(pin, btn);
    row.append(left, actions);
    list.appendChild(row);
  });
}

async function togglePin(kind, id, keep, btn) {
  btn.disabled = true;
  try {
    const reply = await fetch(`/api/${kind}s/${id}/keep`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ keep }),
    });
    if (reply.ok) { loadContent(); loadRetention(); return; }
    const data = await reply.json().catch(() => ({}));
    alert(data.error || "Could not change the pin.");
  } catch { alert("Could not change the pin."); }
  btn.disabled = false;
}

async function deleteContent(kind, id, title, btn) {
  if (!confirm(`Delete "${title}"? This removes the file and its chat replay.`)) return;
  btn.disabled = true;
  try {
    const reply = await fetch(`/api/${kind}s/${id}`, { method: "DELETE" });
    if (reply.ok) { loadContent(); return; }
    const data = await reply.json().catch(() => ({}));
    alert(data.error || "Could not delete.");
  } catch { alert("Could not delete."); }
  btn.disabled = false;
}

document.querySelectorAll(".lib-tab[data-content]").forEach((tab) => {
  tab.addEventListener("click", () => {
    contentTab = tab.dataset.content;
    document.querySelectorAll(".lib-tab[data-content]").forEach((t) => t.classList.toggle("selected", t === tab));
    loadContent();
  });
});

// ---- channel settings (title, description, clip cooldowns) ----

const chSite = document.getElementById("ch-site");
const chTitle = document.getElementById("ch-title");
const chDesc = document.getElementById("ch-desc");
const chCdUser = document.getElementById("ch-cd-user");
const chCdMod = document.getElementById("ch-cd-mod");
const chCdAdmin = document.getElementById("ch-cd-admin");
const chMsg = document.getElementById("ch-msg");

// ---- accent flavor (channel-wide brand color) ----
// The chosen flavor is applied to the whole document (data-accent) so every
// visitor sees it. It is remembered in localStorage for the next no-flash paint.
const ACCENTS = ["green", "amber", "blue", "ghost"];
const swatches = document.querySelectorAll("#accent-swatches .accent-swatch");
let accent = "green";

function applyAccent(value) {
  if (!ACCENTS.includes(value)) return;
  accent = value;
  document.documentElement.dataset.accent = value;
  try { localStorage.setItem("selfstream_accent", value); } catch (e) {}
  swatches.forEach((s) => s.classList.toggle("selected", s.dataset.accent === value));
}

swatches.forEach((s) => {
  s.addEventListener("click", () => applyAccent(s.dataset.accent));
});

function showChMsg(text, ok) {
  chMsg.textContent = text;
  chMsg.classList.toggle("good", !!ok);
  chMsg.classList.toggle("bad", !ok);
  chMsg.hidden = false;
}

async function loadChannel() {
  let data = {};
  try { data = await (await fetch("/api/channel")).json(); } catch { return; }
  chSite.value = data.site_name || "";
  chTitle.value = data.title || "";
  chDesc.value = data.description || "";
  chCdUser.value = data.clip_cooldown_user != null ? data.clip_cooldown_user : 15;
  chCdMod.value = data.clip_cooldown_mod != null ? data.clip_cooldown_mod : 5;
  chCdAdmin.value = data.clip_cooldown_admin != null ? data.clip_cooldown_admin : 1;
  applyAccent(data.accent || "green");
}

document.getElementById("ch-save").addEventListener("click", async () => {
  const siteName = chSite.value.trim();
  if (!siteName) { showChMsg("Site name cannot be empty.", false); return; }
  const title = chTitle.value.trim();
  if (!title) { showChMsg("Stream title cannot be empty.", false); return; }
  const u = parseInt(chCdUser.value, 10);
  const m = parseInt(chCdMod.value, 10);
  const a = parseInt(chCdAdmin.value, 10);
  if ([u, m, a].some(Number.isNaN)) { showChMsg("Cooldowns must be whole numbers.", false); return; }
  chMsg.hidden = true;
  try {
    const reply = await fetch("/api/stream-info", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        site_name: siteName, title, description: chDesc.value.trim(),
        clip_cooldown_user: u, clip_cooldown_mod: m, clip_cooldown_admin: a,
        accent,
      }),
    });
    if (!reply.ok) {
      const d = await reply.json().catch(() => ({}));
      showChMsg(d.error || "Could not save.", false);
      return;
    }
  } catch { showChMsg("Could not reach the server.", false); return; }
  showChMsg("Saved.", true);
});

// ---- chat moderation (slow mode + banned words) ----

const modSlow = document.getElementById("mod-slow");
const modBanned = document.getElementById("mod-banned");
const modMsg = document.getElementById("mod-msg");

function showModMsg(text, ok) {
  modMsg.textContent = text;
  modMsg.classList.toggle("good", !!ok);
  modMsg.classList.toggle("bad", !ok);
  modMsg.hidden = false;
}

async function loadModeration() {
  let data = {};
  try { data = await (await fetch("/api/admin/moderation")).json(); } catch { return; }
  modSlow.value = data.slow_mode_seconds != null ? data.slow_mode_seconds : 0;
  modBanned.value = data.banned_words || "";
}

document.getElementById("mod-save").addEventListener("click", async () => {
  const slow = parseInt(modSlow.value, 10);
  if (Number.isNaN(slow) || slow < 0) { showModMsg("Slow mode must be a whole number of seconds.", false); return; }
  modMsg.hidden = true;
  try {
    const reply = await fetch("/api/admin/moderation", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ slow_mode_seconds: slow, banned_words: modBanned.value }),
    });
    if (!reply.ok) {
      const d = await reply.json().catch(() => ({}));
      showModMsg(d.error || "Could not save.", false);
      return;
    }
  } catch { showModMsg("Could not reach the server.", false); return; }
  showModMsg("Saved.", true);
});

// ---- go-live notifications ----

const notifyWebhook = document.getElementById("notify-webhook");
const notifyEmailOn = document.getElementById("notify-email-on");
const notifyStatus = document.getElementById("notify-status");
const notifyMsg = document.getElementById("notify-msg");

function showNotifyMsg(text, ok) {
  notifyMsg.textContent = text;
  notifyMsg.classList.toggle("good", !!ok);
  notifyMsg.classList.toggle("bad", !ok);
  notifyMsg.hidden = false;
}

async function loadNotify() {
  let data = {};
  try { data = await (await fetch("/api/admin/notify")).json(); } catch { return; }
  notifyWebhook.value = data.discord_webhook || "";
  notifyEmailOn.checked = data.email_on_live !== false;
  const bits = [];
  // Three states, not two: the relay can be missing, or present but switched
  // off here. Saying "email is set up" while nothing sends would be a lie.
  if (!data.smtp_configured) {
    bits.push("Email is not configured on the server (set the SMTP variables to enable it).");
  } else if (data.email_on_live === false) {
    bits.push("Email is set up but switched off, so nobody is emailed when you go live.");
  } else {
    bits.push(`Email is set up. ${data.recipients} ${data.recipients === 1 ? "person" : "people"} will be emailed.`);
  }
  if (!data.site_url) bits.push("Set SELFSTREAM_SITE_URL so messages include a watch link.");
  if (data.last_notified_at) bits.push(`Last announced ${relativeTime(data.last_notified_at)}.`);
  notifyStatus.textContent = bits.join(" ");
}

async function saveNotify(test) {
  notifyMsg.hidden = true;
  const body = {
    discord_webhook: notifyWebhook.value,
    email_on_live: notifyEmailOn.checked,
  };
  if (test) body.test = true;
  let reply;
  try {
    reply = await fetch("/api/admin/notify", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
  } catch { showNotifyMsg("Could not reach the server.", false); return; }
  if (reply.ok) {
    showNotifyMsg(test ? "Test announcement sent." : "Saved.", true);
    loadNotify();
  } else {
    const data = await reply.json().catch(() => ({}));
    showNotifyMsg(data.error || "Could not save.", false);
  }
}

document.getElementById("notify-save").addEventListener("click", () => saveNotify(false));
document.getElementById("notify-test").addEventListener("click", () => saveNotify(true));

// ---- the next scheduled stream ----

const schedWhen = document.getElementById("sched-when");
const schedNote = document.getElementById("sched-note");
const schedMsg = document.getElementById("sched-msg");

function showSchedMsg(text, ok) {
  schedMsg.textContent = text;
  schedMsg.classList.toggle("good", !!ok);
  schedMsg.classList.toggle("bad", !ok);
  schedMsg.hidden = false;
}

function localInputValue(epoch) {
  // <input type="datetime-local"> wants local wall-clock with no zone, so shift
  // the epoch by the browser's offset before trimming the ISO string.
  const at = new Date((epoch - new Date().getTimezoneOffset() * 60) * 1000);
  return at.toISOString().slice(0, 16);
}

function renderSchedule(data) {
  const when = data.next_stream_at || 0;
  schedWhen.value = when ? localInputValue(when) : "";
  schedNote.value = data.next_stream_note || "";
  // Echo the stored time back in words, so there is no doubt what was saved.
  document.getElementById("sched-status").textContent = when
    ? `Announced for ${new Date(when * 1000).toLocaleString()}, your time.`
    : "Nothing scheduled.";
}

async function loadSchedule() {
  try { renderSchedule(await (await fetch("/api/admin/schedule")).json()); }
  catch { /* leave the panel as it was */ }
}

async function saveSchedule(clear) {
  schedMsg.hidden = true;
  // A datetime-local value has no timezone, so the browser reads it as local
  // time; the server only ever stores the epoch.
  const when = clear || !schedWhen.value
    ? 0
    : Math.floor(new Date(schedWhen.value).getTime() / 1000);
  if (!clear && schedWhen.value && !when) {
    showSchedMsg("That is not a valid date and time.", false);
    return;
  }
  let reply;
  try {
    reply = await fetch("/api/admin/schedule", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        next_stream_at: when,
        next_stream_note: clear ? "" : schedNote.value,
      }),
    });
  } catch { showSchedMsg("Could not reach the server.", false); return; }
  const data = await reply.json().catch(() => ({}));
  if (!reply.ok) { showSchedMsg(data.error || "Could not save.", false); return; }
  renderSchedule(data);
  showSchedMsg(when ? "Saved. Viewers can see the countdown." : "Cleared.", true);
}

document.getElementById("sched-save").addEventListener("click", () => saveSchedule(false));
document.getElementById("sched-clear").addEventListener("click", () => saveSchedule(true));

// ---- storage and retention ----

const RETENTION_FIELDS = {
  "ret-vod-count": "vod_keep_count",
  "ret-vod-days": "vod_keep_days",
  "ret-clip-count": "clip_keep_count",
  "ret-clip-days": "clip_keep_days",
  "ret-cap-gb": "media_cap_gb",
};
const retMsg = document.getElementById("ret-msg");

function formatBytes(bytes) {
  const units = ["B", "KB", "MB", "GB", "TB"];
  let value = Math.max(0, bytes || 0);
  let unit = 0;
  while (value >= 1024 && unit < units.length - 1) { value /= 1024; unit += 1; }
  return `${value < 10 && unit > 0 ? value.toFixed(1) : Math.round(value)} ${units[unit]}`;
}

function showRetMsg(text, ok) {
  retMsg.textContent = text;
  retMsg.classList.toggle("good", !!ok);
  retMsg.classList.toggle("bad", !ok);
  retMsg.hidden = false;
}

function renderRetention(data) {
  Object.entries(RETENTION_FIELDS).forEach(([id, field]) => {
    document.getElementById(id).value = data[field] ?? 0;
  });
  const usage = data.usage || {};
  const counts = data.counts || {};
  const plural = (n, word) => `${n} ${word}${n === 1 ? "" : "s"}`;
  const parts = [
    `${formatBytes(usage.total_bytes)} used`,
    `${plural(counts.vods || 0, "recording")}, ${plural(counts.clips || 0, "clip")}`,
  ];
  if (counts.pinned) parts.push(`${counts.pinned} pinned`);
  if (usage.free_bytes) parts.push(`${formatBytes(usage.free_bytes)} free on disk`);
  document.getElementById("storage-usage").textContent = parts.join(" · ");
  // The bar is the media store against the whole filesystem it sits on, so it
  // answers "how close am I to trouble" rather than "how close to my own cap".
  const fill = document.getElementById("usage-fill");
  const capacity = usage.fs_total_bytes || 0;
  const usedShare = capacity
    ? Math.min(100, ((capacity - (usage.free_bytes || 0)) / capacity) * 100)
    : 0;
  fill.style.width = `${usedShare}%`;
  fill.classList.toggle("is-tight", usedShare >= 90);
  const off = Object.values(RETENTION_FIELDS).every((field) => !data[field]);
  const state = document.getElementById("retention-state");
  state.textContent = off
    ? "Retention is off. Nothing is ever deleted automatically."
    : "Retention is on. Unpinned items past these limits are deleted.";
  state.classList.toggle("good", off);
  state.classList.toggle("bad", !off);
  state.hidden = false;
}

async function loadRetention() {
  try { renderRetention(await (await fetch("/api/admin/retention")).json()); }
  catch { /* leave the panel as it was */ }
}

async function saveRetention() {
  retMsg.hidden = true;
  const body = {};
  Object.entries(RETENTION_FIELDS).forEach(([id, field]) => {
    body[field] = Number(document.getElementById(id).value || 0);
  });
  let reply;
  try {
    reply = await fetch("/api/admin/retention", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
  } catch { showRetMsg("Could not reach the server.", false); return; }
  const data = await reply.json().catch(() => ({}));
  if (!reply.ok) { showRetMsg(data.error || "Could not save.", false); return; }
  const removed = data.removed || 0;
  showRetMsg(
    removed
      ? `Saved. Removed ${removed} ${removed === 1 ? "item" : "items"}.`
      : "Saved. Nothing needed removing.",
    true,
  );
  renderRetention(data);
  loadContent();
}

document.getElementById("ret-save").addEventListener("click", saveRetention);

// ---- overlay (OBS chat browser source) ----

const overlayUrlInput = document.getElementById("overlay-url");
const overlayMsg = document.getElementById("overlay-msg");

function showOverlayMsg(text, ok) {
  overlayMsg.textContent = text;
  overlayMsg.classList.toggle("good", !!ok);
  overlayMsg.classList.toggle("bad", !ok);
  overlayMsg.hidden = false;
}

function setOverlayUrl(key) {
  // Origin so the URL is copy-paste ready into OBS on the same network as here.
  overlayUrlInput.value = `${window.location.origin}/overlay?key=${key}`;
}

async function loadOverlay() {
  try {
    const data = await (await fetch("/api/admin/overlay")).json();
    if (data.key) setOverlayUrl(data.key);
  } catch { /* leave the field blank */ }
}

document.getElementById("overlay-copy").addEventListener("click", async (e) => {
  try {
    await navigator.clipboard.writeText(overlayUrlInput.value);
    const btn = e.currentTarget;
    const was = btn.textContent;
    btn.textContent = "Copied";
    setTimeout(() => { btn.textContent = was; }, 1200);
  } catch {
    overlayUrlInput.select();
    showOverlayMsg("Copy failed; the URL is selected so you can copy it.", false);
  }
});

document.getElementById("overlay-regen").addEventListener("click", async (e) => {
  if (!confirm("Regenerate the overlay URL? The current one will stop working.")) return;
  const btn = e.currentTarget;
  btn.disabled = true;
  overlayMsg.hidden = true;
  try {
    const reply = await fetch("/api/admin/overlay/regenerate", { method: "POST" });
    if (reply.ok) {
      const data = await reply.json();
      setOverlayUrl(data.key);
      showOverlayMsg("New URL generated. Update your OBS browser source.", true);
    } else {
      showOverlayMsg("Could not regenerate the URL.", false);
    }
  } catch { showOverlayMsg("Could not reach the server.", false); }
  btn.disabled = false;
});

// ---- stream key (OBS publish) ----

const streamServerInput = document.getElementById("stream-server");
const streamKeyInput = document.getElementById("stream-key");
const streamKeyMsg = document.getElementById("stream-key-msg");

function showStreamKeyMsg(text, ok) {
  streamKeyMsg.textContent = text;
  streamKeyMsg.classList.toggle("good", !!ok);
  streamKeyMsg.classList.toggle("bad", !ok);
  streamKeyMsg.hidden = false;
}

function setStreamKey(key) {
  // The RTMP endpoint OBS publishes to. hostname (not origin) since RTMP is its
  // own scheme and port, served on the same host as this dashboard.
  streamServerInput.value = `rtmp://${window.location.hostname}:1935`;
  // The exact string to paste into the OBS "Stream Key" box. user is omitted;
  // the gate ignores it and checks only the key.
  streamKeyInput.value = `live?pass=${key}`;
}

async function loadStreamKey() {
  try {
    const data = await (await fetch("/api/admin/stream-key")).json();
    if (data.key) setStreamKey(data.key);
  } catch { /* leave the fields blank */ }
}

document.getElementById("stream-key-show").addEventListener("click", (e) => {
  const btn = e.currentTarget;
  const hidden = streamKeyInput.type === "password";
  streamKeyInput.type = hidden ? "text" : "password";
  btn.textContent = hidden ? "Hide" : "Show";
});

document.getElementById("stream-key-copy").addEventListener("click", async (e) => {
  try {
    await navigator.clipboard.writeText(streamKeyInput.value);
    const btn = e.currentTarget;
    const was = btn.textContent;
    btn.textContent = "Copied";
    setTimeout(() => { btn.textContent = was; }, 1200);
  } catch {
    streamKeyInput.select();
    showStreamKeyMsg("Copy failed; the key is selected so you can copy it.", false);
  }
});

document.getElementById("stream-key-regen").addEventListener("click", async (e) => {
  if (!confirm(
    "Regenerate the stream key? A broadcast already live keeps running, but the " +
    "next connection needs the new key. Update OBS before you next go live."
  )) return;
  const btn = e.currentTarget;
  btn.disabled = true;
  streamKeyMsg.hidden = true;
  try {
    const reply = await fetch("/api/admin/stream-key/regenerate", { method: "POST" });
    if (reply.ok) {
      const data = await reply.json();
      setStreamKey(data.key);
      showStreamKeyMsg("New key generated. Update your OBS stream key.", true);
    } else {
      showStreamKeyMsg("Could not regenerate the key.", false);
    }
  } catch { showStreamKeyMsg("Could not reach the server.", false); }
  btn.disabled = false;
});

async function boot() {
  if (!(await requireAdmin())) return;
  mountNav(me, { current: "dashboard" });
  loadContent();
  loadChannel();
  loadModeration();
  loadStreamKey();
  loadOverlay();
  loadNotify();
  loadRetention();
  loadSchedule();
}

boot();
