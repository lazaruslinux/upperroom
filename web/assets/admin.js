// The dashboard: everything an operator runs. People (accounts, bans, invites),
// branding, the schedule, chat rules, the stream key, the overlay,
// notifications, storage and the recorded library.
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
  // A guest pass buys the stream and chat, nothing else on the site.
  // Send them where their pass actually works rather than rendering a
  // page whose every request will 401.
  if (data.guest) { window.location.href = "/watch"; return false; }
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
    // Sharing is per clip and admin only. VODs are deliberately not shareable:
    // a whole broadcast is a much bigger mistake to make public than a minute
    // of it, and a clip's short life bounds the mistake anyway.
    // Shared clips get two buttons rather than one toggle: the link has to stay
    // re-copyable, and unsharing kills it for good, so it cannot be a stray click.
    const shareBtns = [];
    if (kind === "clip" && item.shared) {
      const copy = document.createElement("button");
      copy.type = "button";
      copy.className = "chip-btn pinned-chip";
      copy.textContent = "Copy link";
      copy.title = "Anyone with this link can watch. Click to copy it again.";
      copy.addEventListener("click", () => copyShareLink(item, copy));
      const stop = document.createElement("button");
      stop.type = "button";
      stop.className = "chip-btn danger-chip";
      stop.textContent = "Unshare";
      stop.title = "Kill the public link. Sharing again makes a new one.";
      stop.addEventListener("click", () => unshareClip(item, stop));
      shareBtns.push(copy, stop);
    } else if (kind === "clip") {
      const share = document.createElement("button");
      share.type = "button";
      share.className = "chip-btn";
      share.textContent = "Share";
      share.title = "Make a link anyone can watch, without an account.";
      share.addEventListener("click", () => shareClip(item, share));
      shareBtns.push(share);
    }
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
    shareBtns.forEach((b) => actions.appendChild(b));
    actions.append(pin, btn);
    row.append(left, actions);
    list.appendChild(row);
  });
}

async function shareClip(item, btn) {
  if (!confirm(
    `Share "${item.name}" publicly?\n\n` +
    "Anyone with the link can watch it without an account. " +
    "The chat replay is not included. You can stop sharing at any time.")) return;
  btn.disabled = true;
  try {
    const reply = await fetch(`/api/clips/${item.id}/share`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ share: true }),
    });
    const data = await reply.json().catch(() => ({}));
    if (!reply.ok) {
      alert(data.error || "Could not change sharing.");
    } else if (data.url) {
      const link = window.location.origin + data.url;
      try {
        await navigator.clipboard.writeText(link);
        alert("Link copied:\n\n" + link);
      } catch {
        prompt("Share this link:", link);
      }
    }
  } catch { alert("Could not change sharing."); }
  btn.disabled = false;
  loadContent();
}

async function unshareClip(item, btn) {
  if (!confirm(
    `Stop sharing "${item.name}"?\n\n` +
    "The public link stops working immediately and permanently. " +
    "Sharing again later makes a new link.")) return;
  btn.disabled = true;
  try {
    const reply = await fetch(`/api/clips/${item.id}/share`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ share: false }),
    });
    if (!reply.ok) {
      const data = await reply.json().catch(() => ({}));
      alert(data.error || "Could not change sharing.");
    }
  } catch { alert("Could not change sharing."); }
  btn.disabled = false;
  loadContent();
}

async function copyShareLink(item, btn) {
  if (!item.share_url) return;
  const link = window.location.origin + item.share_url;
  try {
    await navigator.clipboard.writeText(link);
    const was = btn.textContent;
    btn.textContent = "Copied";
    setTimeout(() => { btn.textContent = was; }, 1200);
  } catch {
    prompt("Share this link:", link);
  }
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

// ---- channel settings (the identity: name, description, accent) ----
// The stream title is NOT here any more. It changes every broadcast and it is
// the headline of the link preview, so it lives on the console with the game;
// loadChannel still fills it, because /api/channel is where it comes from.

const chSite = document.getElementById("ch-site");
const chDesc = document.getElementById("ch-desc");
const chMsg = document.getElementById("ch-msg");

// ---- accent flavor (channel-wide brand color) ----
// Picking a swatch is not the same as changing the channel: clicking one only
// marks it selected, and the accent is applied to the document (and remembered
// in localStorage for the next no-flash paint) only once Save actually
// succeeds. That keeps a browsed-but-abandoned pick from restyling the page and
// leaking into the saved localStorage value.
const ACCENTS = ["green", "amber", "blue", "ghost"];
const swatches = document.querySelectorAll("#accent-swatches .accent-swatch");
let accent = "green";

// Mark a swatch as the current pick. No side effects on the document or storage:
// this is only the in-form selection, which Save commits.
function selectAccent(value) {
  if (!ACCENTS.includes(value)) return;
  accent = value;
  swatches.forEach((s) => s.classList.toggle("selected", s.dataset.accent === value));
}

// Commit an accent to the whole document and remember it. Called on load (to
// reflect the saved value) and on a successful save, never on a bare click.
function applyAccentToDocument(value) {
  if (!ACCENTS.includes(value)) return;
  document.documentElement.dataset.accent = value;
  try { localStorage.setItem("selfstream_accent", value); } catch (e) {}
}

swatches.forEach((s) => {
  s.addEventListener("click", () => selectAccent(s.dataset.accent));
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
  chDesc.value = data.description || "";
  setTitleField(data.title || "");
  // On load the saved value is the real one, so both mark it and apply it.
  selectAccent(data.accent || "green");
  applyAccentToDocument(data.accent || "green");
}

document.getElementById("ch-save").addEventListener("click", async () => {
  const siteName = chSite.value.trim();
  if (!siteName) { showChMsg("Site name cannot be empty.", false); return; }
  chMsg.hidden = true;
  try {
    const reply = await fetch("/api/stream-info", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        site_name: siteName, description: chDesc.value.trim(), accent,
      }),
    });
    if (!reply.ok) {
      const d = await reply.json().catch(() => ({}));
      showChMsg(d.error || "Could not save.", false);
      return;
    }
  } catch { showChMsg("Could not reach the server.", false); return; }
  // Only now that the save landed does the accent take effect on the document
  // and in localStorage.
  applyAccentToDocument(accent);
  showChMsg("Saved.", true);
});

