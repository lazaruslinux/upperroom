// Watch page. Confirms the viewer is signed in, plays the low latency stream,
// and runs the chat and watching list over a WebSocket.

const STREAM_URL = "/live/index.m3u8";

const video = document.getElementById("video");
const offline = document.getElementById("offline");
const messages = document.getElementById("messages");
const viewerCount = document.getElementById("viewer-count");
const viewerList = document.getElementById("viewer-list");
const chatForm = document.getElementById("chat-form");
const chatInput = document.getElementById("chat-input");
const unmuteButton = document.getElementById("unmute");
const clipBtn = document.getElementById("clip-btn");
const theaterInter = document.getElementById("theater-inter");
const nowShowing = document.getElementById("now-showing");

let me = null;
let hls = null;
let socket = null;
let streamOnline = false;          // tracks live state so the count can reword
let lastViewerCount = 0;
// Native-HLS status listeners are bound once. startVideo runs again on every
// offline-to-online flip, so re-adding them each time would stack duplicate
// status pollers on the one video element.
let nativeListenersBound = false;
const MAX_VISIBLE_MESSAGES = 50;  // keep the last 50 lines on screen, no more
// True once the initial history batch has rendered, so only genuinely live
// lines animate in - the backlog on connect/reconnect appears instantly.
let chatLive = false;

// The header count is "watching" while live, "in chat" while offline (people
// can still hang out in chat between streams).
function setViewerLabel() {
  const noun = streamOnline ? "watching" : "in chat";
  viewerCount.textContent = `${lastViewerCount} ${noun}`;
}

async function requireAuth() {
  let data;
  try {
    data = await (await fetch("/api/me")).json();
  } catch {
    // A network blip on boot must not crash the page or bounce a signed-in
    // viewer to login. Wait and try again; only an actual authed:false reply
    // sends them to the sign-in page.
    await new Promise((resolve) => setTimeout(resolve, 3000));
    return requireAuth();
  }
  if (!data.authed) {
    window.location.href = "/";
    return false;
  }
  me = data;
  return true;
}

// ---- video ----

// What the stage is showing. This used to be a boolean (offline or not), which
// stopped being enough once a theater session could be running: "no video right
// now" then means intermission, not "the stream is offline", and the two need
// different cards. streamOnline stays derived from it so everything that was
// keyed on live/offline (the viewer label, the highlight composer) is unchanged.
//
// guest_over is terminal: a pass that has run out does not come back, and
// nothing the stream does afterwards should take that card down.
let stage = "offline";
let theaterActive = false;
let theaterState = "off";
let theaterNow = null;

function setStage(next) {
  if (stage === "guest_over") return;
  stage = next;
  const playing = next === "live" || next === "theater_playing";
  streamOnline = playing;
  document.body.dataset.stage = next;
  offline.hidden = next !== "offline";
  if (theaterInter) theaterInter.hidden = next !== "theater_intermission";
  video.style.visibility = playing ? "visible" : "hidden";
  // Clipping only makes sense while the stream is live and being recorded, and
  // during theater nothing is recorded and nothing on screen is ours to cut.
  clipBtn.hidden = next !== "live";
  // The unmute button floats over the video, so it must never sit on one of the
  // cards. Hide it unless the video is actually muted and playing (the "playing"
  // handler below re-shows it in the usual case where playback resumes).
  unmuteButton.hidden = !playing || !(video.muted && !video.paused);
  if (next !== "theater_playing" && next !== "theater_intermission") hideNowShowing();
  setViewerLabel();
  // A highlight needs a live stream to show on, so the composer's send follows
  // the live state too. Guarded because the highlight controls do not exist for
  // a guest (setUpGuest removes the points chip).
  if (typeof updateHighlightSend === "function" && pointsChip && pointsChip.isConnected) {
    updateHighlightSend();
  }
}

// The two stages the video path itself can put us in. Which one depends on
// whether a theater session is running, so both go through here rather than
// being written out at each call site.
function stageForOnline() { return theaterActive ? "theater_playing" : "live"; }
function stageForOffline() { return theaterActive ? "theater_intermission" : "offline"; }

function startVideo() {
  if (window.Hls && Hls.isSupported()) {
    hls = new Hls({ lowLatencyMode: true, backBufferLength: 30 });
    hls.loadSource(STREAM_URL);
    hls.attachMedia(video);
    // Counts consecutive fatal errors with no clean playback in between. A
    // healthy frame resets it, so this only trips when the stream is really
    // down, not on a momentary blip.
    let recoverAttempts = 0;
    hls.on(Hls.Events.MANIFEST_PARSED, () => {
      setStage(stageForOnline());
      video.play().catch(() => {});
    });
    hls.on(Hls.Events.FRAG_BUFFERED, () => {
      recoverAttempts = 0;
      setStage(stageForOnline());
      // Picture has arrived, so the Now Showing card has done its job.
      armNowShowingHide();
    });
    hls.on(Hls.Events.ERROR, (event, data) => {
      if (!data.fatal) return;
      // A brief source hiccup (a muxer restart, a dropped segment) should not
      // blank straight to the offline card. Try to resume a few times first.
      if (recoverAttempts < 3) {
        recoverAttempts++;
        if (data.type === Hls.ErrorTypes.MEDIA_ERROR) {
          hls.recoverMediaError();
        } else {
          hls.startLoad();
        }
        return;
      }
      // Recovery did not take, so the stream is probably actually down. Show
      // the offline card and let the status poll bring it back when it returns.
      hls.destroy();
      hls = null;
      setStage(stageForOffline());
      setTimeout(checkStream, 5000);
    });
  } else if (video.canPlayType("application/vnd.apple.mpegurl")) {
    // Safari on iOS plays HLS natively without hls.js. Bind the status
    // listeners only once; the restart path just re-points the source.
    if (!nativeListenersBound) {
      nativeListenersBound = true;
      video.addEventListener("loadedmetadata", () => setStage(stageForOnline()));
      video.addEventListener("error", () => {
        setStage(stageForOffline());
        setTimeout(checkStream, 5000);
      });
    }
    video.src = STREAM_URL;
  }
}

