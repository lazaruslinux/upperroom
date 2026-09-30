// The dashboard: everything an operator runs.
//
// It opens on Go live: the three things that have to be true for a night to
// happen (OBS has the settings, OBS is streaming, the link has gone out), each
// ticking as it becomes true, beside the room itself. Everything else is a
// section behind the menu in the strip: People, Library, Channel, Chat rules
// and Connections here, Stats on its own page.
//
// Every action is gated server side too; this page only drives the endpoints.

let me = null;               // this browser's identity, for the strip
let strip = null;            // nav.js's strip controller

// ---- small helpers ---------------------------------------------------------

function $(id) { return document.getElementById(id); }

// A status line under a control: good or bad, and gone when there is nothing
// to say.
function say(el, text, ok) {
  el.textContent = text || "";
  el.classList.toggle("good", !!ok);
  el.classList.toggle("bad", !ok);
  el.hidden = !text;
}

async function getJSON(url) {
  const reply = await fetch(url);
  if (!reply.ok) throw new Error(String(reply.status));
  return reply.json();
}

// POST a JSON body. Resolves to { ok, status, data } and never throws on an
// HTTP error, so each caller decides what a refusal means for its own control.
async function postJSON(url, body) {
  const reply = await fetch(url, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  const data = await reply.json().catch(() => ({}));
  return { ok: reply.ok, status: reply.status, data };
}

async function copyText(text) {
  try {
    await navigator.clipboard.writeText(text);
    return true;
  } catch {
    return false;
  }
}

// Copy, then say so on the button itself for a moment. True if it copied.
async function copyWithButton(text, btn, onFail) {
  if (!text) return false;
  if (await copyText(text)) {
    const was = btn.textContent;
    btn.textContent = "Copied";
    setTimeout(() => { btn.textContent = was; }, 1200);
    return true;
  }
  if (onFail) onFail();
  return false;
}

// A secret field: password-masked until asked, and masked again on request.
function wireShow(button, input) {
  button.addEventListener("click", () => {
    const reveal = input.type === "password";
    input.type = reveal ? "text" : "password";
    button.textContent = reveal ? "Hide" : "Show";
    button.setAttribute("aria-pressed", reveal ? "true" : "false");
  });
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

function durationClock(secs) {
  secs = Math.max(0, Math.round(secs || 0));
  const h = Math.floor(secs / 3600);
  const m = Math.floor((secs % 3600) / 60);
  const s = secs % 60;
  const pad = (n) => String(n).padStart(2, "0");
  return h > 0 ? `${h}:${pad(m)}:${pad(s)}` : `${m}:${pad(s)}`;
}

function formatStamp(epoch) {
  return new Date(epoch * 1000).toLocaleString([], {
    month: "short", day: "numeric", hour: "numeric", minute: "2-digit",
  });
}

// A clock time, with the day in front when it is not today.
function clockTime(epoch) {
  const when = new Date(epoch * 1000);
  const time = when.toLocaleTimeString([], { hour: "numeric", minute: "2-digit" });
  if (when.toDateString() === new Date().toDateString()) return time;
  return `${when.toLocaleDateString([], { weekday: "short" })} ${time}`;
}

function formatBytes(bytes) {
  const units = ["B", "KB", "MB", "GB", "TB"];
  let value = Math.max(0, bytes || 0);
  let unit = 0;
  while (value >= 1024 && unit < units.length - 1) { value /= 1024; unit += 1; }
  return `${value < 10 && unit > 0 ? value.toFixed(1) : Math.round(value)} ${units[unit]}`;
}

function plural(n, word, many) {
  return `${n} ${n === 1 ? word : (many || `${word}s`)}`;
}

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

// One row of a list: the words on the left, the buttons on the right.
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

function textNode(tag, cls, text) {
  const el = document.createElement(tag);
  if (cls) el.className = cls;
  el.textContent = text;
  return el;
}

function button(label, cls, onClick) {
  const btn = document.createElement("button");
  btn.type = "button";
  btn.className = cls || "chip";
  btn.textContent = label;
  if (onClick) btn.addEventListener("click", () => onClick(btn));
  return btn;
}

async function requireAdmin() {
  let data;
  try { data = await getJSON("/api/me"); } catch { data = { authed: false }; }
  if (!data.authed) { window.location.href = "/"; return false; }
  if (!data.admin) { window.location.href = "/home"; return false; }
  me = data;
  return true;
}

// =============================================================================
// Go live
// =============================================================================

let stream = {};              // the last /api/admin/stream answer
let storageAway = false;      // the media store did not answer the last ask
let storagePending = 0;       // recordings waiting to be saved to it
let linkSent = false;         // the watch link was copied on this page load

// ---- step 1: the settings OBS needs ----

const streamServer = $("stream-server");
const streamKey = $("stream-key");
const streamKeyMsg = $("stream-key-msg");

function setStreamKey(key) {
  // RTMP is its own scheme and port on the same host as this page.
  streamServer.value = `rtmp://${window.location.hostname}:1935`;
  // Exactly what goes in OBS's Stream Key box. `live` is the path the rest of
  // the app expects; the gate reads only the key after it.
  streamKey.value = `live?pass=${key}`;
}

async function loadStreamKey() {
  try {
    const data = await getJSON("/api/admin/stream-key");
    if (data.key) setStreamKey(data.key);
  } catch { /* leave the fields blank */ }
}

wireShow($("stream-key-show"), streamKey);
$("server-copy").addEventListener("click", (e) => copyWithButton(
  streamServer.value, e.currentTarget, () => {
    streamServer.select();
    say(streamKeyMsg, "Copy failed; the server is selected so you can copy it.", false);
  }));
$("stream-key-copy").addEventListener("click", (e) => copyWithButton(
  streamKey.value, e.currentTarget, () => {
    streamKey.type = "text";
    streamKey.select();
    say(streamKeyMsg, "Copy failed; the key is selected so you can copy it.", false);
  }));

$("stream-key-regen").addEventListener("click", async (e) => {
  if (!confirm(
    "Regenerate the stream key? OBS cannot go live again until you paste the " +
    "new one into it. A broadcast already on air keeps running."
  )) return;
  const btn = e.currentTarget;
  btn.disabled = true;
  say(streamKeyMsg, "", true);
  try {
    const { ok, data } = await postJSON("/api/admin/stream-key/regenerate");
    if (ok && data.key) {
      setStreamKey(data.key);
      say(streamKeyMsg, "New key made. Paste it into OBS before you next go live.", true);
      loadStream();
    } else {
      say(streamKeyMsg, "Could not regenerate the key.", false);
    }
  } catch { say(streamKeyMsg, "Could not reach the server.", false); }
  btn.disabled = false;
});

// ---- the steps themselves ----

const stepObs = $("step-obs");
const stepAir = $("step-air");
const stepLink = $("step-link");

// done: true now. now: the thing to do next. wait: not yet.
function setStep(step, state) {
  step.dataset.state = state;
  const sr = step.querySelector(".step-sr");
  if (sr) sr.textContent = state === "done" ? "(done)" : state === "now" ? "(to do)" : "";
}

// The recorder, in the words step 2 uses.
function recordingWords() {
  if (stream.recording === "restarting") return ". The recorder is restarting.";
  if (stream.recording !== "ok") return ", not recording.";
  if (storageAway) {
    return ". Recording here; the library is not answering, so it is saved once it is back.";
  }
  return ", recording to the library.";
}

function renderSteps() {
  const live = !!stream.live;
  const published = !!stream.last_publish_at;
  setStep(stepObs, published ? "done" : "now");
  // On air with no publish on record means the key changed mid-broadcast: the
  // broadcast is fine, OBS just does not hold the key it will need next time.
  $("obs-note").hidden = published || !live;

  setStep(stepAir, live ? "done" : published ? "now" : "wait");
  const air = $("air-state");
  air.textContent = "";
  if (!live) {
    air.textContent = "Press Start Streaming in OBS.";
  } else {
    air.append("On air since ");
    air.appendChild(textNode("b", "", stream.since ? clockTime(stream.since) : "just now"));
    let rest = recordingWords();
    if (storagePending) {
      rest += ` ${plural(storagePending, "earlier recording")} ${storagePending === 1 ? "is" : "are"} waiting to be saved.`;
    }
    air.append(rest);
  }

  // Step 3 is true once the link has been copied on this page load.
  setStep(stepLink, linkSent ? "done" : live ? "now" : "wait");
  $("copy-link").classList.toggle("is-quiet", !live);
  $("link-note").hidden = live;
}

// ---- step 3: the watch link ----
// The plain /watch address. Anyone with an account opens it straight into the
// room, and a link preview gets the gate's live card for it.

const copyLinkBtn = $("copy-link");
const linkMsg = $("link-msg");
const linkFallback = $("link-fallback");
const linkFallbackUrl = $("link-fallback-url");

// A link the clipboard refused, in its field, selected to copy by hand.
function showFallback(box, url) {
  const input = box.querySelector("input");
  input.value = url;
  box.hidden = false;
  input.focus();
  input.select();
}

function markLinkSent() {
  linkSent = true;
  renderSteps();
}

copyLinkBtn.addEventListener("click", async () => {
  const url = `${location.origin}/watch`;
  say(linkMsg, "", true);
  linkFallback.hidden = true;
  if (await copyText(url)) {
    say(linkMsg, "Copied. Paste it into your group text.", true);
    markLinkSent();
  } else {
    say(linkMsg, "This browser would not copy it. Here it is to copy by hand.", false);
    showFallback(linkFallback, url);
  }
});
// Copied by hand from the fallback counts the same.
linkFallbackUrl.addEventListener("copy", markLinkSent);

// ---- the room: the program monitor ----
// The watch page itself, framed. Spoken to with postMessage rather than by
// changing its src, because a reload would drop its chat socket and its place
// in the stream every time the view changed.

const liveView = $("live-view");
const program = $("program");
const viewChat = $("view-chat");
const dashSound = $("dash-sound");
const VIEW_KEY = "selfstream_dash_video";
let showVideo = true;
let soundOn = false;

function tellFrame() {
  if (!liveView.contentWindow) return;
  liveView.contentWindow.postMessage({ type: "video", show: showVideo }, location.origin);
  liveView.contentWindow.postMessage({ type: "sound", on: soundOn && showVideo }, location.origin);
}

function renderSound() {
  dashSound.hidden = !stream.live || !showVideo;
  dashSound.setAttribute("aria-pressed", soundOn ? "true" : "false");
  dashSound.setAttribute("aria-label", soundOn ? "Sound is on. Mute" : "Turn the sound on");
  $("dash-sound-off").toggleAttribute("hidden", soundOn);
  $("dash-sound-on").toggleAttribute("hidden", !soundOn);
}

function setView(show, remember) {
  showVideo = show;
  program.classList.toggle("is-chat-only", !show);
  viewChat.setAttribute("aria-pressed", show ? "false" : "true");
  if (remember) {
    try { localStorage.setItem(VIEW_KEY, show ? "full" : "chat"); } catch (e) {}
  }
  renderSound();
  tellFrame();
}

function setUpProgram() {
  let saved = "full";
  try { saved = localStorage.getItem(VIEW_KEY) || "full"; } catch (e) {}
  setView(saved !== "chat", false);
  viewChat.addEventListener("click", () => setView(!showVideo, true));
  dashSound.addEventListener("click", () => {
    soundOn = !soundOn;
    renderSound();
    tellFrame();
  });
  // The frame starts on its own defaults, so tell it again once it has loaded
  // and after any reload of its own.
  liveView.addEventListener("load", () => {
    tellFrame();
    // The frame's own chat socket puts the operator in the room; ask again
    // once it has had a moment, so they show on the board without waiting a
    // poll.
    setTimeout(loadStream, 2500);
  });
}

// ---- the room: the slate (tonight's title, and the game) ----

const slateTitle = $("slate-title");
const slateSub = $("slate-sub");
const slateForm = $("slate-form");
const slateEdit = $("slate-edit");
const titleInput = $("onair-title");
const gameInput = $("game-input");
const onairMsg = $("onair-msg");
let savedTitle = "";
let savedGame = "";

function renderSlate() {
  slateTitle.textContent = savedTitle || "Live Stream";
  slateSub.textContent = savedGame ? `Playing ${savedGame}` : "No game";
}

function openSlateForm(open) {
  slateForm.hidden = !open;
  slateEdit.setAttribute("aria-expanded", open ? "true" : "false");
  if (open) {
    titleInput.value = savedTitle;
    gameInput.value = savedGame;
    titleInput.focus();
  }
}

// One request for both lines. An empty game is a real value (it clears the
// label); an empty title is refused, so only a title with words goes up.
async function saveOnAir(body) {
  say(onairMsg, "", true);
  try {
    const { ok, data } = await postJSON("/api/stream-info", body);
    if (!ok) { say(onairMsg, data.error || "Could not save that.", false); return; }
    if (body.title !== undefined) savedTitle = body.title;
    if (body.game !== undefined) savedGame = body.game;
    renderSlate();
    openSlateForm(false);
    say(onairMsg, body.game === "" ? "Saved. No game showing." : "Saved.", true);
    loadStream();                     // refreshes the remembered games
  } catch { say(onairMsg, "Could not reach the server.", false); }
}

slateEdit.addEventListener("click", () => openSlateForm(slateForm.hidden));
$("slate-cancel").addEventListener("click", () => { openSlateForm(false); say(onairMsg, "", true); });
slateForm.addEventListener("submit", (e) => {
  e.preventDefault();
  saveOnAir({ title: titleInput.value.trim(), game: gameInput.value.trim() });
});
// Clears the game on its own, so it cannot trip over an empty title box.
$("game-none").addEventListener("click", () => {
  gameInput.value = "";
  saveOnAir({ game: "" });
});

function renderGameOptions(names) {
  const options = $("game-options");
  options.textContent = "";
  (names || []).forEach((name) => {
    const option = document.createElement("option");
    option.value = name;
    options.appendChild(option);
  });
}

// ---- the room: the call board ----
// The same presence list the watch page draws its board from, read off the
// stream poll: the host first, then everybody in the order they arrived.

function renderBoard() {
  const viewers = (stream.viewers || []).slice()
    .sort((a, b) => (b.admin ? 1 : 0) - (a.admin ? 1 : 0));
  const lines = $("dash-lines");
  lines.textContent = "";
  viewers.forEach((v) => {
    const item = document.createElement("li");
    const line = document.createElement("span");
    line.className = "line";
    if (v.admin) line.classList.add("host");
    const you = me && v.username === me.username;
    line.textContent = you ? "You" : v.name;
    const label = `${v.name}${you ? " (you)" : ""}${v.admin ? ", host" : ""}`;
    line.title = label;
    line.setAttribute("aria-label", label);
    item.appendChild(line);
    lines.appendChild(item);
  });
  $("board-count").textContent = String(viewers.length);
  $("board-empty").hidden = viewers.length > 0;
}

// ---- the room: the readout ----

const roomLimit = $("room-limit");
const limitForm = $("limit-form");
const limitEdit = $("limit-edit");
const roomLimitMsg = $("room-limit-msg");

function sentLabel(bytes) {
  if (bytes >= 1024 ** 3) return `${(bytes / 1024 ** 3).toFixed(1)} GB`;
  return `${Math.round(bytes / 1024 ** 2)} MB`;
}

function renderReadout() {
  const watching = typeof stream.video_watchers === "number" ? stream.video_watchers : 0;
  const limit = typeof stream.max_viewers === "number" ? stream.max_viewers : 0;
  $("ro-watching").textContent = String(watching);
  $("ro-limit").textContent = limit > 0 ? String(limit) : "None";
  $("ro-sent").textContent = sentLabel(stream.sent_bytes || 0);
}

function openLimitForm(open) {
  limitForm.hidden = !open;
  limitEdit.setAttribute("aria-expanded", open ? "true" : "false");
  if (open) {
    roomLimit.value = String(stream.max_viewers || 0);
    roomLimit.focus();
    roomLimit.select();
  }
}

limitEdit.addEventListener("click", () => openLimitForm(limitForm.hidden));
$("limit-cancel").addEventListener("click", () => openLimitForm(false));
limitForm.addEventListener("submit", async (e) => {
  e.preventDefault();
  say(roomLimitMsg, "", true);
  const raw = Number.parseInt(roomLimit.value, 10);
  if (!Number.isFinite(raw) || raw < 0) {
    say(roomLimitMsg, "That has to be a whole number, 0 or more.", false);
    return;
  }
  try {
    const { ok, data } = await postJSON("/api/stream-info", { max_viewers: raw });
    if (!ok) { say(roomLimitMsg, data.error || "Could not save that.", false); return; }
    stream.max_viewers = raw;
    renderReadout();
    openLimitForm(false);
    say(roomLimitMsg, raw === 0 ? "Saved. No limit." : `Saved. ${raw} at a time.`, true);
    loadStream();
  } catch { say(roomLimitMsg, "Could not reach the server.", false); }
});

// ---- the poll ----
// The lamp in the strip and the steps read the same moment: the page asks for
// the public status (the lamp, the channel's name and accent) and the admin
// stream payload (everything else) together, every ten seconds.

async function loadStream() {
  let status = null;
  try {
    const [statusReply, streamReply] = await Promise.all([
      fetch("/api/status"), fetch("/api/admin/stream"),
    ]);
    if (statusReply.ok) status = await statusReply.json();
    if (streamReply.ok) stream = await streamReply.json();
  } catch { return; }        // keep the last state rather than flash off air
  if (status && strip) strip.setStatus(status);
  renderGameOptions(stream.recent_games);
  // Not written into the slate while it is being edited.
  if (slateForm.hidden) {
    savedGame = stream.game || "";
    renderSlate();
  }
  renderSteps();
  renderBoard();
  renderReadout();
  renderSound();
  renderTheaterSubs(stream);
}

// Whether the media store is answering, for step 2's recording line. It asks
// the store itself, so it runs far less often than the stream poll.
async function loadStorageState() {
  try {
    const data = await getJSON("/api/admin/retention");
    storageAway = !!(data.usage && data.usage.available === false);
    storagePending = data.pending || 0;
  } catch { return; }
  renderSteps();
}

// =============================================================================
// Library: broadcasts and clips, and storage
// =============================================================================

let contentTab = "vods";

async function loadContent() {
  const kind = contentTab === "vods" ? "vod" : "clip";
  let items = [];
  try { items = (await getJSON(`/api/${contentTab}`))[contentTab] || []; }
  catch { items = []; }
  const list = $("content-list");
  $("content-empty").hidden = items.length > 0;
  $("content-empty").textContent = kind === "vod" ? "No broadcasts saved yet." : "No clips yet.";
  list.textContent = "";
  items.forEach((item) => {
    const { row, main, tools } = rowNode();
    const title = kind === "vod" ? item.title : item.name;
    const when = new Date((kind === "vod" ? item.started_at : item.created_at) * 1000)
      .toLocaleDateString();
    const link = document.createElement("a");
    link.className = "row-title";
    link.href = `/media?type=${kind}&id=${item.id}`;
    link.textContent = title;
    main.append(link, textNode("span", "row-meta",
      `${durationClock(item.duration)} · ${plural(item.views, "view")} · ${when}` +
      (kind === "clip" && item.creator ? ` · @${item.creator}` : "")));
    // Sharing is per clip and admin only. A whole broadcast is a much bigger
    // mistake to make public than a minute of it, so broadcasts are never
    // shared. A shared clip gets two buttons rather than a toggle: the link has
    // to stay re-copyable, and unsharing kills it for good.
    if (kind === "clip" && item.shared) {
      const copy = button("Copy link", "chip is-on", (btn) => copyShareLink(item, btn));
      copy.title = "Anyone with this link can watch. Copy it again.";
      const stop = button("Unshare", "chip danger", (btn) => shareClip(item, false, btn));
      stop.title = "Kill the public link. Sharing again makes a new one.";
      tools.append(copy, stop);
    } else if (kind === "clip") {
      const share = button("Share", "chip", (btn) => shareClip(item, true, btn));
      share.title = "Make a link anyone can watch, without an account.";
      tools.appendChild(share);
    }
    const pin = button(item.keep ? "Pinned" : "Pin", item.keep ? "chip is-on" : "chip",
      (btn) => togglePin(kind, item.id, !item.keep, btn));
    pin.setAttribute("aria-pressed", item.keep ? "true" : "false");
    pin.title = item.keep
      ? "Retention never removes this. Press to unpin."
      : "Keep this no matter what retention says.";
    tools.append(pin, button("Delete", "chip danger", (btn) => deleteContent(kind, item.id, title, btn)));
    list.appendChild(row);
  });
}

async function shareClip(item, share, btn) {
  const question = share
    ? `Share "${item.name}" publicly?\n\nAnyone with the link can watch it without an account. ` +
      "The chat replay is not included. You can stop sharing at any time."
    : `Stop sharing "${item.name}"?\n\nThe public link stops working immediately and permanently. ` +
      "Sharing again later makes a new link.";
  if (!confirm(question)) return;
  btn.disabled = true;
  try {
    const { ok, data } = await postJSON(`/api/clips/${item.id}/share`, { share });
    if (!ok) {
      alert(data.error || "Could not change sharing.");
    } else if (share && data.url) {
      const link = window.location.origin + data.url;
      if (await copyText(link)) alert(`Link copied:\n\n${link}`);
      else prompt("Share this link:", link);
    }
  } catch { alert("Could not change sharing."); }
  btn.disabled = false;
  loadContent();
}

async function copyShareLink(item, btn) {
  if (!item.share_url) return;
  const link = window.location.origin + item.share_url;
  copyWithButton(link, btn, () => prompt("Share this link:", link));
}

async function togglePin(kind, id, keep, btn) {
  btn.disabled = true;
  try {
    const { ok, data } = await postJSON(`/api/${kind}s/${id}/keep`, { keep });
    if (ok) { loadContent(); loadRetention(); return; }
    alert(data.error || "Could not change the pin.");
  } catch { alert("Could not change the pin."); }
  btn.disabled = false;
}

async function deleteContent(kind, id, title, btn) {
  if (!confirm(`Delete "${title}"? This removes the file and its chat replay.`)) return;
  btn.disabled = true;
  try {
    const reply = await fetch(`/api/${kind}s/${id}`, { method: "DELETE" });
    if (reply.ok) { loadContent(); loadRetention(); return; }
    const data = await reply.json().catch(() => ({}));
    alert(data.error || "Could not delete.");
  } catch { alert("Could not delete."); }
  btn.disabled = false;
}

document.querySelectorAll(".lib-tab[data-content]").forEach((tab) => {
  tab.addEventListener("click", () => {
    contentTab = tab.dataset.content;
    document.querySelectorAll(".lib-tab[data-content]").forEach((t) => {
      t.classList.toggle("selected", t === tab);
      t.setAttribute("aria-pressed", t === tab ? "true" : "false");
    });
    loadContent();
  });
});

// ---- storage and retention ----

const RETENTION_FIELDS = {
  "ret-vod-count": "vod_keep_count",
  "ret-vod-days": "vod_keep_days",
  "ret-clip-count": "clip_keep_count",
  "ret-clip-days": "clip_keep_days",
  "ret-cap-gb": "media_cap_gb",
};
const retMsg = $("ret-msg");

function renderRetention(data) {
  Object.entries(RETENTION_FIELDS).forEach(([id, field]) => {
    $(id).value = data[field] ?? 0;
  });
  const usage = data.usage || {};
  const counts = data.counts || {};
  // The media store may be on another machine and may be away. Say that in
  // place of the byte counts rather than show zeros that read as "empty"; the
  // counts and limits come from the database and are still true.
  const away = usage.available === false;
  storageAway = away;
  storagePending = data.pending || 0;
  const usageLine = $("storage-usage");
  usageLine.classList.toggle("is-away", away);
  const parts = [
    away ? "The media store is not answering, so its usage is unknown" : `${formatBytes(usage.total_bytes)} used`,
    `${plural(counts.vods || 0, "broadcast")}, ${plural(counts.clips || 0, "clip")}`,
  ];
  if (counts.pinned) parts.push(`${counts.pinned} pinned`);
  if (!away && usage.free_bytes) parts.push(`${formatBytes(usage.free_bytes)} free on disk`);
  usageLine.textContent = parts.join(" · ");
  // The bar is the store against the whole disk it sits on, so it answers
  // "how close am I to trouble" rather than "how close to my own cap".
  const fill = $("usage-fill");
  const capacity = usage.fs_total_bytes || 0;
  const usedShare = capacity && !away
    ? Math.min(100, ((capacity - (usage.free_bytes || 0)) / capacity) * 100)
    : 0;
  fill.style.transform = `scaleX(${usedShare / 100})`;
  fill.classList.toggle("is-tight", usedShare >= 90);
  const off = Object.values(RETENTION_FIELDS).every((field) => !data[field]);
  const state = $("retention-state");
  state.textContent = off
    ? "Retention is off. Nothing is ever deleted automatically."
    : "Retention is on. Unpinned items past these limits are deleted.";
  state.hidden = false;
  // Recordings held back because the store could not be written. Shown only
  // when there are any; the bytes are still safe on the server's own disk.
  const pending = $("storage-pending");
  const waiting = storagePending;
  pending.textContent = waiting
    ? `${plural(waiting, "recording")} ${waiting === 1 ? "is" : "are"} waiting to be saved. This retries on its own.`
    : "";
  pending.hidden = !waiting;
  renderSteps();
}

async function loadRetention() {
  try { renderRetention(await getJSON("/api/admin/retention")); }
  catch { /* leave the panel as it was */ }
}

$("ret-save").addEventListener("click", async () => {
  say(retMsg, "", true);
  const body = {};
  Object.entries(RETENTION_FIELDS).forEach(([id, field]) => {
    body[field] = Number($(id).value || 0);
  });
  let result;
  try { result = await postJSON("/api/admin/retention", body); }
  catch { say(retMsg, "Could not reach the server.", false); return; }
  if (!result.ok) { say(retMsg, result.data.error || "Could not save.", false); return; }
  const removed = result.data.removed || 0;
  say(retMsg, removed ? `Saved. Removed ${plural(removed, "item")}.` : "Saved. Nothing needed removing.", true);
  renderRetention(result.data);
  loadContent();
});

// =============================================================================
// Channel: the name, the description, the accent, and go-live notifications
// =============================================================================

const chSite = $("ch-site");
const chDesc = $("ch-desc");
const chMsg = $("ch-msg");

// Picking a swatch is not the same as changing the channel: a press only marks
// it, and the accent reaches the document (and localStorage, for the next
// first paint) once Save has actually landed. A browsed-but-abandoned pick
// never restyles the page.
const ACCENTS = ["green", "amber", "blue", "ghost"];
const swatches = document.querySelectorAll("#accent-swatches .accent-swatch");
let accent = "green";

function selectAccent(value) {
  if (!ACCENTS.includes(value)) return;
  accent = value;
  swatches.forEach((s) => {
    const on = s.dataset.accent === value;
    s.classList.toggle("selected", on);
    s.setAttribute("aria-pressed", on ? "true" : "false");
  });
}

function applyAccentToDocument(value) {
  if (!ACCENTS.includes(value)) return;
  document.documentElement.dataset.accent = value;
  try { localStorage.setItem("selfstream_accent", value); } catch (e) {}
}

swatches.forEach((s) => s.addEventListener("click", () => selectAccent(s.dataset.accent)));

// Eager, whatever section is showing: it owns the accent, and it is where the
// slate's title comes from.
async function loadChannel() {
  let data = {};
  try { data = await getJSON("/api/channel"); } catch { return; }
  chSite.value = data.site_name || "";
  chDesc.value = data.description || "";
  if (slateForm.hidden) {
    savedTitle = data.title || "";
    renderSlate();
  }
  selectAccent(data.accent || "green");
  applyAccentToDocument(data.accent || "green");
}

$("ch-save").addEventListener("click", async () => {
  const siteName = chSite.value.trim();
  if (!siteName) { say(chMsg, "Site name cannot be empty.", false); return; }
  say(chMsg, "", true);
  try {
    const { ok, data } = await postJSON("/api/stream-info", {
      site_name: siteName, description: chDesc.value.trim(), accent,
    });
    if (!ok) { say(chMsg, data.error || "Could not save.", false); return; }
  } catch { say(chMsg, "Could not reach the server.", false); return; }
  applyAccentToDocument(accent);
  say(chMsg, "Saved.", true);
  loadStream();                      // the strip carries the site name
});

// ---- go-live notifications ----
// A Web Push to every device people turned on in Options. The switch saves on
// its own; the test goes only to this admin's own devices.

const notifyOn = $("notify-on");
const notifyTest = $("notify-test");
const notifyMsg = $("notify-msg");

function renderNotify(data) {
  notifyOn.setAttribute("aria-checked", data.on ? "true" : "false");
  notifyOn.disabled = !data.ready;
  notifyTest.disabled = !data.ready;
  const devices = `${plural(data.devices, "device")} on ${plural(data.accounts, "account")}`;
  let line;
  if (!data.ready) line = "Set SELFSTREAM_SITE_URL on the server to turn notifications on.";
  else if (!data.on) line = `Off, so nobody is notified. ${devices} would get it.`;
  else line = `${devices} will get it.`;
  $("notify-status").textContent = line;
}

async function loadNotify() {
  try { renderNotify(await getJSON("/api/admin/notify")); } catch { /* keep what shows */ }
}

notifyOn.addEventListener("click", async () => {
  const on = notifyOn.getAttribute("aria-checked") !== "true";
  say(notifyMsg, "", true);
  let result;
  try { result = await postJSON("/api/admin/notify", { on }); }
  catch { say(notifyMsg, "Could not reach the server.", false); return; }
  if (!result.ok) say(notifyMsg, result.data.error || "Could not save.", false);
  loadNotify();
});

notifyTest.addEventListener("click", async () => {
  say(notifyMsg, "", true);
  notifyTest.disabled = true;
  let result;
  try { result = await postJSON("/api/admin/notify/test"); }
  catch { result = null; }
  notifyTest.disabled = false;
  if (!result) { say(notifyMsg, "Could not reach the server.", false); return; }
  const data = result.data;
  if (!result.ok) say(notifyMsg, data.error || "Could not send a test.", false);
  else if (!data.devices) say(notifyMsg, "None of your devices are signed up. Turn it on in Options on your phone first.", false);
  else if (!data.failed) say(notifyMsg, `Sent to ${plural(data.sent, "device")}.`, true);
  else say(notifyMsg, `Sent to ${data.sent} of ${plural(data.devices, "device")}. The rest did not accept it.`, false);
});

// =============================================================================
// Chat rules: slow mode and the word filter
// =============================================================================

const modSlow = $("mod-slow");
const modBanned = $("mod-banned");
const modMsg = $("mod-msg");
const bannedEditor = $("banned-editor");
const bannedOpen = $("mod-banned-open");
const bannedMsg = $("mod-banned-msg");

// Entries split on newlines or commas, the way the server splits them. The
// count is only for the summary line, so it never has to agree with the
// filter about anything the filter decides.
function countBannedWords(raw) {
  return new Set(
    String(raw || "").replace(/,/g, "\n").split("\n").map((w) => w.trim().toLowerCase()).filter(Boolean)
  ).size;
}

function showBannedCount(raw) {
  $("mod-banned-label").textContent = `${plural(countBannedWords(raw), "banned word")}`;
}

async function loadModeration() {
  let data = {};
  try { data = await getJSON("/api/admin/moderation"); } catch { return; }
  modSlow.value = data.slow_mode_seconds != null ? data.slow_mode_seconds : 0;
  modBanned.value = data.banned_words || "";
  showBannedCount(modBanned.value);
}

bannedOpen.addEventListener("click", async () => {
  const open = bannedEditor.hidden;
  if (open) {
    // Opening always shows what is saved, so an abandoned edit does not come
    // back from the dead.
    say(bannedMsg, "", true);
    await loadModeration();
  }
  bannedEditor.hidden = !open;
  bannedOpen.textContent = open ? "Hide the list" : "Show the list";
  bannedOpen.setAttribute("aria-expanded", open ? "true" : "false");
  if (open) modBanned.focus();
});

$("mod-banned-save").addEventListener("click", async () => {
  say(bannedMsg, "", true);
  // Only the list goes up: the endpoint changes just the fields it is given,
  // so this cannot quietly save an unsaved slow mode value too.
  try {
    const { ok, data } = await postJSON("/api/admin/moderation", { banned_words: modBanned.value });
    if (!ok) { say(bannedMsg, data.error || "Could not save.", false); return; }
  } catch { say(bannedMsg, "Could not reach the server.", false); return; }
  showBannedCount(modBanned.value);
  say(bannedMsg, "Saved.", true);
});

$("mod-save").addEventListener("click", async () => {
  const slow = parseInt(modSlow.value, 10);
  if (Number.isNaN(slow) || slow < 0) {
    say(modMsg, "Slow mode must be a whole number of seconds.", false);
    return;
  }
  say(modMsg, "", true);
  try {
    // Slow mode only, for the same reason the list saves alone.
    const { ok, data } = await postJSON("/api/admin/moderation", { slow_mode_seconds: slow });
    if (!ok) { say(modMsg, data.error || "Could not save.", false); return; }
  } catch { say(modMsg, "Could not reach the server.", false); return; }
  say(modMsg, slow ? `Saved. ${plural(slow, "second")} between messages.` : "Saved. Slow mode is off.", true);
});

// =============================================================================
// Connections: the overlay, and theater with its projector
// =============================================================================

const overlayUrl = $("overlay-url");
const overlayMsg = $("overlay-msg");

function setOverlayUrl(key) {
  // The origin, so the URL is ready to paste into OBS.
  overlayUrl.value = `${window.location.origin}/overlay?key=${key}`;
}

async function loadOverlay() {
  try {
    const data = await getJSON("/api/admin/overlay");
    if (data.key) setOverlayUrl(data.key);
  } catch { /* leave the field blank */ }
}

wireShow($("overlay-show"), overlayUrl);
$("overlay-copy").addEventListener("click", (e) => copyWithButton(
  overlayUrl.value, e.currentTarget, () => {
    overlayUrl.type = "text";
    overlayUrl.select();
    say(overlayMsg, "Copy failed; the URL is selected so you can copy it.", false);
  }));

// One synthetic event to any connected overlay, so the operator can see the
// OBS browser source is wired up. It goes to overlay sockets only, never to
// real chat or the chat log.
document.querySelectorAll("[data-overlay-test]").forEach((btn) => {
  btn.addEventListener("click", async () => {
    const kind = btn.dataset.overlayTest;
    say(overlayMsg, "", true);
    try {
      const { ok } = await postJSON("/api/admin/overlay/test", { kind });
      say(overlayMsg, ok ? `Sent a test ${kind} to the overlay.` : "Could not send the test.", ok);
    } catch { say(overlayMsg, "Could not reach the server.", false); }
  });
});

$("overlay-regen").addEventListener("click", async (e) => {
  if (!confirm("Regenerate the overlay URL? The current one stops working, in OBS too, until you paste the new one.")) return;
  const btn = e.currentTarget;
  btn.disabled = true;
  say(overlayMsg, "", true);
  try {
    const { ok, data } = await postJSON("/api/admin/overlay/regenerate");
    if (ok && data.key) {
      setOverlayUrl(data.key);
      say(overlayMsg, "New URL made. Update your OBS browser source.", true);
    } else {
      say(overlayMsg, "Could not regenerate the URL.", false);
    }
  } catch { say(overlayMsg, "Could not reach the server.", false); }
  btn.disabled = false;
});

// ---- theater and the projector ----
// The session controls, and the key the projector signs in with. The key is
// handled like the stream key because it is the same kind of secret.

const theaterStatus = $("theater-status");
const theaterStart = $("theater-start");
const theaterEnd = $("theater-end");
const theaterStop = $("theater-stop");
const theaterNoSubs = $("theater-nosubs");
const theaterQuery = $("theater-query");
const theaterSubs = $("theater-subs");
const theaterSubsDefault = $("theater-subs-default");
const theaterResults = $("theater-results");
const theaterMsg = $("theater-msg");
const projectorStatus = $("projector-status");
const projectorKey = $("projector-key");
const projectorMsg = $("projector-msg");

let theaterActive = false;

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
  // Only worth offering while there is a title to put back on.
  theaterNoSubs.hidden = !theaterActive || !now;
}

// The channel's subtitle default, put in the boxes ONCE, from the first poll
// that carries it. The stream poll runs every ten seconds, and putting the
// default back each time would undo a per-play override mid-session.
let subsDefaultApplied = false;

function renderTheaterSubs(data) {
  if (subsDefaultApplied || typeof data.theater_subtitles !== "boolean") return;
  subsDefaultApplied = true;
  theaterSubsDefault.checked = data.theater_subtitles;
  theaterSubs.checked = data.theater_subtitles;
}

function renderProjector(data) {
  if (!data.has_key) {
    projectorStatus.textContent = "No key yet. Regenerate to make one, then give it to the projector.";
  } else if (data.connected) {
    projectorStatus.textContent = "Connected.";
  } else if (data.last_seen) {
    projectorStatus.textContent = `Not connected. Last seen ${formatStamp(data.last_seen)}.`;
  } else {
    projectorStatus.textContent = "Not connected.";
  }
  if (data.key !== undefined) projectorKey.value = data.key || "";
}

// The server says in /api/me whether theater is on. Off, its routes answer
// 404, so the panels are not asked for at all.
const theaterEnabled = () => !me || me.theater !== false;

async function loadProjector() {
  if (!theaterEnabled()) return;
  try {
    const reply = await fetch("/api/admin/theater/projector");
    if (reply.ok) {
      $("projector-panel").hidden = false;
      renderProjector(await reply.json());
    }
  } catch { /* keep the last state rather than flash disconnected */ }
}

async function loadTheater() {
  if (!theaterEnabled()) return;
  try {
    const reply = await fetch("/api/theater");
    if (reply.ok) {
      $("theater-panel").hidden = false;
      renderTheater(await reply.json());
    }
  } catch { /* same */ }
}

// Every control answers with the same state payload, so one path applies it.
async function theaterAction(path, body) {
  say(theaterMsg, "", true);
  try {
    const { ok, status, data } = await postJSON(path, body);
    if (!ok) {
      say(theaterMsg, status === 502 ? "The projector is not connected." : (data.error || "Could not do that."), false);
      return null;
    }
    renderTheater(data);
    loadProjector();
    return data;
  } catch {
    say(theaterMsg, "Could not reach the server.", false);
    return null;
  }
}

// The rows live in theater-picker.js, shared with the watch page's host modal
// so the two cannot drift. What stays here is where a play comes from and
// where a message goes.
let lastResults = [];

function pickerOptions() {
  return {
    onPlay: async (item) => {
      const done = await theaterAction("/api/admin/theater/play", {
        jf_id: item.jf_id, subtitles: theaterSubs.checked,
      });
      if (done) say(theaterMsg, `Playing "${item.title}".`, true);
      return !!done;
    },
    onBack: () => renderTheaterResults(lastResults),
    message: (text, ok) => say(theaterMsg, text, ok),
  };
}

function renderTheaterResults(results) {
  lastResults = results;
  theaterPicker.render(theaterResults, results, pickerOptions());
}

$("theater-search").addEventListener("click", async () => {
  const query = theaterQuery.value.trim();
  if (query.length < 2) { say(theaterMsg, "Search for at least two characters.", false); return; }
  say(theaterMsg, "", true);
  try {
    const reply = await fetch(`/api/admin/theater/search?q=${encodeURIComponent(query)}`);
    const data = await reply.json().catch(() => ({}));
    if (!reply.ok) {
      say(theaterMsg, reply.status === 502 ? "The projector is not connected." : (data.error || "Could not search."), false);
      return;
    }
    renderTheaterResults(data.results || []);
    if (!(data.results || []).length) say(theaterMsg, "Nothing matched.", false);
  } catch { say(theaterMsg, "Could not reach the server.", false); }
});

theaterQuery.addEventListener("keydown", (e) => {
  if (e.key === "Enter") $("theater-search").click();
});

theaterStart.addEventListener("click", async () => {
  if (await theaterAction("/api/admin/theater/session")) {
    say(theaterMsg, "Session started. Viewers see the intermission card.", true);
  }
});

theaterStop.addEventListener("click", () => theaterAction("/api/admin/theater/stop"));

// One press, no confirmation: the room is watching subtitles run out of sync
// while it takes, and the worst case is the same film from the start.
theaterNoSubs.addEventListener("click", async () => {
  if (await theaterAction("/api/admin/theater/restart")) {
    say(theaterMsg, "Restarted without subtitles.", true);
  }
});

theaterSubsDefault.addEventListener("change", async () => {
  const on = theaterSubsDefault.checked;
  try {
    const { ok } = await postJSON("/api/stream-info", { theater_subtitles: on });
    if (!ok) {
      theaterSubsDefault.checked = !on;
      say(theaterMsg, "Could not save that.", false);
      return;
    }
    theaterSubs.checked = on;
    say(theaterMsg, on ? "Subtitles on by default." : "Subtitles off by default.", true);
  } catch {
    theaterSubsDefault.checked = !on;
    say(theaterMsg, "Could not reach the server.", false);
  }
});

theaterEnd.addEventListener("click", async () => {
  if (!confirm(
    "End the theater session? Whatever is playing stops and the room goes back " +
    "to the ordinary broadcast. Chat is kept."
  )) return;
  if (await theaterAction("/api/admin/theater/end")) say(theaterMsg, "Session ended.", true);
});

wireShow($("projector-show"), projectorKey);
$("projector-copy").addEventListener("click", (e) => copyWithButton(
  projectorKey.value, e.currentTarget, () => {
    projectorKey.type = "text";
    projectorKey.select();
    say(projectorMsg, "Copy failed; the key is selected so you can copy it.", false);
  }));

$("projector-regen").addEventListener("click", async (e) => {
  if (!confirm(
    "Regenerate the projector key? The projector disconnects at once and will " +
    "not come back until it has the new key."
  )) return;
  const btn = e.currentTarget;
  btn.disabled = true;
  say(projectorMsg, "", true);
  try {
    const { ok, data } = await postJSON("/api/admin/theater/projector/key");
    if (ok) {
      renderProjector(data);
      say(projectorMsg, "New key made. Update the projector's settings.", true);
    } else {
      say(projectorMsg, "Could not regenerate the key.", false);
    }
  } catch { say(projectorMsg, "Could not reach the server.", false); }
  btn.disabled = false;
});

// =============================================================================
// People: accounts, bans and invite codes
// =============================================================================

let users = [];
let editing = null;           // the username open in the edit view

const usersModal = $("users-modal");
const usersTitle = $("users-modal-title");

function openUsers(view, title) {
  usersModal.querySelectorAll(".users-view").forEach((v) => {
    v.hidden = v.dataset.view !== view;
  });
  usersTitle.textContent = title;
  usersModal.hidden = false;
}

function closeUsers() { usersModal.hidden = true; }

async function loadUsers() {
  try { users = (await getJSON("/api/admin/users")).users || []; }
  catch { return; }
  renderUsers();
}

function roleBadge(text) { return textNode("span", "role-badge", text); }

function renderUsers() {
  const list = $("user-list");
  $("user-empty").hidden = users.length > 0;
  $("acct-count").textContent = users.length ? `(${users.length})` : "";
  list.textContent = "";
  users.forEach((u) => {
    const { row, main, tools } = rowNode("person-row");
    row.insertBefore(avatarNode(u.username, u.display_name, u.avatar_version), main);
    const name = document.createElement("span");
    name.className = "row-title";
    name.textContent = u.display_name;
    if (u.is_admin) name.appendChild(roleBadge("admin"));
    if (u.is_moderator) name.appendChild(roleBadge("mod"));
    main.append(name, textNode("span", "row-meta",
      `@${u.username} · seen ${relativeTime(u.last_seen)} · ${formatDuration(u.watch_seconds)} watched · ${u.messages} msg`));
    tools.append(
      button("Edit", "chip", () => openEdit(u)),
      button("Activity", "chip", () => openActivity(u)),
    );
    list.appendChild(row);
  });
}

// ---- create ----

const createForm = $("create-form");
const cError = $("c-error");

$("user-new").addEventListener("click", () => {
  createForm.reset();
  cError.hidden = true;
  openUsers("create", "New account");
  $("c-username").focus();
});

createForm.addEventListener("submit", async (e) => {
  e.preventDefault();
  cError.hidden = true;
  const body = {
    username: $("c-username").value,
    display_name: $("c-name").value,
    password: $("c-password").value,
    is_admin: $("c-admin").checked,
    is_moderator: $("c-mod").checked,
  };
  try {
    const { ok, data } = await postJSON("/api/admin/users", body);
    if (ok) { closeUsers(); loadUsers(); return; }
    cError.textContent = data.error || "Could not create the account.";
  } catch { cError.textContent = "Could not reach the server."; }
  cError.hidden = false;
});

// ---- edit ----
// No display name: an admin picks the starting name when making the account,
// and after that it belongs to the account holder. The server refuses it too.

const editForm = $("edit-form");
const eError = $("e-error");
const eAdmin = $("e-admin");

function openEdit(user) {
  editing = user.username;
  $("e-password").value = "";
  eAdmin.checked = !!user.is_admin;
  $("e-mod").checked = !!user.is_moderator;
  eError.hidden = true;
  openUsers("edit", `Edit @${user.username}`);
}

editForm.addEventListener("submit", async (e) => {
  e.preventDefault();
  eError.hidden = true;
  const body = {
    is_admin: eAdmin.checked,
    is_moderator: $("e-mod").checked,
  };
  const pw = $("e-password").value;
  if (pw) body.password = pw;
  try {
    const reply = await fetch(`/api/admin/users/${encodeURIComponent(editing)}`, {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    if (reply.ok) { closeUsers(); loadUsers(); return; }
    const data = await reply.json().catch(() => ({}));
    eError.textContent = data.error || "Could not save changes.";
  } catch { eError.textContent = "Could not reach the server."; }
  eError.hidden = false;
});

// ---- delete ----
// An account goes with its watch history and its chat, and there is no undo,
// so the only way through is typing the username. The server asks for the
// same thing, so a mis-wired button cannot delete anyone either.

const deleteForm = $("delete-form");
const dConfirm = $("d-confirm");
const dGo = $("d-go");
const dError = $("d-error");

// Held apart from `editing`, and read only here, so whatever the edit view
// does afterwards this flow can only delete the account it was opened on.
let deleting = null;

$("e-delete").addEventListener("click", () => {
  deleting = editing;
  $("d-blurb").textContent =
    `This removes @${deleting}, their watch history and their chat. It cannot be undone.`;
  dConfirm.value = "";
  dGo.disabled = true;
  dError.hidden = true;
  openUsers("delete", `Delete @${deleting}`);
  dConfirm.focus();
});

$("d-back").addEventListener("click", () => {
  const user = users.find((u) => u.username === deleting);
  if (user) openEdit(user);
  else closeUsers();
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
      closeUsers();
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

const aWatch = $("a-watch");
const aChat = $("a-chat");

function activityRow(when, text, cls) {
  const row = document.createElement("li");
  row.className = "row activity-row";
  row.append(textNode("span", "act-when", when), textNode("span", cls || "act-text", text));
  return row;
}

function switchActivityTab(which) {
  document.querySelectorAll(".activity-tabs .tab").forEach((t) => {
    const on = t.dataset.tab === which;
    t.classList.toggle("selected", on);
    t.setAttribute("aria-pressed", on ? "true" : "false");
  });
  aWatch.hidden = which !== "watch";
  aChat.hidden = which !== "chat";
}

document.querySelectorAll(".activity-tabs .tab").forEach((t) => {
  t.addEventListener("click", () => switchActivityTab(t.dataset.tab));
});

async function openActivity(user) {
  aWatch.textContent = "";
  aWatch.appendChild(textNode("li", "empty", "Loading…"));
  aChat.textContent = "";
  switchActivityTab("watch");
  openUsers("activity", `Activity · @${user.username}`);

  let data = { watch_sessions: [], chat: [] };
  try {
    data = await getJSON(`/api/admin/users/${encodeURIComponent(user.username)}/activity`);
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

// ---- bans (the same endpoints the moderation page uses) ----

let bans = [];

async function loadBans() {
  try { bans = (await getJSON("/api/mod/bans")).bans || []; }
  catch { bans = []; }
  renderBans();
}

function renderBans() {
  const list = $("ban-list");
  $("ban-empty").hidden = bans.length > 0;
  $("ban-count").textContent = bans.length ? `(${bans.length})` : "";
  list.textContent = "";
  bans.forEach((b) => {
    const { row, main, tools } = rowNode();
    const by = b.banned_by_name || b.banned_by;
    main.append(
      textNode("span", "row-title", `${b.display_name || b.username} @${b.username}`),
      textNode("span", "row-meta", `banned by ${by}${b.reason ? ` · ${b.reason}` : ""}`),
    );
    tools.appendChild(button("Lift the ban", "chip", (btn) => unban(b.username, btn)));
    list.appendChild(row);
  });
}

async function unban(username, btn) {
  btn.disabled = true;
  try {
    const { ok, data } = await postJSON("/api/mod/unban", { username });
    if (ok) { loadBans(); return; }
    alert(data.error || "Could not lift the ban.");
  } catch { alert("Could not lift the ban."); }
  btn.disabled = false;
}

// ---- invite codes ----
// An invite link is /join#<code>. The code rides after the #, which a browser
// never sends, so it never reaches a server log or a link preview.

let invites = [];
const inviteMsg = $("invite-msg");

function inviteLink(code) {
  return `${location.origin}/join#${encodeURIComponent(code)}`;
}

async function copyInviteLink(code, btn) {
  const link = inviteLink(code);
  say(inviteMsg, "", true);
  $("invite-fallback").hidden = true;
  if (await copyWithButton(link, btn)) {
    say(inviteMsg, "Copied. Send it to the one person it is for.", true);
  } else {
    say(inviteMsg, "This browser would not copy it. Here it is to copy by hand.", false);
    showFallback($("invite-fallback"), link);
  }
}

async function loadInvites() {
  try { invites = (await getJSON("/api/admin/invites")).invites || []; }
  catch { invites = []; }
  renderInvites();
}

function inviteStatus(inv) {
  if (inv.redeemed_at) {
    const who = inv.redeemed_by_name || inv.redeemed_by || "someone";
    return { text: `used by ${who} · ${new Date(inv.redeemed_at * 1000).toLocaleDateString()}`, active: false };
  }
  if (inv.revoked_at) return { text: "revoked", active: false };
  return { text: "active", active: true };
}

function renderInvites() {
  const list = $("invite-list");
  $("invite-empty").hidden = invites.length > 0;
  list.textContent = "";
  invites.forEach((inv) => {
    const status = inviteStatus(inv);
    const { row, main, tools } = rowNode(status.active ? "" : "is-spent");
    main.append(
      textNode("span", "row-title code", inv.code),
      textNode("span", "row-meta", (inv.label ? `${inv.label} · ` : "") + status.text),
    );
    tools.appendChild(button("Copy", "chip", (btn) => copyWithButton(inv.code, btn, () => {
      say(inviteMsg, `This browser would not copy it. The code is ${inv.code}.`, false);
    })));
    if (status.active) {
      tools.appendChild(button("Copy link", "chip", (btn) => copyInviteLink(inv.code, btn)));
      tools.appendChild(button("Revoke", "chip danger", (btn) => revokeInvite(inv.code, btn)));
    } else {
      // A spent code is the only kind that can be removed; an active one has
      // to be revoked first.
      tools.appendChild(button("Remove", "chip danger", (btn) => removeInvite(inv.code, btn)));
    }
    list.appendChild(row);
  });
  $("invite-clear-used").hidden = !invites.some((i) => i.redeemed_at || i.revoked_at);
}

async function revokeInvite(code, btn) {
  if (!confirm(`Revoke ${code}? It can no longer be used.`)) return;
  btn.disabled = true;
  say(inviteMsg, "", true);
  try {
    const reply = await fetch(`/api/admin/invites/${encodeURIComponent(code)}`, { method: "DELETE" });
    if (reply.ok) { loadInvites(); return; }
    const data = await reply.json().catch(() => ({}));
    say(inviteMsg, data.error || "Could not revoke the code.", false);
  } catch { say(inviteMsg, "Could not reach the server.", false); }
  btn.disabled = false;
}

async function removeInvite(code, btn) {
  if (!confirm(`Remove ${code} from the list? Any account it made is not affected.`)) return;
  btn.disabled = true;
  say(inviteMsg, "", true);
  try {
    const { ok, data } = await postJSON(`/api/admin/invites/${encodeURIComponent(code)}/remove`);
    if (ok) { loadInvites(); return; }
    say(inviteMsg, data.error || "Could not remove the code.", false);
  } catch { say(inviteMsg, "Could not reach the server.", false); }
  btn.disabled = false;
}

$("invite-clear-used").addEventListener("click", async (e) => {
  const btn = e.currentTarget;
  if (!confirm("Remove every used and revoked invite code? The accounts they made are not affected.")) return;
  btn.disabled = true;
  say(inviteMsg, "", true);
  try {
    const { ok, data } = await postJSON("/api/admin/invites/clear-used");
    if (!ok) say(inviteMsg, data.error || "Could not clear the codes.", false);
  } catch { say(inviteMsg, "Could not reach the server.", false); }
  btn.disabled = false;
  loadInvites();
});

$("invite-new").addEventListener("click", async (e) => {
  const btn = e.currentTarget;
  btn.disabled = true;
  say(inviteMsg, "", true);
  try {
    const { ok, data } = await postJSON("/api/admin/invites", { label: $("invite-label").value });
    if (ok) {
      $("invite-label").value = "";
      $("invite-made-code").textContent = data.code;
      $("invite-made").hidden = false;
      $("invite-fallback").hidden = true;
      loadInvites();
    } else {
      say(inviteMsg, data.error || "Could not generate a code.", false);
    }
  } catch { say(inviteMsg, "Could not reach the server.", false); }
  btn.disabled = false;
});

$("invite-made-copy").addEventListener("click", (e) => {
  copyInviteLink($("invite-made-code").textContent, e.currentTarget);
});

// =============================================================================
// The sections
// =============================================================================
// One section at a time. A section's data is fetched the first time it is
// shown, so opening the dashboard costs the Go live screen and nothing else.
// The dashboard always opens on Go live (a link to /admin#people lands on
// People). Older links name the tabs this replaced, and still land.

const SECTIONS = [
  { key: "golive", label: "Go live", href: "/admin#golive" },
  { key: "people", label: "People", href: "/admin#people" },
  { key: "library", label: "Library", href: "/admin#library" },
  { key: "channel", label: "Channel", href: "/admin#channel" },
  { key: "chat", label: "Chat rules", href: "/admin#chat" },
  { key: "connections", label: "Connections", href: "/admin#connections" },
  { key: "stats", label: "Stats", href: "/analytics" },
];

const OLD_NAMES = { broadcast: "golive", content: "library" };

const LOADERS = {
  golive: [loadStreamKey, loadStorageState],
  people: [loadUsers, loadBans, loadInvites],
  library: [loadContent, loadRetention],
  channel: [loadNotify],
  chat: [loadModeration],
  connections: [loadOverlay, loadTheater, loadProjector],
};

const loaded = new Set();
let current = "golive";

function sectionFromHash() {
  const name = (location.hash || "").replace("#", "");
  return OLD_NAMES[name] || name;
}

function showSection(name, moveFocus) {
  if (!LOADERS[name]) name = "golive";
  current = name;
  document.querySelectorAll(".dash-section").forEach((section) => {
    section.hidden = section.dataset.panel !== name;
  });
  document.body.dataset.section = name;
  if (strip && strip.setSection) strip.setSection(name);
  if (!loaded.has(name)) {
    loaded.add(name);
    LOADERS[name].forEach((load) => load());
  }
  if (moveFocus) {
    window.scrollTo(0, 0);
    const heading = document.querySelector(`.dash-section[data-panel="${name}"] h1`);
    if (heading) heading.focus({ preventScroll: true });
  }
}

async function loadVersion() {
  try {
    const data = await getJSON("/api/status");
    if (data.version) $("version-line").textContent = `upperroom v${data.version}`;
  } catch { /* leave it blank rather than show a stale guess */ }
}

async function boot() {
  if (!(await requireAdmin())) return;
  const start = LOADERS[sectionFromHash()] ? sectionFromHash() : "golive";
  // The page polls /api/status itself, alongside the admin payload, so the
  // lamp and the steps always describe the same moment.
  strip = mountNav(me, {
    current: "dashboard", sections: SECTIONS, section: start, poll: false, pageName: "Dashboard",
  });
  setUpProgram();
  loadVersion();
  loadChannel();
  loadStream();
  setInterval(loadStream, 10000);
  // The store only matters on Go live, and only while it shows.
  setInterval(() => { if (current === "golive") loadStorageState(); }, 60000);
  // Only while a session is open: the state moves on its own then (a title
  // ending puts the room back to intermission).
  setInterval(() => {
    if (!theaterActive) return;
    loadTheater();
    loadProjector();
  }, 10000);
  window.addEventListener("hashchange", () => showSection(sectionFromHash(), true));
  showSection(start, false);
}

boot();