// ---- chat moderation (slow mode + banned words) ----

const modSlow = document.getElementById("mod-slow");
const modBanned = document.getElementById("mod-banned");
const modMsg = document.getElementById("mod-msg");
const modBannedLabel = document.getElementById("mod-banned-label");
const bannedModal = document.getElementById("banned-modal");
const bannedMsg = document.getElementById("mod-banned-msg");

function showModMsg(text, ok) {
  modMsg.textContent = text;
  modMsg.classList.toggle("good", !!ok);
  modMsg.classList.toggle("bad", !ok);
  modMsg.hidden = false;
}

function showBannedMsg(text, ok) {
  bannedMsg.textContent = text;
  bannedMsg.classList.toggle("good", !!ok);
  bannedMsg.classList.toggle("bad", !ok);
  bannedMsg.hidden = false;
}

// Entries are separated by newlines or commas, the same split the server does.
// Counting here is only for the summary line, so it never has to be exact about
// anything the filter itself decides.
function countBannedWords(raw) {
  return new Set(
    String(raw || "").replace(/,/g, "\n").split("\n").map((w) => w.trim().toLowerCase()).filter(Boolean)
  ).size;
}

function showBannedCount(raw) {
  const n = countBannedWords(raw);
  modBannedLabel.textContent = n === 1 ? "Banned words (1)" : `Banned words (${n})`;
}

async function loadModeration() {
  let data = {};
  try { data = await (await fetch("/api/admin/moderation")).json(); } catch { return; }
  modSlow.value = data.slow_mode_seconds != null ? data.slow_mode_seconds : 0;
  modBanned.value = data.banned_words || "";
  showBannedCount(modBanned.value);
}

document.getElementById("mod-banned-open").addEventListener("click", () => {
  // Reopen always shows what is actually saved, so abandoning an edit and
  // coming back does not resurrect the abandoned text.
  bannedMsg.hidden = true;
  loadModeration().then(() => { bannedModal.hidden = false; });
});

document.getElementById("mod-banned-save").addEventListener("click", async () => {
  bannedMsg.hidden = true;
  // Only banned_words goes up: the endpoint updates just the fields it is given,
  // so this cannot quietly save an unsaved slow-mode value sitting behind it.
  try {
    const reply = await fetch("/api/admin/moderation", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ banned_words: modBanned.value }),
    });
    if (!reply.ok) {
      const d = await reply.json().catch(() => ({}));
      showBannedMsg(d.error || "Could not save.", false);
      return;
    }
  } catch { showBannedMsg("Could not reach the server.", false); return; }
  showBannedCount(modBanned.value);
  showBannedMsg("Saved.", true);
});

document.getElementById("mod-save").addEventListener("click", async () => {
  const slow = parseInt(modSlow.value, 10);
  if (Number.isNaN(slow) || slow < 0) { showModMsg("Slow mode must be a whole number of seconds.", false); return; }
  modMsg.hidden = true;
  try {
    const reply = await fetch("/api/admin/moderation", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      // Slow mode only. The word list has its own save inside its modal, and
      // sending it from here too would let an abandoned edit ride along.
      body: JSON.stringify({ slow_mode_seconds: slow }),
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
    `${plural(counts.vods || 0, "broadcast")}, ${plural(counts.clips || 0, "clip")}`,
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

// The "Set up in OBS" rows are the same overlay URL with extra query options.
// Each button carries its own suffix in the markup; the key is filled in here so
// a regenerate refreshes every row along with the field above.
const overlaySetupBtns = document.querySelectorAll("[data-overlay-query]");

function setOverlayUrl(key) {
  // Origin so the URL is copy-paste ready into OBS on the same network as here.
  const base = `${window.location.origin}/overlay?key=${key}`;
  overlayUrlInput.value = base;
  overlaySetupBtns.forEach((btn) => {
    btn.dataset.url = base + btn.dataset.overlayQuery;
  });
}

const overlayTicker = document.getElementById("overlay-ticker");

async function loadOverlay() {
  try {
    const data = await (await fetch("/api/admin/overlay")).json();
    if (data.key) setOverlayUrl(data.key);
    if (typeof data.ticker === "string") overlayTicker.value = data.ticker;
  } catch { /* leave the field blank */ }
}

// Save the ticker over the channel-settings route; the server cleans it and
// pushes the new line to any connected overlay at once.
document.getElementById("overlay-ticker-save").addEventListener("click", async (e) => {
  const btn = e.currentTarget;
  btn.disabled = true;
  overlayMsg.hidden = true;
  try {
    const reply = await fetch("/api/stream-info", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ overlay_ticker: overlayTicker.value }),
    });
    if (reply.ok) {
      showOverlayMsg("Ticker saved.", true);
    } else {
      showOverlayMsg("Could not save the ticker.", false);
    }
  } catch { showOverlayMsg("Could not reach the server.", false); }
  btn.disabled = false;
});

// Test-fire buttons: send one synthetic event to any connected overlay so the
// operator can confirm their OBS browser source is wired up. The event goes to
// overlay sockets only; it never touches real chat or the chat log.
document.querySelectorAll("[data-overlay-test]").forEach((btn) => {
  btn.addEventListener("click", async () => {
    const kind = btn.dataset.overlayTest;
    overlayMsg.hidden = true;
    try {
      const reply = await fetch("/api/admin/overlay/test", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ kind }),
      });
      if (reply.ok) {
        showOverlayMsg(`Sent a test ${kind} to the overlay.`, true);
      } else {
        showOverlayMsg("Could not send the test.", false);
      }
    } catch { showOverlayMsg("Could not reach the server.", false); }
  });
});

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