// The channel accent rides along on the status poll (the head bootstrap already
// painted the last-seen value from localStorage); keep it in sync with the
// server and remember it for the next no-flash paint.
function applyAccent(value) {
  if (!["green", "amber", "blue", "ghost"].includes(value)) return;
  if (document.documentElement.dataset.accent !== value) {
    document.documentElement.dataset.accent = value;
    try { localStorage.setItem("selfstream_accent", value); } catch (e) {}
  }
}

async function checkStream() {
  let data;
  try {
    data = await (await fetch("/api/status")).json();
  } catch {
    // A failed poll must not end the polling loop: show the offline card and
    // try again, so the page recovers on its own when the server comes back.
    setStage(stageForOffline());
    setTimeout(checkStream, 5000);
    return;
  }
  applyAccent(data.accent);
  // Name the browser tab after the operator's site, not the platform.
  if (data.site_name && document.title !== data.site_name) document.title = data.site_name;
  if (data.online && !hls) {
    startVideo();
  } else if (!data.online) {
    setStage(stageForOffline());
    setTimeout(checkStream, 5000);
  }
}

// ---- theater ----
// A theater session is the operator playing something from their own library to
// the room. The video path is the ordinary one, so all this does is decide which
// card the stage shows between titles and put a Now Showing panel over the first
// couple of seconds of one.

const nsArt = document.getElementById("ns-art");
const nsTitle = document.getElementById("ns-title");
const nsMeta = document.getElementById("ns-meta");
const nsSynopsis = document.getElementById("ns-synopsis");
let nowShowingKey = null;     // which title the card is currently showing
let nowShowingTimer = null;

function hideNowShowing(forget) {
  if (nowShowingTimer) { clearTimeout(nowShowingTimer); nowShowingTimer = null; }
  if (nowShowing) nowShowing.hidden = true;
  // The key survives an ordinary hide so a repeated frame for the same title
  // cannot re-raise a card that has done its job. It is forgotten only when
  // the title changes or stops, so the next title gets its own card.
  if (forget) nowShowingKey = null;
  if (theaterInter) theaterInter.hidden = stage !== "theater_intermission";
}

// The card comes down once the picture is actually there, not on a timer from
// when the title was chosen: the whole point is to cover the wait, and how long
// that is depends on the viewer's connection.
function armNowShowingHide() {
  if (!nowShowing || nowShowing.hidden || nowShowingTimer) return;
  nowShowingTimer = setTimeout(() => {
    nowShowingTimer = null;
    hideNowShowing();
  }, 2000);
}

function showNowShowing(now) {
  if (!nowShowing || !now) return;
  const key = `${now.title}|${now.art || ""}`;
  if (key === nowShowingKey) return;
  nowShowingKey = key;
  if (nowShowingTimer) { clearTimeout(nowShowingTimer); nowShowingTimer = null; }
  nsTitle.textContent = now.title || "";
  const bits = [];
  if (now.year) bits.push(now.year);
  if (now.runtime_min) bits.push(`${now.runtime_min} min`);
  nsMeta.textContent = bits.join(" · ");
  nsMeta.hidden = bits.length === 0;
  nsSynopsis.textContent = now.synopsis || "";
  nsSynopsis.hidden = !now.synopsis;
  if (now.art) {
    nsArt.src = now.art;
    nsArt.hidden = false;
  } else {
    nsArt.removeAttribute("src");
    nsArt.hidden = true;
  }
  nowShowing.hidden = false;
  // Two cards on the stage read as clutter: while this one is up, the
  // intermission card yields (hideNowShowing restores it per stage).
  if (theaterInter) theaterInter.hidden = true;
}

function applyTheater(data) {
  theaterActive = !!data.active;
  theaterState = data.state || "off";
  theaterNow = data.now || null;
  // Re-run the stage decision with the new session state, keeping whichever of
  // live/offline the video path last told us.
  setStage(streamOnline ? stageForOnline() : stageForOffline());
  // The card covers the whole wait from "title chosen" to "picture arrived":
  // `now` is set the moment the projector is told to play, while the state is
  // still intermission, and the buffered handler takes the card down. Keying
  // on the stage here lost the common ordering where the server's "playing"
  // frame lands before this client's own player has flipped online.
  if (theaterActive && theaterNow) showNowShowing(theaterNow);
  else hideNowShowing(true);
  renderHostStrip();
}

async function loadTheater() {
  try {
    applyTheater(await (await fetch("/api/theater")).json());
  } catch {
    /* keep the last state rather than flapping the stage on a blip */
  }
}

// ---- the host strip (admin only) ----

const hostStrip = document.getElementById("host-strip");
const hostState = document.getElementById("host-state");
const hostStart = document.getElementById("host-start");
const hostPlay = document.getElementById("host-play");
const hostStop = document.getElementById("host-stop");
const hostEnd = document.getElementById("host-end");
const hostMsg = document.getElementById("host-msg");
const searchModal = document.getElementById("theater-search-modal");
const tsQuery = document.getElementById("ts-query");
const tsSubs = document.getElementById("ts-subs");
const tsResults = document.getElementById("ts-results");
const tsMsg = document.getElementById("ts-msg");

function showHostMsg(text, ok) {
  if (!hostMsg) return;
  hostMsg.textContent = text || "";
  hostMsg.classList.toggle("bad", !ok);
  hostMsg.hidden = !text;
}

function renderHostStrip() {
  if (!hostStrip || !hostStrip.isConnected) return;
  const label = !theaterActive ? "theater off"
    : theaterState === "playing" ? "playing" : "intermission";
  hostState.textContent = theaterNow ? `${label} · ${theaterNow.title}` : label;
  hostStart.hidden = theaterActive;
  hostPlay.hidden = !theaterActive;
  hostStop.hidden = !theaterActive || !theaterNow;
  hostEnd.hidden = !theaterActive;
}

// Every host action answers with the same state payload the socket broadcasts,
// so one path applies it and the strip can never disagree with the stage.
async function hostAction(path, body) {
  showHostMsg("", true);
  try {
    const reply = await fetch(path, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: body ? JSON.stringify(body) : undefined,
    });
    const data = await reply.json().catch(() => ({}));
    if (!reply.ok) {
      showHostMsg(
        reply.status === 502 ? "projector offline" : (data.error || "could not do that"),
        false,
      );
      return null;
    }
    applyTheater(data);
    return data;
  } catch {
    showHostMsg("could not reach the server", false);
    return null;
  }
}

