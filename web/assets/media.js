// Playback page for a saved VOD or a clip, with chat replayed in sync. Driven by
// the query string, e.g. /media?type=vod&id=12 or /media?type=clip&id=5.
let me = null;               // this browser's identity, for the shared nav


const params = new URLSearchParams(location.search);
const TYPE = params.get("type") === "clip" ? "clip" : "vod";
const ID = parseInt(params.get("id"), 10);

const video = document.getElementById("video");
const titleEl = document.getElementById("media-title");
const subEl = document.getElementById("media-sub");
const descEl = document.getElementById("media-desc");
const replayMessages = document.getElementById("replay-messages");
const replayEmpty = document.getElementById("replay-empty");
const replayToggle = document.getElementById("replay-toggle");
const heatmap = document.getElementById("heatmap");

let replay = [];        // chat lines, sorted by offset_s
let shownIdx = 0;       // how many have been revealed
let lastT = 0;
let replayOn = true;
let viewCounted = false;
let hmPlayhead = null;   // the moving marker, once the strip is built
let hmDuration = 0;      // media length the strip was bucketed against
let viewSuffix = "";     // the "· when · clipped by" tail after the view count

// ---- shared render helpers (kept local to this page) ----

const FONTS = {
  system: "",
  mono: "'Roboto Mono', monospace",
  comic: "'Comic Neue', cursive",
  retro: "'VT323', monospace",
  caveat: "'Caveat', cursive",
};