// One delegated handler for the "Set up in OBS" rows, rather than seven copies of
// the same listener. Same feedback as the Copy URL button above.
document.getElementById("overlay-setup").addEventListener("click", async (e) => {
  const btn = e.target.closest("[data-overlay-query]");
  if (!btn || !btn.dataset.url) return;
  try {
    await navigator.clipboard.writeText(btn.dataset.url);
    const was = btn.textContent;
    btn.textContent = "Copied";
    setTimeout(() => { btn.textContent = was; }, 1200);
  } catch {
    showOverlayMsg("Copy failed; your browser blocked the clipboard.", false);
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


// ---- theater and the projector --------------------------------------------
// The session controls, and the key the projector authenticates with. The key
// panel is the stream key panel's shape on purpose: it is the same kind of
// secret, so it should be handled with the same three buttons.

const theaterStatus = document.getElementById("theater-status");
const theaterStart = document.getElementById("theater-start");
const theaterEnd = document.getElementById("theater-end");
const theaterStop = document.getElementById("theater-stop");
const theaterQuery = document.getElementById("theater-query");
const theaterSubs = document.getElementById("theater-subs");
const theaterResults = document.getElementById("theater-results");
const theaterMsg = document.getElementById("theater-msg");
const projectorStatus = document.getElementById("projector-status");
const projectorKeyInput = document.getElementById("projector-key");
const projectorMsg = document.getElementById("projector-msg");

let theaterActive = false;

function showTheaterMsg(text, ok) {
  theaterMsg.textContent = text;
  theaterMsg.classList.toggle("good", !!ok);
  theaterMsg.classList.toggle("bad", !ok);
  theaterMsg.hidden = false;
}

function showProjectorMsg(text, ok) {
  projectorMsg.textContent = text;
  projectorMsg.classList.toggle("good", !!ok);
  projectorMsg.classList.toggle("bad", !ok);
  projectorMsg.hidden = false;
}

function renderTheater(data) {
  theaterActive = !!data.active;
  const now = data.now;
  if (!theaterActive) {
    theaterStatus.textContent = "No session running.";
  } else if (now) {
    const bits = [now.title];
    if (now.year) bits.push(now.year);
    theaterStatus.textContent =
      `${data.state === "playing" ? "Playing" : "Starting"}: ${bits.join(" · ")}`;
  } else {
    theaterStatus.textContent = "Session running · intermission.";
  }
  theaterStart.hidden = theaterActive;
  theaterEnd.hidden = !theaterActive;
  theaterStop.hidden = !theaterActive || !now;
}

function renderProjector(data) {
  if (!data.has_key) {
    projectorStatus.textContent =
      "No key yet. Regenerate to make one, then give it to the projector.";
  } else if (data.connected) {
    projectorStatus.textContent = "Connected.";
  } else if (data.last_seen) {
    projectorStatus.textContent = `Not connected. Last seen ${formatStamp(data.last_seen)}.`;
  } else {
    projectorStatus.textContent = "Not connected.";
  }
  if (data.key !== undefined) projectorKeyInput.value = data.key || "";
}

async function loadProjector() {
  try {
    const reply = await fetch("/api/admin/theater/projector");
    if (reply.ok) renderProjector(await reply.json());
  } catch { /* leave the last state rather than flashing disconnected */ }
}

async function loadTheater() {
  try {
    const reply = await fetch("/api/theater");
    if (reply.ok) renderTheater(await reply.json());
  } catch { /* same */ }
}

// Every control answers with the same state payload, so one path applies it.
async function theaterAction(path, body) {
  theaterMsg.hidden = true;
  try {
    const reply = await fetch(path, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: body ? JSON.stringify(body) : undefined,
    });
    const data = await reply.json().catch(() => ({}));
    if (!reply.ok) {
      showTheaterMsg(
        reply.status === 502
          ? "The projector is not connected."
          : (data.error || "Could not do that."),
        false,
      );
      return null;
    }
    renderTheater(data);
    loadProjector();
    return data;
  } catch {
    showTheaterMsg("Could not reach the server.", false);
    return null;
  }
}

function renderTheaterResults(results) {
  theaterResults.textContent = "";
  results.forEach((item) => {
    const row = document.createElement("div");
    row.className = "ts-row";
    const label = document.createElement("div");
    label.className = "ts-label";
    const title = document.createElement("span");
    title.className = "ts-title";
    title.textContent = item.title;
    label.appendChild(title);
    const bits = [];
    if (item.year) bits.push(item.year);
    if (item.runtime_min) bits.push(`${item.runtime_min} min`);
    if (item.has_subtitles) bits.push("subtitles");
    if (bits.length) {
      const meta = document.createElement("span");
      meta.className = "ts-meta";
      meta.textContent = bits.join(" · ");
      label.appendChild(meta);
    }
    row.appendChild(label);
    const play = document.createElement("button");
    play.type = "button";
    play.className = "chip-btn";
    play.textContent = "play";
    play.addEventListener("click", async () => {
      play.disabled = true;
      const done = await theaterAction("/api/admin/theater/play", {
        jf_id: item.jf_id, subtitles: theaterSubs.checked,
      });
      if (done) showTheaterMsg(`Playing "${item.title}".`, true);
      play.disabled = false;
    });
    row.appendChild(play);
    theaterResults.appendChild(row);
  });
}

document.getElementById("theater-search").addEventListener("click", async () => {
  const query = theaterQuery.value.trim();
  if (query.length < 2) {
    showTheaterMsg("Search for at least two characters.", false);
    return;
  }
  theaterMsg.hidden = true;
  try {
    const reply = await fetch(
      `/api/admin/theater/search?q=${encodeURIComponent(query)}`
    );
    const data = await reply.json().catch(() => ({}));
    if (!reply.ok) {
      showTheaterMsg(
        reply.status === 502
          ? "The projector is not connected."
          : (data.error || "Could not search."),
        false,
      );
      return;
    }
    renderTheaterResults(data.results || []);
    if (!(data.results || []).length) showTheaterMsg("Nothing matched.", false);
  } catch { showTheaterMsg("Could not reach the server.", false); }
});

theaterQuery.addEventListener("keydown", (e) => {
  if (e.key === "Enter") document.getElementById("theater-search").click();
});

theaterStart.addEventListener("click", async () => {
  if (await theaterAction("/api/admin/theater/session")) {
    showTheaterMsg("Session started. Viewers see the intermission card.", true);
  }
});

theaterStop.addEventListener("click", () => theaterAction("/api/admin/theater/stop"));

theaterEnd.addEventListener("click", async () => {
  if (!confirm(
    "End the theater session? Whatever is playing stops, and the chat is wiped " +
    "the way it is at the end of any broadcast."
  )) return;
  if (await theaterAction("/api/admin/theater/end")) {
    showTheaterMsg("Session ended.", true);
  }
});

document.getElementById("projector-show").addEventListener("click", (e) => {
  const btn = e.currentTarget;
  const hidden = projectorKeyInput.type === "password";
  projectorKeyInput.type = hidden ? "text" : "password";
  btn.textContent = hidden ? "Hide" : "Show";
});

document.getElementById("projector-copy").addEventListener("click", async (e) => {
  try {
    await navigator.clipboard.writeText(projectorKeyInput.value);
    const btn = e.currentTarget;
    const was = btn.textContent;
    btn.textContent = "Copied";
    setTimeout(() => { btn.textContent = was; }, 1200);
  } catch {
    projectorKeyInput.select();
    showProjectorMsg("Copy failed; the key is selected so you can copy it.", false);
  }
});

document.getElementById("projector-regen").addEventListener("click", async (e) => {
  if (!confirm(
    "Regenerate the projector key? The projector disconnects at once and will " +
    "not come back until it has the new key."
  )) return;
  const btn = e.currentTarget;
  btn.disabled = true;
  projectorMsg.hidden = true;
  try {
    const reply = await fetch("/api/admin/theater/projector/key", { method: "POST" });
    if (reply.ok) {
      renderProjector(await reply.json());
      showProjectorMsg("New key generated. Update the projector's settings.", true);
    } else {
      showProjectorMsg("Could not regenerate the key.", false);
    }
  } catch { showProjectorMsg("Could not reach the server.", false); }
  btn.disabled = false;
});


// ---- people: accounts, bans and invites -----------------------------------
// Ported from the old /accounts page. The endpoints are unchanged; only where
// the UI lives has moved. The list, the forms and the activity view share one
// modal and swap its body, so nothing ever stacks.

let users = [];
let editing = null;   // username open in the edit view

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

const usersModal = document.getElementById("users-modal");

function showUsersView(which) {
  usersModal.querySelectorAll(".users-view").forEach((v) => {
    v.hidden = v.dataset.view !== which;
  });
}

function openUsers(view) {
  showUsersView(view || "list");
  usersModal.hidden = false;
  loadUsers();
}

document.getElementById("manage-users").addEventListener("click", () => openUsers());
document.getElementById("manage-bans")
  .addEventListener("click", () => openUsers("bans"));
usersModal.querySelectorAll("[data-back]").forEach((b) => {
  b.addEventListener("click", () => showUsersView("list"));
});

async function loadUsers() {
  const reply = await fetch("/api/admin/users");
  if (!reply.ok) return;
  users = (await reply.json()).users || [];
  renderUsers();
  loadBans();
}

function renderUsers() {
  const list = document.getElementById("user-list");
  document.getElementById("user-empty").hidden = users.length > 0;
  list.innerHTML = "";
  users.forEach((u) => {
    const row = document.createElement("div");
    row.className = "user-row" + (u.is_admin ? " is-admin" : "");
    row.appendChild(avatarNode(u.username, u.display_name, u.avatar_version, "avatar"));

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
    const seen = document.createElement("div");
    seen.className = "user-seen";
    seen.textContent =
      `${relativeTime(u.last_seen)} · ${formatDuration(u.watch_seconds)} · ${u.messages} msg`;
    ident.append(nameRow, handle, seen);
    row.appendChild(ident);

    const actions = document.createElement("span");
    actions.className = "row-actions";
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
    row.appendChild(actions);

    list.appendChild(row);
  });
}

// ---- create ----

const createForm = document.getElementById("create-form");
const cError = document.getElementById("c-error");

document.getElementById("user-new").addEventListener("click", () => {
  createForm.reset();
  cError.hidden = true;
  showUsersView("create");
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
    showUsersView("list");
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
// changes it from their own settings. The server refuses it either way.

const editForm = document.getElementById("edit-form");
const eError = document.getElementById("e-error");
const eAdmin = document.getElementById("e-admin");
const eAdminNote = document.getElementById("e-admin-note");

// The address stays editable for an admin account, because hiding it would mean
// a forgotten address silently starts receiving mail the day they are demoted.
// Say plainly that it is dormant instead.
function syncAdminNote() { eAdminNote.hidden = !eAdmin.checked; }
eAdmin.addEventListener("change", syncAdminNote);

function openEdit(user) {
  editing = user.username;
  document.getElementById("e-title").textContent = `Edit @${user.username}`;
  document.getElementById("e-email").value = user.email || "";
  document.getElementById("e-password").value = "";
  eAdmin.checked = !!user.is_admin;
  document.getElementById("e-mod").checked = !!user.is_moderator;
  document.getElementById("e-notify").checked = user.notify_live !== 0;
  syncAdminNote();
  eError.hidden = true;
  showUsersView("edit");
}

editForm.addEventListener("submit", async (e) => {
  e.preventDefault();
  eError.hidden = true;
  const body = {
    email: document.getElementById("e-email").value,
    is_admin: eAdmin.checked,
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
    showUsersView("list");
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

const deleteForm = document.getElementById("delete-form");
const dConfirm = document.getElementById("d-confirm");
const dGo = document.getElementById("d-go");
const dError = document.getElementById("d-error");

// Held separately from `editing`, and read only from here, so that whatever the
// edit view does afterwards this flow can only ever delete the account it was
// opened on.
let deleting = null;

document.getElementById("e-delete").addEventListener("click", () => {
  deleting = editing;
  document.getElementById("d-blurb").textContent =
    `This removes @${deleting}, their watch history and their chat. It cannot be undone.`;
  dConfirm.value = "";
  dGo.disabled = true;
  dError.hidden = true;
  showUsersView("delete");
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
      deleting = null;
      showUsersView("list");
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

const aWatch = document.getElementById("a-watch");
const aChat = document.getElementById("a-chat");

async function openActivity(user) {
  document.getElementById("a-title").textContent = `Activity · @${user.username}`;
  aWatch.innerHTML = `<p class="muted">Loading…</p>`;
  aChat.innerHTML = "";
  switchActivityTab("watch");
  showUsersView("activity");

  let data = { watch_sessions: [], chat: [] };
  try {
    data = await (await fetch(`/api/admin/users/${encodeURIComponent(user.username)}/activity`)).json();
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

function switchActivityTab(which) {
  document.querySelectorAll(".activity-tabs .tab").forEach((t) => {
    t.classList.toggle("selected", t.dataset.tab === which);
  });
  aWatch.hidden = which !== "watch";
  aChat.hidden = which !== "chat";
}
document.querySelectorAll(".activity-tabs .tab").forEach((t) => {
  t.addEventListener("click", () => switchActivityTab(t.dataset.tab));
});

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
  // The count rides the button that opens the list, so the People tab still
  // says at a glance whether anyone is barred without a section of its own.
  const opener = document.getElementById("manage-bans");
  if (opener) opener.textContent = bans.length ? `Bans (${bans.length})` : "Bans";
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
    } else {
      // Spent codes previously had no action at all, so the list only grew.
      // Only these can be removed; an active one has to be revoked first.
      const removeBtn = document.createElement("button");
      removeBtn.type = "button";
      removeBtn.className = "chip-btn danger-chip";
      removeBtn.textContent = "Remove";
      removeBtn.addEventListener("click", () => removeInvite(inv.code, removeBtn));
      actions.appendChild(removeBtn);
    }

    row.append(left, actions);
    list.appendChild(row);
  });
  // The sweep is only offered when there is something to sweep.
  const spent = invites.filter((i) => i.redeemed_at || i.revoked_at);
  document.getElementById("invite-clear-used").hidden = spent.length === 0;
}

async function removeInvite(code, btn) {
  btn.disabled = true;
  try {
    const reply = await fetch(
      `/api/admin/invites/${encodeURIComponent(code)}/remove`,
      { method: "POST" },
    );
    if (reply.ok) { loadInvites(); return; }
    const data = await reply.json().catch(() => ({}));
    alert(data.error || "Could not remove the code.");
  } catch { alert("Could not remove the code."); }
  btn.disabled = false;
}

document.getElementById("invite-clear-used").addEventListener("click", async (e) => {
  const btn = e.currentTarget;
  if (!confirm("Remove every used and revoked invite code? The accounts they created are not affected.")) return;
  btn.disabled = true;
  try {
    const reply = await fetch("/api/admin/invites/clear-used", { method: "POST" });
    if (!reply.ok) {
      const data = await reply.json().catch(() => ({}));
      alert(data.error || "Could not clear the codes.");
    }
  } catch { alert("Could not reach the server."); }
  btn.disabled = false;
  loadInvites();
});

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

// ---- guest passes (generate in a batch, copy, revoke, remove) ----
// The workflow this is shaped around: make several at once, copy them all into
// one group text, and each person redeems one. So the batch field and "copy all
// unused" are the primary controls, not conveniences.

let guestPasses = [];
let guestMinutes = 30;

async function loadGuestPasses() {
  try {
    const data = await (await fetch("/api/admin/guest-passes")).json();
    guestPasses = data.passes || [];
    guestMinutes = data.minutes || 30;
    document.getElementById("guest-minutes").textContent = guestMinutes;
  } catch { guestPasses = []; }
  renderGuestPasses();
}

function guestPassStatus(row, now) {
  if (row.redeemed_at) {
    const who = row.redeemed_by_name || "a guest";
    // While the session is still running, the useful thing to know is how long
    // is left, not when it started.
    const left = (row.guest_expires_at || 0) - now;
    if (left > 0) {
      return { text: `${who} · ${Math.ceil(left / 60)} min left`, cls: "active" };
    }
    return { text: `used by ${who}`, cls: "redeemed" };
  }
  if (row.revoked_at) return { text: "revoked", cls: "revoked" };
  return { text: "unused", cls: "active" };
}

function renderGuestPasses() {
  const list = document.getElementById("gp-list");
  const now = Math.floor(Date.now() / 1000);
  document.getElementById("gp-empty").hidden = guestPasses.length > 0;
  const spent = guestPasses.filter((p) => p.redeemed_at || p.revoked_at);
  const unused = guestPasses.filter((p) => !p.redeemed_at && !p.revoked_at);
  document.getElementById("gp-copy-all").hidden = unused.length === 0;
  document.getElementById("gp-clear-used").hidden = spent.length === 0;
  list.innerHTML = "";
  guestPasses.forEach((row) => {
    const status = guestPassStatus(row, now);
    const item = document.createElement("div");
    item.className = "activity-row ban-row";

    const left = document.createElement("span");
    const code = document.createElement("span");
    code.className = "invite-code";
    code.textContent = row.code;
    const meta = document.createElement("span");
    meta.className = "invite-status " + status.cls;
    meta.textContent = (row.label ? `${row.label} · ` : "") + status.text;
    left.append(code, document.createElement("br"), meta);

    const actions = document.createElement("span");
    actions.className = "invite-actions";
    const copyBtn = document.createElement("button");
    copyBtn.type = "button";
    copyBtn.className = "chip-btn";
    copyBtn.textContent = "Copy";
    copyBtn.addEventListener("click", () => copyInvite(row.code, copyBtn));
    actions.appendChild(copyBtn);

    if (!row.redeemed_at && !row.revoked_at) {
      const revokeBtn = document.createElement("button");
      revokeBtn.type = "button";
      revokeBtn.className = "chip-btn danger-chip";
      revokeBtn.textContent = "Revoke";
      revokeBtn.addEventListener("click", () => revokeGuestPass(row.code, revokeBtn));
      actions.appendChild(revokeBtn);
    } else {
      // Only a spent pass can be removed. An active one has to be revoked
      // first, so removing can never quietly un-issue a code someone holds.
      const removeBtn = document.createElement("button");
      removeBtn.type = "button";
      removeBtn.className = "chip-btn danger-chip";
      removeBtn.textContent = "Remove";
      removeBtn.addEventListener("click", () => removeGuestPass(row.code, removeBtn));
      actions.appendChild(removeBtn);
    }

    item.append(left, actions);
    list.appendChild(item);
  });
}

async function revokeGuestPass(code, btn) {
  if (!confirm(`Revoke ${code}? It can no longer be redeemed.`)) return;
  btn.disabled = true;
  try {
    const reply = await fetch(`/api/admin/guest-passes/${encodeURIComponent(code)}`, {
      method: "DELETE",
    });
    if (reply.ok) { loadGuestPasses(); return; }
    const data = await reply.json().catch(() => ({}));
    alert(data.error || "Could not revoke the pass.");
  } catch { alert("Could not revoke the pass."); }
  btn.disabled = false;
}

async function removeGuestPass(code, btn) {
  btn.disabled = true;
  try {
    const reply = await fetch(
      `/api/admin/guest-passes/${encodeURIComponent(code)}/remove`,
      { method: "POST" },
    );
    if (reply.ok) { loadGuestPasses(); return; }
    const data = await reply.json().catch(() => ({}));
    alert(data.error || "Could not remove the pass.");
  } catch { alert("Could not remove the pass."); }
  btn.disabled = false;
}

document.getElementById("gp-new").addEventListener("click", async (e) => {
  const btn = e.currentTarget;
  const label = document.getElementById("gp-label").value;
  const count = Number(document.getElementById("gp-count").value) || 1;
  btn.disabled = true;
  try {
    const reply = await fetch("/api/admin/guest-passes", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ label, count }),
    });
    if (reply.ok) {
      document.getElementById("gp-label").value = "";
      loadGuestPasses();
    } else {
      const data = await reply.json().catch(() => ({}));
      alert(data.error || "Could not generate passes.");
    }
  } catch { alert("Could not reach the server."); }
  btn.disabled = false;
});

document.getElementById("gp-copy-all").addEventListener("click", async (e) => {
  const btn = e.currentTarget;
  const codes = guestPasses
    .filter((p) => !p.redeemed_at && !p.revoked_at)
    .map((p) => p.code);
  if (!codes.length) return;
  try {
    await navigator.clipboard.writeText(codes.join("\n"));
    const was = btn.textContent;
    btn.textContent = "Copied";
    setTimeout(() => { btn.textContent = was; }, 1200);
  } catch {
    alert(codes.join("\n"));
  }
});

document.getElementById("gp-clear-used").addEventListener("click", async (e) => {
  const btn = e.currentTarget;
  if (!confirm("Remove every used and revoked pass? The codes themselves are already spent.")) return;
  btn.disabled = true;
  try {
    const reply = await fetch("/api/admin/guest-passes/clear-used", { method: "POST" });
    if (!reply.ok) {
      const data = await reply.json().catch(() => ({}));
      alert(data.error || "Could not clear the passes.");
    }
  } catch { alert("Could not reach the server."); }
  btn.disabled = false;
  loadGuestPasses();
});

// ---- the live console (top of the dashboard) ----
// The watch page itself, in a frame, so the streamer sees and hears exactly what
// the room does without a second tab. The frame is talked to with postMessage
// rather than by changing its src, because a reload would drop its chat socket
// and its place in the stream every time the view is toggled.

const liveView = document.getElementById("live-view");
const liveFrame = document.getElementById("live-frame");
const VIEW_KEY = "selfstream_dash_video";
let showVideo = true;

function tellFrame() {
  if (!liveView || !liveView.contentWindow) return;
  liveView.contentWindow.postMessage(
    { type: "video", show: showVideo }, location.origin
  );
}

function setView(show, remember) {
  showVideo = show;
  if (liveFrame) liveFrame.classList.toggle("is-chat-only", !show);
  document.querySelectorAll(".live-view-toggle .chip-btn").forEach((btn) => {
    btn.classList.toggle("is-on", (btn.dataset.view === "chat") !== show);
  });
  if (remember) {
    try { localStorage.setItem(VIEW_KEY, show ? "full" : "chat"); } catch (e) {}
  }
  tellFrame();
}

function setUpConsole() {
  if (!liveView) return;
  let saved = "full";
  try { saved = localStorage.getItem(VIEW_KEY) || "full"; } catch (e) {}
  setView(saved !== "chat", false);
  document.querySelectorAll(".live-view-toggle .chip-btn").forEach((btn) => {
    btn.addEventListener("click", () => setView(btn.dataset.view !== "chat", true));
  });
  // The frame starts on the default view, so tell it again once it has loaded
  // and after any later reload of its own.
  liveView.addEventListener("load", tellFrame);
}

// ---- what is on: the title and the game ----
// The two lines of the link preview, edited together on the console. The title
// comes from /api/channel (loadChannel fills it); the game rides the stream
// poll, because it changes far more often and the poll is already running.

const titleInput = document.getElementById("onair-title");
const gameInput = document.getElementById("game-input");
const gameOptions = document.getElementById("game-options");
const onairMsg = document.getElementById("onair-msg");
let savedGame = "";

function showOnAirMsg(text, ok) {
  onairMsg.textContent = text;
  onairMsg.classList.toggle("good", !!ok);
  onairMsg.classList.toggle("bad", !ok);
  onairMsg.hidden = !text;
}

// Called by loadChannel, which is the only thing that reads the saved title.
function setTitleField(value) {
  if (titleInput) titleInput.value = value;
}

function renderGameOptions(names) {
  gameOptions.textContent = "";
  (names || []).forEach((name) => {
    const option = document.createElement("option");
    option.value = name;
    gameOptions.appendChild(option);
  });
}

// One request for both fields. `game` is sent as a plain string because an empty
// one is a real value there (it clears the label); an empty title is refused by
// the server, so it is only sent when there is something to send.
async function saveOnAir({ title, game }) {
  showOnAirMsg("", true);
  const body = {};
  if (title !== undefined) body.title = title;
  if (game !== undefined) body.game = game;
  try {
    const reply = await fetch("/api/stream-info", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    const data = await reply.json().catch(() => ({}));
    if (!reply.ok) {
      showOnAirMsg(data.error || "Could not save that.", false);
      return;
    }
    if (game !== undefined) {
      savedGame = game;
      gameInput.value = game;
    }
    showOnAirMsg(game === "" ? "Saved. No game showing." : "Saved.", true);
    loadStream();          // refreshes the remembered list straight away
  } catch {
    showOnAirMsg("Could not reach the server.", false);
  }
}

function setUpOnAir() {
  if (!gameInput) return;
  const save = () => saveOnAir({
    title: titleInput.value.trim(), game: gameInput.value.trim(),
  });
  [titleInput, gameInput].forEach((field) => {
    field.addEventListener("keydown", (e) => { if (e.key === "Enter") save(); });
  });
  document.getElementById("onair-save").addEventListener("click", save);
  // Clears the game on its own, so it cannot trip over an empty title box.
  document.getElementById("game-none").addEventListener("click", () => {
    gameInput.value = "";
    saveOnAir({ game: "" });
  });
}

// ---- live stream strip (top of the dashboard) ----
// A streamer should never have to read the container logs to know their broadcast
// is up and being recorded, so the strip polls the admin stream status while the
// page is open and shows live/offline, uptime, who is watching, and the recorder.
const streamStrip = document.getElementById("stream-strip");
const streamState = document.getElementById("stream-state");
const streamSince = document.getElementById("stream-since");
const streamWatching = document.getElementById("stream-watching");
const streamRec = document.getElementById("stream-rec");

function uptimeLabel(since) {
  const secs = Math.max(0, Math.floor(Date.now() / 1000) - since);
  if (secs < 60) return "up just now";
  const hours = Math.floor(secs / 3600);
  const mins = Math.floor((secs % 3600) / 60);
  return hours > 0 ? `up ${hours}h ${mins}m` : `up ${mins}m`;
}

function renderStream(data) {
  const live = !!data.live;
  streamStrip.classList.toggle("is-live", live);
  streamStrip.classList.toggle("is-offline", !live);
  streamState.textContent = live ? "Live" : "Offline";
  if (!live) {
    streamSince.hidden = true;
    streamWatching.hidden = true;
    streamRec.hidden = true;
    return;
  }
  streamSince.textContent = data.since ? uptimeLabel(data.since) : "";
  streamSince.hidden = !data.since;
  const n = typeof data.watching === "number" ? data.watching : 0;
  streamWatching.textContent = n === 1 ? "1 watching" : `${n} watching`;
  streamWatching.hidden = false;
  // The recorder state is the headline: a live broadcast that is not being
  // recorded is a problem to surface, not one to leave the streamer guessing at.
  if (data.recording === "ok") {
    streamRec.textContent = "recording";
    streamRec.className = "stream-strip-rec is-recording";
  } else if (data.recording === "restarting") {
    streamRec.textContent = "recording (restarting)";
    streamRec.className = "stream-strip-rec is-restarting";
  } else {
    streamRec.textContent = "not recording";
    streamRec.className = "stream-strip-rec is-notrecording";
  }
  streamRec.hidden = false;
}

// The game rides the same poll. Only written into the box when the operator is
// not mid-edit, so a poll landing while they type cannot overwrite it.
function renderGame(data) {
  if (!gameInput) return;
  renderGameOptions(data.recent_games);
  const value = data.game || "";
  if (value !== savedGame && document.activeElement !== gameInput) {
    savedGame = value;
    gameInput.value = value;
  }
}

async function loadStream() {
  if (!streamStrip) return;
  try {
    const reply = await fetch("/api/admin/stream");
    if (reply.ok) {
      const data = await reply.json();
      renderStream(data);
      renderGame(data);
    }
  } catch { /* keep the last state rather than flashing offline on a blip */ }
}

// The dashboard footer shows the running release, read from /api/status rather
// than baked into the markup so a version bump changes it in exactly one place
// (config.VERSION) and can never drift here. Left blank if status is unreachable.
async function loadVersion() {
  const line = document.getElementById("version-line");
  if (!line) return;
  try {
    const data = await (await fetch("/api/status")).json();
    if (data.version) line.textContent = `upperroom v${data.version}`;
  } catch { /* leave the footer blank rather than show a stale guess */ }
}

// ---- the control groups ----
// Fourteen panels stacked in one column was a page to scroll past and fourteen
// requests fired at page open, most of them for something the operator was not
// looking at. They are five tabs now: one group is shown at a time, and a
// group's data is fetched the first time it is shown. The console above is not
// a group, because it is what the page is for.

const TAB_KEY = "selfstream_dash_tab";

const PANEL_LOADERS = {
  broadcast: [loadTheater],
  content: [loadContent, loadRetention],
  people: [loadBans, loadInvites, loadGuestPasses],
  channel: [loadModeration, loadNotify],
  connections: [loadStreamKey, loadOverlay, loadProjector],
};

const loadedPanels = new Set();

function showPanel(name, remember) {
  if (!PANEL_LOADERS[name]) name = "broadcast";
  document.querySelectorAll(".admin-tabs .lib-tab").forEach((tab) => {
    tab.classList.toggle("selected", tab.dataset.panel === name);
  });
  document.querySelectorAll(".admin-group").forEach((group) => {
    group.hidden = group.dataset.panel !== name;
  });
  if (!loadedPanels.has(name)) {
    loadedPanels.add(name);
    PANEL_LOADERS[name].forEach((load) => load());
  }
  if (remember) {
    try { localStorage.setItem(TAB_KEY, name); } catch (e) {}
  }
}

function setUpPanels() {
  document.querySelectorAll(".admin-tabs .lib-tab").forEach((tab) => {
    tab.addEventListener("click", () => showPanel(tab.dataset.panel, true));
  });
  // Following /admin#people from the dashboard itself changes only the hash, so
  // the page never reloads and nothing below would run again.
  window.addEventListener("hashchange", () => {
    const name = (location.hash || "").replace("#", "");
    if (PANEL_LOADERS[name]) showPanel(name, true);
  });
  // A link to /admin#people lands there; otherwise carry on from wherever the
  // last visit finished.
  let start = (location.hash || "").replace("#", "");
  if (!PANEL_LOADERS[start]) {
    try { start = localStorage.getItem(TAB_KEY) || ""; } catch (e) { start = ""; }
  }
  showPanel(start, false);
}

// Invites and guest passes share a panel: the same job twice over, so the two
// lists take turns rather than sitting one above the other.
function setUpCodeTabs() {
  const tabs = document.querySelectorAll(".lib-tab[data-codes]");
  tabs.forEach((tab) => {
    tab.addEventListener("click", () => {
      tabs.forEach((t) => t.classList.toggle("selected", t === tab));
      document.querySelectorAll(".codes-view").forEach((view) => {
        view.hidden = view.dataset.codes !== tab.dataset.codes;
      });
    });
  });
}

async function boot() {
  if (!(await requireAdmin())) return;
  mountNav(me, { current: "dashboard" });
  setUpConsole();
  setUpOnAir();
  setUpCodeTabs();
  loadVersion();
  loadStream();
  setInterval(loadStream, 10000);
  // Eager despite living in the Channel tab: it owns the accent applied to the
  // whole document, and it is where the console's title field is filled from.
  loadChannel();
  // Last, so the first group's loaders run after everything above is wired.
  setUpPanels();
  // Only while a session is open: the state moves on its own then (a title
  // ending puts the room back to intermission), and between sessions the
  // operator's own actions are the only thing that changes it. What is actually
  // on air is the console's job now, and that is a live player, not a poll.
  setInterval(() => {
    if (!theaterActive) return;
    loadTheater();
    loadProjector();
  }, 10000);
}

boot();