function renderSearchResults(results) {
  tsResults.textContent = "";
  if (!results.length) {
    tsMsg.textContent = "Nothing matched.";
    tsMsg.hidden = false;
    return;
  }
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
      tsMsg.hidden = true;
      const done = await hostAction("/api/admin/theater/play", {
        jf_id: item.jf_id, subtitles: tsSubs.checked,
      });
      if (done) closeModal(searchModal);
      else {
        tsMsg.textContent = hostMsg.textContent;
        tsMsg.hidden = false;
        play.disabled = false;
      }
    });
    row.appendChild(play);
    tsResults.appendChild(row);
  });
}

async function runSearch() {
  const query = tsQuery.value.trim();
  if (query.length < 2) {
    tsMsg.textContent = "Type at least two characters.";
    tsMsg.hidden = false;
    return;
  }
  tsMsg.textContent = "Searching…";
  tsMsg.hidden = false;
  try {
    const reply = await fetch(
      `/api/admin/theater/search?q=${encodeURIComponent(query)}`
    );
    const data = await reply.json().catch(() => ({}));
    if (!reply.ok) {
      tsMsg.textContent =
        reply.status === 502 ? "projector offline" : (data.error || "could not search");
      return;
    }
    tsMsg.hidden = true;
    renderSearchResults(data.results || []);
  } catch {
    tsMsg.textContent = "could not reach the server";
  }
}

function setUpHost() {
  if (!hostStrip) return;
  // Everything here is admin only, so for everyone else it is removed rather
  // than hidden, exactly as the guest setup removes what a guest cannot use.
  if (!me || !me.admin) {
    hostStrip.remove();
    if (searchModal) searchModal.remove();
    return;
  }
  hostStrip.hidden = false;
  hostStart.addEventListener("click", () => hostAction("/api/admin/theater/session"));
  hostStop.addEventListener("click", () => hostAction("/api/admin/theater/stop"));
  hostEnd.addEventListener("click", () => {
    if (!confirm("End the theater session? Chat is wiped when it ends.")) return;
    hostAction("/api/admin/theater/end");
  });
  hostPlay.addEventListener("click", () => {
    tsQuery.value = "";
    tsResults.textContent = "";
    tsMsg.hidden = true;
    openModal(searchModal);
    tsQuery.focus();
  });
  tsQuery.addEventListener("keydown", (e) => {
    if (e.key === "Enter") runSearch();
  });
  document.getElementById("ts-search").addEventListener("click", runSearch);
  renderHostStrip();
}

// ---- chat and presence ----

function avatarColor(seed) {
  let hash = 0;
  for (let i = 0; i < seed.length; i++) hash = (hash * 31 + seed.charCodeAt(i)) % 360;
  return `hsl(${hash}, 55%, 45%)`;
}

function makeClickable(node, username) {
  node.classList.add("avatar-clickable");
  node.addEventListener("click", () => openProfile(username));
  return node;
}

function initialsNode(username, name, big) {
  const span = document.createElement("span");
  span.className = big ? "avatar avatar-lg" : "avatar";
  span.textContent = ((name || username || "?").trim().charAt(0) || "?").toUpperCase();
  span.style.background = avatarColor(username || "?");
  return span;
}

function avatarNode(username, name, version, big, clickable) {
  let node;
  if (!version) {
    node = initialsNode(username, name, big);
  } else {
    const img = document.createElement("img");
    img.className = big ? "avatar avatar-lg" : "avatar";
    img.alt = "";
    img.src = `/api/avatar/${encodeURIComponent(username)}?v=${version}`;
    // If the image cannot load, fall back to the initials bubble.
    img.addEventListener("error", () => {
      const fallback = initialsNode(username, name, big);
      if (clickable) makeClickable(fallback, username);
      img.replaceWith(fallback);
    });
    node = img;
  }
  if (clickable) makeClickable(node, username);
  return node;
}

// Role marks sit just to the right of the avatar, in place of any "(admin)"
// text. The host (admin) shows a bright-red video-camera icon that reads as
// "broadcaster"; a moderator shows a small blue "mod" tag. An admin keeps every
// moderator power, so an admin shows only the camera.
const CAMERA_SVG =
  '<svg viewBox="0 0 24 24" width="13" height="13" fill="currentColor" aria-hidden="true">' +
  '<path d="M3 7.5A1.5 1.5 0 0 1 4.5 6h9A1.5 1.5 0 0 1 15 7.5v9A1.5 1.5 0 0 1 13.5 18h-9' +
  'A1.5 1.5 0 0 1 3 16.5v-9Zm14 3 3.25-2.17a.6.6 0 0 1 .95.5v6.34a.6.6 0 0 1-.95.5L17 13.5v-3Z"/></svg>';

function roleBadgeNode(admin, mod, big) {
  if (!admin && !mod) return null;
  const span = document.createElement("span");
  if (admin) {
    span.className = "role-tag host" + (big ? " role-tag-lg" : "");
    span.title = "Broadcaster";
    span.innerHTML = CAMERA_SVG;   // a static, trusted icon; no user data
  } else {
    span.className = "role-tag mod" + (big ? " role-tag-lg" : "");
    span.title = "Moderator";
    span.textContent = "mod";
  }
  return span;
}

function formatTimestamp(ts) {
  // 24-hour local time, no date, e.g. "17:51". Chat is ephemeral, so the day
  // only matters on saved VODs and clips, where it is shown on the media page.
  const d = ts ? new Date(ts * 1000) : new Date();
  const h = String(d.getHours()).padStart(2, "0");
  const m = String(d.getMinutes()).padStart(2, "0");
  return `${h}:${m}`;
}

function atBottom() {
  return messages.scrollHeight - messages.scrollTop - messages.clientHeight < 40;
}

function addLine(node) {
  const stick = atBottom();
  if (chatLive) node.classList.add("enter");   // fade+rise only for live lines
  messages.appendChild(node);
  // Keep only the most recent lines so chat stays static but bounded.
  while (messages.children.length > MAX_VISIBLE_MESSAGES) {
    messages.removeChild(messages.firstChild);
  }
  if (stick) messages.scrollTop = messages.scrollHeight;
}