function avatarColor(seed) {
  let hash = 0;
  for (let i = 0; i < seed.length; i++) hash = (hash * 31 + seed.charCodeAt(i)) % 360;
  return `hsl(${hash}, 55%, 45%)`;
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

// The host (admin) shows a bright-red video-camera icon; a moderator shows a
// small blue "mod" tag. Matches the live chat marks.
const CAMERA_SVG =
  '<svg viewBox="0 0 24 24" width="13" height="13" fill="currentColor" aria-hidden="true">' +
  '<path d="M3 7.5A1.5 1.5 0 0 1 4.5 6h9A1.5 1.5 0 0 1 15 7.5v9A1.5 1.5 0 0 1 13.5 18h-9' +
  'A1.5 1.5 0 0 1 3 16.5v-9Zm14 3 3.25-2.17a.6.6 0 0 1 .95.5v6.34a.6.6 0 0 1-.95.5L17 13.5v-3Z"/></svg>';

function roleBadgeNode(admin, mod) {
  if (!admin && !mod) return null;
  const span = document.createElement("span");
  if (admin) {
    span.className = "role-tag host";
    span.title = "Broadcaster";
    span.innerHTML = CAMERA_SVG;   // a static, trusted icon; no user data
  } else {
    span.className = "role-tag mod";
    span.title = "Moderator";
    span.textContent = "mod";
  }
  return span;
}

function clock(secs) {
  secs = Math.max(0, Math.floor(secs || 0));
  const m = Math.floor(secs / 60);
  const s = secs % 60;
  return `${m}:${String(s).padStart(2, "0")}`;
}

function relDate(epoch) {
  if (!epoch) return "";
  const secs = Math.floor(Date.now() / 1000) - epoch;
  if (secs < 3600) return `${Math.max(1, Math.floor(secs / 60))}m ago`;
  if (secs < 86400) return `${Math.floor(secs / 3600)}h ago`;
  if (secs < 2592000) return `${Math.floor(secs / 86400)}d ago`;
  return new Date(epoch * 1000).toLocaleDateString();
}

function appendReplayLine(msg) {
  const line = document.createElement("div");
  line.className = "msg";
  line.appendChild(avatarNode(msg.username, msg.display_name, msg.avatar_version || 0));
  const badge = roleBadgeNode(msg.admin, msg.moderator);
  if (badge) line.appendChild(badge);
  const wrap = document.createElement("span");
  wrap.className = "msg-body";
  const head = document.createElement("span");
  head.className = "msg-head";
  const name = document.createElement("span");
  name.className = msg.admin ? "name admin" : "name";
  name.textContent = msg.display_name;
  // The author's chat colors were frozen into the snapshot, so the replay looks
  // the way the live chat did: their name color on the name, their message color
  // on the text. Colors were guarded for readability when they were chosen.
  if (msg.name_color) name.style.color = msg.name_color;
  const time = document.createElement("span");
  time.className = "msg-time";
  time.textContent = clock(msg.offset_s);
  head.append(name, time);
  const body = document.createElement("span");
  body.className = "body";
  if (msg.deleted) {
    body.textContent = "deleted by a moderator";
    body.classList.add("deleted");
  } else {
    body.textContent = msg.text;
    body.style.fontFamily = FONTS[msg.font] || "";
    if (msg.msg_color) body.style.color = msg.msg_color;
  }
  wrap.append(head, body);
  line.appendChild(wrap);
  replayMessages.appendChild(line);
  replayMessages.scrollTop = replayMessages.scrollHeight;
}

// ---- replay sync ----

function revealUpTo(t) {
  while (shownIdx < replay.length && replay[shownIdx].offset_s <= t) {
    appendReplayLine(replay[shownIdx]);
    shownIdx++;
  }
}

function resetTo(t) {
  replayMessages.innerHTML = "";
  shownIdx = 0;
  if (replayOn) revealUpTo(t);
}

video.addEventListener("timeupdate", () => {
  updateHeatmapPlayhead();
  if (!replayOn) return;
  const t = video.currentTime;
  if (t + 0.5 < lastT) resetTo(t);   // jumped backward
  else revealUpTo(t);
  lastT = t;
  countView();
});
video.addEventListener("seeking", () => {
  updateHeatmapPlayhead();
  if (replayOn) resetTo(video.currentTime);
});

replayToggle.addEventListener("click", () => {
  replayOn = !replayOn;
  replayToggle.textContent = replayOn ? "On" : "Off";
  if (replayOn) resetTo(video.currentTime);
  else replayMessages.innerHTML = "";
});

// ---- chat-activity heatmap ----

// Bucket the recorded chat by timestamp and draw a bar per bucket, so the
// busy moments of the stream stand out. Bail quietly if there is nothing to
// show; the strip stays hidden.
function buildHeatmap(duration) {
  if (!duration) return;
  const msgs = replay.filter((m) => !m.deleted);
  if (!msgs.length) return;

  const bucketSeconds = Math.max(2, Math.ceil(duration / 100));
  const n = Math.ceil(duration / bucketSeconds);
  const counts = new Array(n).fill(0);
  for (const m of msgs) {
    let i = Math.floor(m.offset_s / bucketSeconds);
    if (i >= n) i = n - 1;   // clamp anything at or past the end into the last bucket
    counts[i]++;
  }
  const max = Math.max(...counts);

  for (let i = 0; i < n; i++) {
    const bar = document.createElement("div");
    bar.className = "hm-bar";
    bar.style.height = counts[i] ? `${18 + (82 * counts[i]) / max}%` : "2px";
    const label = counts[i] === 1 ? "1 message" : `${counts[i]} messages`;
    bar.title = `${clock(i * bucketSeconds)} · ${label}`;
    heatmap.appendChild(bar);
  }

  hmPlayhead = document.createElement("div");
  hmPlayhead.className = "hm-playhead";
  heatmap.appendChild(hmPlayhead);

  hmDuration = duration;
  heatmap.hidden = false;
  updateHeatmapPlayhead();
}

// Slide the marker to match playback. No-op until the strip is built.
function updateHeatmapPlayhead() {
  if (!hmPlayhead || !hmDuration) return;
  const pct = Math.min(1, Math.max(0, video.currentTime / hmDuration)) * 100;
  hmPlayhead.style.left = `${pct}%`;
}

heatmap.addEventListener("click", (event) => {
  if (!hmDuration) return;
  const rect = heatmap.getBoundingClientRect();
  const t = ((event.clientX - rect.left) / rect.width) * hmDuration;
  video.currentTime = Math.min(hmDuration, Math.max(0, t));
});

// ---- view count (once per visit, after playback starts) ----

// Render the meta line for a given view count, so a fresh count from the server
// can replace the one painted at load without rebuilding the rest of the line.
function renderViews(n) {
  const views = n === 1 ? "1 view" : `${n} views`;
  subEl.textContent = views + viewSuffix;
}

async function countView() {
  if (viewCounted) return;
  viewCounted = true;
  // Counting the view returns the fresh total, so show it: the page loaded with a
  // count that did not yet include this visit, and throwing the response away left
  // it one behind until a reload.
  try {
    const reply = await fetch(`/api/${TYPE}s/${ID}/view`, { method: "POST" });
    if (reply.ok) {
      const data = await reply.json();
      if (typeof data.views === "number") renderViews(data.views);
    }
  } catch { /* ignore: a failed count just leaves the loaded number in place */ }
}

// ---- load ----

async function requireAuth() {
  let data;
  try { data = await (await fetch("/api/me")).json(); } catch { data = { authed: false }; }
  if (!data.authed) { window.location.href = "/"; return false; }
  // A guest pass buys the stream and chat, nothing else on the site.
  // Send them where their pass actually works rather than rendering a
  // page whose every request will 401.
  if (data.guest) { window.location.href = "/watch"; return false; }
  me = data;
  return true;
}

async function loadMedia() {
  let meta;
  try {
    const reply = await fetch(`/api/${TYPE}s/${ID}`);
    if (!reply.ok) throw new Error("not found");
    meta = await reply.json();
  } catch {
    titleEl.textContent = "Not found";
    subEl.textContent = "This recording is no longer available.";
    return;
  }
  titleEl.textContent = TYPE === "vod" ? meta.title : meta.name;
  const when = relDate(TYPE === "vod" ? meta.started_at : meta.created_at);
  viewSuffix = ` · ${when}`;
  if (TYPE === "clip" && meta.creator) viewSuffix += ` · clipped by @${meta.creator}`;
  renderViews(meta.views);
  if (TYPE === "vod" && meta.description) descEl.textContent = meta.description;
  if (TYPE === "clip") setUpRename(meta);
  if (TYPE === "clip" && me && me.admin) {
    shareState = { shared: !!meta.shared, url: meta.share_url, name: meta.name };
    renderShare();
  }
  video.poster = meta.poster ? `/media/${TYPE}s/${ID}.jpg` : "";
  video.src = `/media/${TYPE}s/${meta.filename}`;

  // Chat replay
  try {
    replay = (await (await fetch(`/api/${TYPE}s/${ID}/chat`)).json()).messages || [];
  } catch { replay = []; }
  if (!replay.length) {
    // No chat to replay: state the absence in place of the empty dark box rather
    // than leaving a labeled, bare panel, the same way a removed comment shows a
    // note instead of closing the gap.
    replayMessages.hidden = true;
    replayEmpty.hidden = false;
    replayToggle.hidden = true;
  }

  buildHeatmap(meta.duration);
}

// The channel accent (the brand color) is server-driven. The head bootstrap
// paints the last-seen value from localStorage; this syncs it with the server on
// load and remembers it for the next no-flash paint.
function applyAccent(value) {
  if (!["green", "amber", "blue", "ghost"].includes(value)) return;
  if (document.documentElement.dataset.accent !== value) {
    document.documentElement.dataset.accent = value;
    try { localStorage.setItem("selfstream_accent", value); } catch (e) {}
  }
}


// ---- likes and comments ---------------------------------------------------
// Accounts only, and deliberately beside the chat replay rather than inside it.
// The replay is what was said live and is frozen; this is what people say
// afterwards. The public clip page has neither, by design.

const likeBtn = document.getElementById("like-btn");
const likeCount = document.getElementById("like-count");
const commentsSection = document.getElementById("comments-section");
const commentList = document.getElementById("comment-list");
const commentEmpty = document.getElementById("comment-empty");
const commentForm = document.getElementById("comment-form");
const commentInput = document.getElementById("comment-input");
const commentMsg = document.getElementById("comment-msg");

let liked = false;
let canModerate = false;

function showCommentMsg(text, ok) {
  commentMsg.textContent = text;
  commentMsg.classList.toggle("good", !!ok);
  commentMsg.classList.toggle("bad", !ok);
  commentMsg.hidden = false;
  setTimeout(() => { commentMsg.hidden = true; }, 4000);
}

function renderComments(comments) {
  commentList.innerHTML = "";
  commentEmpty.hidden = comments.length > 0;
  comments.forEach((c) => {
    const row = document.createElement("div");
    row.className = "comment" + (c.deleted_by ? " is-deleted" : "");

    row.appendChild(avatarNode(c.username, c.display_name, c.avatar_version));

    const bubble = document.createElement("div");
    bubble.className = "comment-body";

    const head = document.createElement("div");
    head.className = "comment-head";
    const who = document.createElement("span");
    who.className = "comment-author";
    // textContent, never innerHTML: a display name is somebody else's input.
    who.textContent = c.display_name || c.username;
    if (c.name_color) who.style.color = c.name_color;
    head.appendChild(who);
    const badge = roleBadgeNode(c.is_admin, c.is_moderator);
    if (badge) head.appendChild(badge);
    const when = document.createElement("span");
    when.className = "comment-when muted";
    when.textContent = new Date(c.ts * 1000).toLocaleString([], {
      month: "short", day: "numeric", hour: "numeric", minute: "2-digit",
    });
    head.appendChild(when);
    bubble.appendChild(head);

    const text = document.createElement("div");
    text.className = "comment-text";
    if (c.deleted_by) {
      // Shown as removed rather than vanishing, so the thread does not silently
      // close the gap. Same as the chat replay.
      text.classList.add("muted");
      text.textContent = "removed";
    } else {
      text.textContent = c.text;
    }
    bubble.appendChild(text);

    // An author may remove their own; a moderator or admin may remove any.
    const mine = me && c.username === me.username;
    if (!c.deleted_by && (mine || canModerate)) {
      const del = document.createElement("button");
      del.type = "button";
      del.className = "chip-btn comment-delete";
      del.textContent = "Delete";
      del.addEventListener("click", () => removeComment(c.id, del));
      bubble.appendChild(del);
    }

    row.appendChild(bubble);
    commentList.appendChild(row);
  });
}

async function loadReactions() {
  try {
    const reply = await fetch(`/api/${TYPE}s/${ID}/reactions`);
    if (!reply.ok) return;
    const data = await reply.json();
    liked = data.liked;
    canModerate = data.can_moderate;
    likeCount.textContent = data.likes;
    likeBtn.classList.toggle("pinned-chip", liked);
    likeBtn.title = liked ? "You like this. Click to undo." : "Like this";
    likeBtn.hidden = false;
    commentsSection.hidden = false;
    renderComments(data.comments || []);
  } catch { /* leave the section hidden rather than showing a broken one */ }
}

likeBtn.addEventListener("click", async () => {
  likeBtn.disabled = true;
  try {
    const reply = await fetch(`/api/${TYPE}s/${ID}/like`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ liked: !liked }),
    });
    const data = await reply.json().catch(() => ({}));
    if (reply.ok) {
      liked = data.liked;
      likeCount.textContent = data.likes;
      likeBtn.classList.toggle("pinned-chip", liked);
      likeBtn.title = liked ? "You like this. Click to undo." : "Like this";
    }
  } catch { /* leave the count as it was */ }
  likeBtn.disabled = false;
});

commentForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  const text = commentInput.value.trim();
  if (!text) return;
  const button = commentForm.querySelector("button");
  button.disabled = true;
  try {
    const reply = await fetch(`/api/${TYPE}s/${ID}/comments`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ text }),
    });
    const data = await reply.json().catch(() => ({}));
    if (reply.ok) {
      commentInput.value = "";
      renderComments(data.comments || []);
    } else {
      showCommentMsg(data.error || "Could not post that.", false);
    }
  } catch { showCommentMsg("Could not reach the server.", false); }
  button.disabled = false;
});

async function removeComment(id, button) {
  if (!confirm("Remove this comment?")) return;
  button.disabled = true;
  try {
    const reply = await fetch(`/api/comments/${id}`, { method: "DELETE" });
    if (reply.ok) { loadReactions(); return; }
    const data = await reply.json().catch(() => ({}));
    showCommentMsg(data.error || "Could not remove it.", false);
  } catch { showCommentMsg("Could not remove it.", false); }
  button.disabled = false;
}

// ---- renaming a clip ------------------------------------------------------
// The watch page asks for a name once, straight after the cut, and a skipped or
// hurried one leaves a clip called "Clip". This is the second chance. The maker
// may rename their own; a moderator or admin may rename any. VODs are titled by
// the operator elsewhere and are not renamed here.