function renderChat(msg) {
  const line = document.createElement("div");
  line.className = "msg";
  if (msg.id != null) line.dataset.msgid = msg.id;
  line.appendChild(avatarNode(msg.user, msg.name, msg.avatar || 0, false, true));
  const badge = roleBadgeNode(msg.admin, msg.mod, false);
  if (badge) line.appendChild(badge);
  const bodyWrap = document.createElement("span");
  bodyWrap.className = "msg-body";
  const head = document.createElement("span");
  head.className = "msg-head";
  const name = document.createElement("span");
  name.className = msg.admin ? "name admin" : "name";
  name.textContent = msg.name;
  // Each person's chosen name color, if any, overrides the theme default (and the
  // admin amber). Colors are guarded server-side for readability.
  if (msg.name_color) name.style.color = msg.name_color;
  const time = document.createElement("span");
  time.className = "msg-time";
  time.textContent = formatTimestamp(msg.ts);
  head.append(name, time);
  const body = document.createElement("span");
  body.className = "body";
  if (msg.deleted) {
    markBodyDeleted(body);
  } else {
    body.textContent = msg.text;        // textContent keeps any HTML inert
    // Each person's own font and message color ride along on their messages for
    // everyone to see.
    body.style.fontFamily = FONTS[msg.font] || "";
    if (msg.msg_color) body.style.color = msg.msg_color;
  }
  bodyWrap.append(head, body);
  line.appendChild(bodyWrap);
  // Host and moderators get a hover delete button on every line, removing that
  // one message for everyone by its id.
  if (me && (me.admin || me.mod) && msg.id != null) {
    const del = document.createElement("button");
    del.type = "button";
    del.className = "msg-del";
    del.title = "Delete message";
    del.setAttribute("aria-label", "Delete message");
    del.textContent = "×";
    del.addEventListener("click", (e) => {
      e.stopPropagation();
      if (socket && socket.readyState === WebSocket.OPEN) {
        socket.send(JSON.stringify({ type: "moddelete", id: msg.id }));
      }
    });
    line.appendChild(del);
  }
  addLine(line);
}

// A line removed by a moderator stays in place but its text is replaced, so the
// conversation does not visibly reflow and everyone sees it was moderated.
function markBodyDeleted(body) {
  body.textContent = "deleted by a moderator";
  body.classList.add("deleted");
  body.style.fontFamily = "";
}

function applyDelete(id) {
  if (id == null) return;
  const line = messages.querySelector(`[data-msgid="${id}"]`);
  if (line) {
    const body = line.querySelector(".body");
    if (body) markBodyDeleted(body);
  }
}

function renderSystem(msg) {
  const line = document.createElement("div");
  line.className = "msg system";
  line.textContent = msg.text;
  addLine(line);
}

// A highlight reads as a spotlighted chat line: the sender's avatar, role mark,
// display name (in their chosen color), the time, and the message they spent
// points on, all inside an accent border. Built like renderChat so a highlight
// carries the same identity a normal line does. textContent only, so a crafted
// message can never inject markup.
function renderHighlight(msg) {
  const line = document.createElement("div");
  line.className = "msg highlight";
  // A highlight now carries a message id (it is logged like a chat line), so tag
  // the line with it the same way renderChat does. That is what lets applyDelete
  // find and blank this line when a moderator deletes the highlight.
  if (msg.id != null) line.dataset.msgid = msg.id;
  line.appendChild(avatarNode(msg.user, msg.name, msg.avatar || 0, false, true));
  const badge = roleBadgeNode(msg.admin, msg.mod, false);
  if (badge) line.appendChild(badge);
  const bodyWrap = document.createElement("span");
  bodyWrap.className = "msg-body";
  const head = document.createElement("span");
  head.className = "msg-head";
  const name = document.createElement("span");
  name.className = msg.admin ? "name admin" : "name";
  name.textContent = msg.name;
  if (msg.name_color) name.style.color = msg.name_color;
  const time = document.createElement("span");
  time.className = "msg-time";
  time.textContent = formatTimestamp(msg.ts);
  head.append(name, time);
  const body = document.createElement("span");
  body.className = "body";
  body.textContent = msg.message;   // textContent keeps any markup inert
  bodyWrap.append(head, body);
  line.appendChild(bodyWrap);
  addLine(line);
}

function renderPresence(msg) {
  lastViewerCount = msg.count;
  setViewerLabel();
  viewerList.innerHTML = "";
  msg.viewers.forEach((viewer) => {
    const item = document.createElement("li");
    item.appendChild(avatarNode(viewer.username, viewer.name, viewer.avatar || 0, false, true));
    const badge = roleBadgeNode(viewer.admin, viewer.mod, false);
    if (badge) item.appendChild(badge);
    const youSuffix = me && viewer.username === me.username ? " (you)" : "";
    const label = document.createElement("span");
    label.textContent = viewer.name + youSuffix;
    if (viewer.name_color) label.style.color = viewer.name_color;
    item.appendChild(label);
    viewerList.appendChild(item);
  });
}

function connectChat() {
  chatLive = false;   // the reconnect backlog should not animate either
  const scheme = location.protocol === "https:" ? "wss" : "ws";
  socket = new WebSocket(`${scheme}://${location.host}/ws`);

  // The theater state rides this socket, so a reconnect may have missed a
  // transition. Ask once on connect rather than assume the last one still holds.
  socket.addEventListener("open", () => { loadTheater(); });

  // A single per-type render for the message lines, used for both the live feed
  // and the replayed backlog, so a highlight in the history renders as a
  // highlight rather than being forced through the plain-chat renderer.
  function renderLine(msg) {
    if (msg.type === "chat") renderChat(msg);
    else if (msg.type === "highlight") renderHighlight(msg);
    else if (msg.type === "system") renderSystem(msg);
  }

  socket.addEventListener("message", (event) => {
    const msg = JSON.parse(event.data);
    if (msg.type === "presence") renderPresence(msg);
    else if (msg.type === "delete") applyDelete(msg.id);
    else if (msg.type === "wipe") {
      // The room is deliberately cleared between broadcasts. Empty it, then say
      // why, so a mid-conversation chat does not just vanish with no explanation.
      messages.innerHTML = "";
      if (msg.reason === "stream_ended") {
        renderSystem({ text: "Stream ended. Chat clears between broadcasts." });
      } else if (msg.reason === "moderator") {
        renderSystem({ text: "A moderator cleared the chat." });
      }
    }
    else if (msg.type === "theater") applyTheater(msg);
    else if (msg.type === "hello") {
      me = me || msg.you;
      msg.history.forEach(renderLine);
      chatLive = true;   // everything after the backlog is live
    } else {
      renderLine(msg);
    }
  });

  // If the connection drops, wait a moment and reconnect. Not, however, when
  // the server closed it because this session is no longer welcome: 4401 (no
  // valid account behind the cookie, which includes a guest whose pass ran out)
  // and 4403 (country) are answers, not blips, and retrying every three seconds
  // forever would be a loop that only stops when the tab closes.
  socket.addEventListener("close", (event) => {
    if (event.code === 4401 || event.code === 4403) {
      if (me && me.guest) endGuestSession();
      return;
    }
    if (event.code === 4429) {
      // Too many sockets from this account, or too many attempts from this
      // address. Retrying faster is exactly the wrong response, and a tight
      // loop here is what the limit exists to stop, so back off a long way.
      renderSystem({
        type: "system",
        text: "Chat is open in too many places. Close another tab, or wait a minute.",
        ts: Math.floor(Date.now() / 1000),
      });
      setTimeout(connectChat, 60000);
      return;
    }
    setTimeout(connectChat, 3000);
  });

  chatForm.onsubmit = (event) => {
    event.preventDefault();
    const text = chatInput.value.trim();
    if (!text || !socket || socket.readyState !== WebSocket.OPEN) return;
    socket.send(JSON.stringify({ type: "chat", text }));
    chatInput.value = "";
  };
}

document.getElementById("viewer-toggle").addEventListener("click", () => {
  viewerList.hidden = !viewerList.hidden;
});

// ---- settings: theme, chat font, avatar, bio ----

const THEME_KEY = "selfstream_theme";
const FONTS = {
  system: "",
  mono: "'Roboto Mono', monospace",
  comic: "'Comic Neue', cursive",
  retro: "'VT323', monospace",
  caveat: "'Caveat', cursive",
};
const FONT_LIST = [
  ["system", "Default"],
  ["mono", "Roboto Mono"],
  ["comic", "Comic Neue"],
  ["retro", "VT323"],
  ["caveat", "Caveat"],
];

const settingsPanel = document.getElementById("settings-panel");
const themeToggle = document.getElementById("theme-toggle");
const fontPicker = document.getElementById("font-picker");

document.getElementById("settings-toggle").addEventListener("click", () => {
  settingsPanel.hidden = !settingsPanel.hidden;
});

function applyTheme(theme) {
  document.documentElement.dataset.theme = theme;
  localStorage.setItem(THEME_KEY, theme);
  themeToggle.textContent = theme === "light" ? "Light" : "Dark";
}
applyTheme(localStorage.getItem(THEME_KEY) || "dark");
themeToggle.addEventListener("click", () => {
  applyTheme(document.documentElement.dataset.theme === "light" ? "dark" : "light");
});

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

// Your chosen font rides along on your own messages for everyone to see, so it
// is saved on the server (not just this browser). Each option is rendered in
// its own typeface so you can preview it before picking.
function buildFontPicker() {
  fontPicker.innerHTML = "";
  FONT_LIST.forEach(([key, label]) => {
    const btn = document.createElement("button");
    btn.type = "button";
    btn.className = "font-option" + (me && me.font === key ? " selected" : "");
    btn.textContent = label;
    btn.style.fontFamily = FONTS[key] || "";
    btn.addEventListener("click", async () => {
      me.font = key;
      fontPicker.querySelectorAll(".font-option").forEach((b) => b.classList.remove("selected"));
      btn.classList.add("selected");
      await saveProfile({ font: key });
    });
    fontPicker.appendChild(btn);
  });
}

// ---- chat colors (name + message text) ----
// Each viewer can color their own display name and message text. The choice is
// saved on the server so it rides along on their messages for everyone, and the
// server guards it for readability (rejecting invisible or reserved-red picks).
const nameColorInput = document.getElementById("name-color");
const msgColorInput = document.getElementById("msg-color");
const colorReset = document.getElementById("color-reset");
const colorMsg = document.getElementById("color-msg");
const DEFAULT_SWATCH = "#e6e8e2";

function showColorMsg(text, ok) {
  colorMsg.textContent = text || "";
  colorMsg.className = "settings-note pw-msg" + (text ? (ok ? " ok" : " bad") : "");
}

// Like saveProfile but returns the server's error text, so a rejected color can
// explain why (too dark, reserved red, malformed).
async function saveColor(patch) {
  try {
    const reply = await fetch("/api/profile", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(patch),
    });
    const data = await reply.json().catch(() => ({}));
    return { ok: reply.ok, error: data.error };
  } catch {
    return { ok: false, error: "Could not reach the server." };
  }
}

function setupColorPickers() {
  nameColorInput.value = me.name_color || DEFAULT_SWATCH;
  msgColorInput.value = me.msg_color || DEFAULT_SWATCH;
  nameColorInput.addEventListener("change", async () => {
    const r = await saveColor({ name_color: nameColorInput.value });
    if (r.ok) { me.name_color = nameColorInput.value; showColorMsg("Name color saved.", true); }
    else showColorMsg(r.error || "That color was rejected.", false);
  });
  msgColorInput.addEventListener("change", async () => {
    const r = await saveColor({ msg_color: msgColorInput.value });
    if (r.ok) { me.msg_color = msgColorInput.value; showColorMsg("Text color saved.", true); }
    else showColorMsg(r.error || "That color was rejected.", false);
  });
  colorReset.addEventListener("click", async () => {
    const r = await saveColor({ name_color: "", msg_color: "" });
    if (r.ok) {
      me.name_color = ""; me.msg_color = "";
      nameColorInput.value = DEFAULT_SWATCH;
      msgColorInput.value = DEFAULT_SWATCH;
      showColorMsg("Colors reset to default.", true);
    } else showColorMsg(r.error || "Could not reset.", false);
  });
}