const renameBtn = document.getElementById("rename-btn");
const renameEdit = document.getElementById("rename-edit");
const renameInput = document.getElementById("rename-input");
const renameSave = document.getElementById("rename-save");
const renameCancel = document.getElementById("rename-cancel");

function setUpRename(meta) {
  if (!me || !(me.admin || me.mod || meta.creator === me.username)) return;
  renameBtn.hidden = false;
  renameBtn.addEventListener("click", () => {
    renameInput.value = titleEl.textContent;
    renameEdit.hidden = false;
    renameBtn.hidden = true;
    renameInput.focus();
    renameInput.select();
  });
  renameCancel.addEventListener("click", closeRename);
  renameSave.addEventListener("click", saveRename);
}

function closeRename() {
  renameEdit.hidden = true;
  renameBtn.hidden = false;
}

async function saveRename() {
  const name = renameInput.value.trim();
  if (!name) { closeRename(); return; }
  renameSave.disabled = true;
  try {
    const reply = await fetch(`/api/clips/${ID}/name`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ name }),
    });
    const data = await reply.json().catch(() => ({}));
    if (reply.ok) {
      titleEl.textContent = data.name;
      // The share confirmations name the clip, so keep them honest.
      shareState.name = data.name;
      closeRename();
    } else {
      alert(data.error || "Could not rename it.");
    }
  } catch { alert("Could not rename it."); }
  renameSave.disabled = false;
}