// Reflect the signed in account's saved chat font and colors in the settings
// panel. Avatar, bio, and password now live in the home page's "Your settings".
function loadMyProfile() {
  if (!me) return;
  buildFontPicker();
  setupColorPickers();
}

// ---- profile popup (tap any avatar) ----

const profileModal = document.getElementById("profile-modal");
const profileAvatar = document.getElementById("profile-avatar");
const profileName = document.getElementById("profile-name");
const profileBio = document.getElementById("profile-bio");
const profileJoined = document.getElementById("profile-joined");
const profilePoints = document.getElementById("profile-points");
const profileModActions = document.getElementById("profile-modactions");

function sendChatCommand(text) {
  if (socket && socket.readyState === WebSocket.OPEN) {
    socket.send(JSON.stringify({ type: "chat", text }));
  }
}

// Host and moderators get quick moderation actions inside a viewer's profile
// popup: timeout, purge, ban, and (admin only) promote/demote. Each sends the
// matching chat command over the socket; the server authorizes it against a
// fresh role read and replies privately with the outcome.
function buildModActions(data) {
  profileModActions.hidden = true;
  profileModActions.innerHTML = "";
  if (!me || !(me.admin || me.mod)) return;           // plain viewers see none
  if (data.username === me.username) return;           // not on yourself
  if (data.admin && !me.admin) return;                 // a mod can't act on an admin
  const u = data.username;
  const actions = [
    ["Timeout 5m", () => sendChatCommand(`/timeout ${u} 300`)],
    ["Timeout 1h", () => sendChatCommand(`/timeout ${u} 3600`)],
    ["Delete all", () => { if (confirm(`Delete all of ${data.name}'s messages?`)) sendChatCommand(`/purge ${u}`); }],
    ["Ban", () => { if (confirm(`Ban ${data.name} from chat?`)) sendChatCommand(`/ban ${u}`); }],
  ];
  if (me.admin) {
    actions.push(data.mod
      ? ["Remove mod", () => sendChatCommand(`/unmod ${u}`)]
      : ["Make mod", () => sendChatCommand(`/mod ${u}`)]);
  }
  actions.forEach(([label, fn]) => {
    const btn = document.createElement("button");
    btn.type = "button";
    btn.className = "pill mod-action";
    btn.textContent = label;
    btn.addEventListener("click", () => { fn(); closeModal(profileModal); });
    profileModActions.appendChild(btn);
  });
  profileModActions.hidden = false;
}

function formatJoined(ts) {
  // Date only, in a compact M.D.YY style (chat timestamps are time-only now).
  if (!ts) return "";
  const d = new Date(ts * 1000);
  const yr = String(d.getFullYear()).slice(-2);
  return `joined ${d.getMonth() + 1}.${d.getDate()}.${yr}`;
}

async function openProfile(username) {
  try {
    const data = await (await fetch(`/api/profile/${encodeURIComponent(username)}`)).json();
    profileAvatar.innerHTML = "";
    profileAvatar.appendChild(avatarNode(data.username, data.name, data.avatar || 0, true, false));
    const badge = roleBadgeNode(data.admin, data.mod, true);
    if (badge) profileAvatar.appendChild(badge);
    profileName.textContent = data.name;
    profileBio.textContent = data.bio || "No bio yet.";
    profileJoined.textContent = formatJoined(data.joined);
    profilePoints.textContent = data.points != null ? `pts ${data.points}` : "";
    buildModActions(data);
    openModal(profileModal);
  } catch {
    /* a failed lookup just does nothing */
  }
}

// ---- modal helpers ----

function openModal(m) { m.hidden = false; }
function closeModal(m) { m.hidden = true; }
document.querySelectorAll(".modal").forEach((m) => {
  m.addEventListener("click", (e) => {
    if (e.target === m || e.target.hasAttribute("data-close")) closeModal(m);
  });
});
document.addEventListener("keydown", (e) => {
  if (e.key === "Escape") {
    document.querySelectorAll(".modal:not([hidden])").forEach(closeModal);
  }
});

// Browsers only allow autoplay when the video starts muted. The stream still
// carries your audio, so once it is playing we offer a button to turn sound on.
video.addEventListener("playing", () => {
  unmuteButton.hidden = !video.muted;
  // Safari plays HLS natively, with no FRAG_BUFFERED to hang the card off.
  armNowShowingHide();
});
video.addEventListener("volumechange", () => {
  if (!video.muted) unmuteButton.hidden = true;
});
unmuteButton.addEventListener("click", () => {
  video.muted = false;
  video.play().catch(() => {});
  unmuteButton.hidden = true;
});

// Keep the page exactly as tall as the visible viewport so the video, the last
// message, and the send box stay on screen on mobile as the address bar or the
// keyboard slides in and out. When the keyboard opens the ONLY thing that should
// change is the messages list shrinking - the video stays put and the input
// stays pinned above the keyboard. The body is overflow:hidden, but iOS still
// scrolls the visual viewport and displaces the whole layout, so we re-measure
// --vvh and force the window back to the top on every viewport change.
function lockHeight() {
  const vv = window.visualViewport;
  const height = (vv && vv.height) || window.innerHeight;
  document.documentElement.style.setProperty("--vvh", height + "px");
  // Counteract any page displacement the keyboard caused. offsetTop is the
  // visual viewport's own shift; pageX/YOffset is the layout scroll. Zero both.
  if (window.pageYOffset !== 0 || window.pageXOffset !== 0) window.scrollTo(0, 0);
}
if (window.visualViewport) {
  // Some mobile browsers only fire "scroll" (not "resize") when the bottom
  // toolbar or the keyboard slides in or out, which changes the visible height,
  // so listen to both. Otherwise the send box can end up hidden behind them.
  window.visualViewport.addEventListener("resize", lockHeight);
  window.visualViewport.addEventListener("scroll", lockHeight);
}
window.addEventListener("resize", lockHeight);
window.addEventListener("orientationchange", lockHeight);
// Re-measure shortly after load too; the first value can be taken before the
// browser chrome has settled.
window.addEventListener("load", () => setTimeout(lockHeight, 200));
lockHeight();

// Focusing the chat input opens the keyboard. Animate the layout height change
// (so the video resizes smoothly, not in a jump) and, once the viewport has
// settled, re-measure and pin the newest message to the bottom so it stays in
// view above the keyboard. Blur reverses it.
function settleAfterKeyboard() {
  // 300ms covers the keyboard slide-in on both iOS and Android; re-measure a
  // couple of times because the viewport height arrives in stages.
  [120, 300].forEach((t) => setTimeout(() => {
    lockHeight();
    messages.scrollTop = messages.scrollHeight;
  }, t));
}
chatInput.addEventListener("focus", () => {
  document.body.classList.add("kb-anim");
  settleAfterKeyboard();
});
chatInput.addEventListener("blur", () => {
  settleAfterKeyboard();
  setTimeout(() => document.body.classList.remove("kb-anim"), 320);
});

// ---- clipping the recent stream ----

const clipLenModal = document.getElementById("clip-len-modal");
const clipLenButtons = Array.from(document.querySelectorAll(".clip-len"));
const clipSave = document.getElementById("clip-save");
const clipMsg = document.getElementById("clip-msg");
const clipNameModal = document.getElementById("clip-name-modal");
const clipName = document.getElementById("clip-name");
const clipNameSave = document.getElementById("clip-name-save");
const clipNameSkip = document.getElementById("clip-name-skip");
const clipNameMsg = document.getElementById("clip-name-msg");

function showClipMsg(text, ok, link, el = clipMsg) {
  el.className = "pw-msg " + (ok ? "ok" : "bad");
  el.textContent = "";
  el.append(document.createTextNode(text));
  if (link) {
    el.append(document.createTextNode(" "));
    const a = document.createElement("a");
    a.href = link;
    a.textContent = "View clip";
    el.appendChild(a);
  }
}

// The instant the viewer was actually looking at when they pressed Clip.
//
// This is the whole of clip accuracy. The old code let the server use its own
// clock at the moment the SAVE request arrived, which is wrong by however long
// the viewer spent typing a name, plus however far behind the live edge their
// player happens to be. Both of those are seconds, and the second one varies per
// viewer, so no fixed correction can fix it.
//
// MediaMTX stamps its playlist with EXT-X-PROGRAM-DATE-TIME, so hls.js can tell
// us the exact wall-clock time of the frame on screen via playingDate. When that
// is unavailable (Safari playing HLS natively, or a source without the stamp) we
// fall back to now minus the measured latency, and failing that send nothing at
// all and let the server use its own estimate.
function selectClipLength(value) {
  clipSeconds = value;
  clipLenButtons.forEach((btn) => {
    btn.classList.toggle("is-on", Number(btn.dataset.seconds) === value);
  });
}

clipLenButtons.forEach((btn) => {
  btn.addEventListener("click", () => selectClipLength(Number(btn.dataset.seconds)));
});

let clipSeconds = 30;    // the chip that is selected

function currentFrameInstant() {
  try {
    if (hls && hls.playingDate) return hls.playingDate.getTime() / 1000;
    if (hls && typeof hls.latency === "number" && hls.latency > 0) {
      return Date.now() / 1000 - hls.latency;
    }
  } catch (e) {
    /* fall through and let the server estimate */
  }
  return null;
}

// Captured on Clip, sent on Save, so typing a name cannot move the window.
let clipInstant = null;

// The clip the name modal is naming. It already exists by then.
let savedClipId = null;

clipBtn.addEventListener("click", () => {
  clipInstant = currentFrameInstant();
  clipMsg.textContent = "";
  clipMsg.className = "pw-msg";
  clipSave.disabled = false;
  openModal(clipLenModal);
});

clipSave.addEventListener("click", async () => {
  clipSave.disabled = true;
  showClipMsg("Saving…", true);
  try {
    const body = { at: clipInstant };
    if (clipSeconds) body.seconds = clipSeconds;
    const reply = await fetch("/api/clip", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    const data = await reply.json().catch(() => ({}));
    if (reply.ok) {
      closeModal(clipLenModal);
      openNameModal(data.id);
    } else {
      // Theater, cooldown, not live: the refusal belongs on the step that asked.
      showClipMsg(data.error || "Could not make the clip.", false);
      clipSave.disabled = false;
    }
  } catch {
    showClipMsg("Could not make the clip.", false);
    clipSave.disabled = false;
  }
});

function openNameModal(id) {
  savedClipId = id;
  clipName.value = "";
  clipNameSave.disabled = false;
  clipNameSkip.textContent = "Skip";
  // Say it is saved up front, link and all: naming is optional and closing this
  // by any route is a perfectly good ending, so the confirmation cannot wait on
  // a second button press.
  showClipMsg("Clip saved.", true, `/media?type=clip&id=${id}`, clipNameMsg);
  openModal(clipNameModal);
  clipName.focus();
}

clipNameSave.addEventListener("click", async () => {
  const name = clipName.value.trim();
  if (!name || !savedClipId) {
    closeModal(clipNameModal);
    return;
  }
  clipNameSave.disabled = true;
  try {
    const reply = await fetch(`/api/clips/${savedClipId}/name`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ name }),
    });
    const data = await reply.json().catch(() => ({}));
    if (reply.ok) {
      showClipMsg(
        "Clip saved.", true, `/media?type=clip&id=${savedClipId}`, clipNameMsg
      );
      // Naming is done, so the way out stops reading as skipping something.
      // Save stays live: a typo is fixable here rather than only on the clip.
      clipNameSkip.textContent = "Close";
    } else {
      showClipMsg(data.error || "Could not name it.", false, null, clipNameMsg);
    }
  } catch {
    showClipMsg("Could not name it.", false, null, clipNameMsg);
  }
  clipNameSave.disabled = false;
});

// ---- channel points and the highlight redemption ----

const pointsChip = document.getElementById("points-chip");
const highlightModal = document.getElementById("highlight-modal");
const highlightBalance = document.getElementById("highlight-balance");
const highlightCostEl = document.getElementById("highlight-cost");
const highlightInput = document.getElementById("highlight-input");
const highlightSend = document.getElementById("highlight-send");
const highlightMsg = document.getElementById("highlight-msg");

let myPoints = 0;
let highlightCost = 50;

function setPoints(n) {
  myPoints = n;
  pointsChip.textContent = `pts ${n}`;
  pointsChip.hidden = false;
  highlightBalance.textContent = `pts ${n}`;
  updateHighlightSend();
}