// ---- clip sharing (admin only) --------------------------------------------
// The link is the whole credential, so it has to be re-copyable for as long as
// the clip is shared, and stopping has to be its own button: unsharing mints a
// dead token, and a single toggle made that one stray click away. VODs are
// never shareable, and the public clip page never gets these controls.

const reactionsRow = document.getElementById("reactions");
let shareState = { shared: false, url: null, name: "" };

function chipButton(label, danger) {
  const btn = document.createElement("button");
  btn.type = "button";
  btn.className = "chip-btn" + (danger ? " danger-chip" : "");
  btn.dataset.share = "1";   // marks it for removal when the state swaps
  btn.textContent = label;
  return btn;
}

function renderShare() {
  reactionsRow.querySelectorAll("[data-share]").forEach((el) => el.remove());
  if (!shareState.shared) {
    const btn = chipButton("Share", false);
    btn.title = "Make a link anyone can watch, without an account.";
    btn.addEventListener("click", () => setShare(true, btn));
    reactionsRow.appendChild(btn);
    return;
  }
  const copy = chipButton("Copy link", false);
  copy.title = "Copy the public link again.";
  copy.addEventListener("click", () => copyShareLink(copy));
  const stop = chipButton("Stop sharing", true);
  stop.title = "Kill the public link. Sharing again makes a new one.";
  stop.addEventListener("click", () => setShare(false, stop));
  reactionsRow.append(copy, stop);
}

async function copyShareLink(btn) {
  if (!shareState.url) return;
  const link = window.location.origin + shareState.url;
  try {
    await navigator.clipboard.writeText(link);
    const was = btn.textContent;
    btn.textContent = "Copied";
    setTimeout(() => { btn.textContent = was; }, 1200);
  } catch {
    prompt("Share this link:", link);
  }
}

async function setShare(on, btn) {
  const name = shareState.name;
  if (on && !confirm(
    `Share "${name}" publicly?\n\n` +
    "Anyone with the link can watch it without an account. " +
    "The chat replay is not included. You can stop sharing at any time.")) return;
  if (!on && !confirm(
    `Stop sharing "${name}"?\n\n` +
    "The public link stops working immediately and permanently. " +
    "Sharing again later makes a new link.")) return;
  btn.disabled = true;
  try {
    const reply = await fetch(`/api/clips/${ID}/share`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ share: on }),
    });
    const data = await reply.json().catch(() => ({}));
    if (!reply.ok) {
      alert(data.error || "Could not change sharing.");
    } else {
      shareState.shared = on;
      shareState.url = on ? data.url : null;
      if (on && data.url) {
        const link = window.location.origin + data.url;
        try {
          await navigator.clipboard.writeText(link);
          alert("Link copied:\n\n" + link);
        } catch {
          prompt("Share this link:", link);
        }
      }
      renderShare();   // replaces this button, disabled and all
      return;
    }
  } catch { alert("Could not change sharing."); }
  btn.disabled = false;
}

// The operator's site name leads the top bar and names the browser tab, so the
// platform brand ("powered by upperroom") stays a credit rather than the title.
async function boot() {
  if (!(await requireAuth())) return;
  // This page already asks for status, so hand the site name to the nav rather
  // than making it fetch the same thing again.
  let status = {};
  try { status = await (await fetch("/api/status")).json(); } catch (e) {}
  applyAccent(status.accent);
  // Playing one item is where the browse page leads, so Browse stays lit.
  mountNav(me, { current: "browse", siteName: status.site_name });
  if (!ID) { titleEl.textContent = "Not found"; return; }
  await loadMedia();
  loadReactions();
}

boot();