// Send stays disabled until the balance covers the cost and there is something
// to say. A highlight only shows on the live stream, so while the stream is
// offline the send is disabled outright with an explaining title, matching the
// server, which refuses an offline redeem before any spend.
function updateHighlightSend() {
  if (!streamOnline) {
    highlightSend.disabled = true;
    highlightSend.title = "Highlights show on stream, and the stream is offline right now.";
    return;
  }
  highlightSend.title = "";
  highlightSend.disabled = myPoints < highlightCost || !highlightInput.value.trim();
}

async function loadPoints() {
  try {
    const data = await (await fetch("/api/points")).json();
    if (typeof data.cost === "number") highlightCost = data.cost;
    highlightCostEl.textContent = `pts ${highlightCost}`;
    setPoints(data.points || 0);
  } catch {
    /* leave the chip as it is */
  }
}

async function sendHighlight() {
  const message = highlightInput.value.trim();
  if (!message) return;
  highlightMsg.hidden = true;
  highlightSend.disabled = true;
  try {
    const reply = await fetch("/api/redeem", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ message }),
    });
    const data = await reply.json().catch(() => ({}));
    if (reply.ok) {
      highlightInput.value = "";
      setPoints(data.points);   // updates the chip and the balance line
      closeModal(highlightModal);
    } else {
      showHighlightMsg(data.detail || data.error || "Could not highlight that.", false);
      updateHighlightSend();
    }
  } catch {
    showHighlightMsg("Could not reach the server.", false);
    updateHighlightSend();
  }
}

function showHighlightMsg(text, ok) {
  highlightMsg.className = "pw-msg " + (ok ? "ok" : "bad");
  highlightMsg.textContent = text;
  highlightMsg.hidden = false;
}

highlightInput.addEventListener("input", updateHighlightSend);
highlightInput.addEventListener("keydown", (e) => {
  if (e.key === "Enter" && !highlightSend.disabled) sendHighlight();
});
highlightSend.addEventListener("click", sendHighlight);

pointsChip.addEventListener("click", () => {
  highlightMsg.hidden = true;
  openModal(highlightModal);
  loadPoints();   // fetch a fresh balance and cost each time it opens
  // Opening it between streams should say why the send is greyed out rather
  // than leave a dead button with no explanation.
  if (!streamOnline) {
    showHighlightMsg("Highlights show on stream, and the stream is offline right now.", false);
  }
});

// ---- guest passes ---------------------------------------------------------
// A guest watches and chats and does nothing else, so the controls that need an
// account are removed rather than left to fail on a 403. The countdown is drawn
// from the absolute expiry the server sent, so it does not drift while the page
// sits open and does not care whether the visitor's clock is right.

const guestTimer = document.getElementById("guest-timer");
const guestOver = document.getElementById("guest-over");
let guestTick = null;

function formatRemaining(seconds) {
  const minutes = Math.floor(seconds / 60);
  const rest = seconds % 60;
  if (minutes > 0) return `${minutes}:${String(rest).padStart(2, "0")} left`;
  return `${rest}s left`;
}

function endGuestSession() {
  if (guestTick) { clearInterval(guestTick); guestTick = null; }
  guestTimer.textContent = "pass ended";
  guestTimer.classList.add("is-out");
  // Stop the video rather than let it stall on its own: the next segment would
  // be refused by /api/verify anyway, and a spinner reads as a broken stream
  // instead of a pass that ran out.
  try {
    video.pause();
    if (hls) { hls.destroy(); hls = null; }
  } catch (e) { /* nothing to stop */ }
  // Terminal: setStage refuses to move off guest_over, so nothing the stream
  // does afterwards puts an offline or intermission card over this one.
  setStage("guest_over");
  video.hidden = true;
  guestOver.hidden = false;
  // Chat goes with it. The socket is closed from the server side by the reaper,
  // but do not leave a live-looking composer behind in the meantime.
  chatInput.disabled = true;
  chatInput.placeholder = "your guest pass has ended";
}

function renderGuestTimer() {
  const left = me.guest_expires_at - Math.floor(Date.now() / 1000);
  if (left <= 0) {
    endGuestSession();
    return;
  }
  guestTimer.textContent = formatRemaining(left);
  // The last five minutes get a warning look, so the end is not a surprise.
  guestTimer.classList.toggle("is-low", left <= 300);
}

function setUpGuest() {
  if (!me || !me.guest) return;
  // Clipping, points and the highlight composer all need an account.
  clipBtn.remove();
  pointsChip.remove();
  // The font and color pickers save to a member profile, so a guest's saves
  // 403 (and the font picker would pretend to succeed). Remove those rows
  // rather than leave controls that cannot take.
  document
    .querySelectorAll("#settings-panel .setting-font, #settings-panel .setting-colors")
    .forEach((row) => row.remove());
  const colorNote = document.getElementById("color-msg");
  if (colorNote) colorNote.remove();
  const note = document.querySelector("#settings-panel .settings-note.muted");
  if (note) {
    note.textContent =
      "You are watching as a guest. Sign in or use an invite code for an account.";
  }
  // The home link goes nowhere useful for a guest; point it at the way in.
  const homeLink = document.querySelector(".chat-head a[href='/home']");
  if (homeLink) {
    homeLink.setAttribute("href", "/");
    homeLink.setAttribute("aria-label", "Sign in");
  }
  guestTimer.hidden = false;
  renderGuestTimer();
  guestTick = setInterval(renderGuestTimer, 1000);
}

async function boot() {
  if (!(await requireAuth())) return;
  // Let moderators and admins know the commands exist, without cluttering chat
  // for everyone else.
  if (me && (me.admin || me.mod)) {
    chatInput.placeholder = "say something, or /help";
  }
  setUpGuest();
  setUpHost();
  // The font and color controls belong to a member profile; a guest has none
  // and setUpGuest has already removed those rows, so skip wiring them.
  if (!me.guest) loadMyProfile();
  // A guest has no balance and the endpoint refuses them, so do not ask.
  if (!me.guest) loadPoints();
  // Before the first status poll, so an intermission never flashes the offline
  // card on the way in.
  await loadTheater();
  connectChat();
  checkStream();
}

boot();
