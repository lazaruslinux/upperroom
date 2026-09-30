// The room. Confirms the viewer is signed in, plays the low latency stream on
// the program monitor, and runs the chat and the call board over a WebSocket.
//
// Top to bottom on a phone: the lamp strip (nav.js), the monitor, the slate
// (what is on, and the sound), the call board (who is here), then chat, with
// the composer at the thumb. On a wide screen chat moves into a column beside
// the monitor. Nothing is ever drawn over a playing picture.

const STREAM_URL = "/live/index.m3u8";

const video = document.getElementById("video");
const monitor = document.getElementById("monitor");
const offline = document.getElementById("offline");
const lastAir = document.getElementById("last-air");
const messages = document.getElementById("messages");
const viewerCount = document.getElementById("viewer-count");
const lines = document.getElementById("lines");
const chatOffline = document.getElementById("chat-offline");
const chatForm = document.getElementById("chat-form");
const chatInput = document.getElementById("chat-input");
const clipBtn = document.getElementById("clip-btn");
const pointsBtn = document.getElementById("points-btn");
const soundBtn = document.getElementById("sound-btn");
const fullBtn = document.getElementById("full-btn");
const chatToggle = document.getElementById("chat-toggle");
const slateTitle = document.getElementById("slate-title");
const slateSub = document.getElementById("slate-sub");
const offair = document.getElementById("offair");
const theaterInter = document.getElementById("theater-inter");
const nowShowing = document.getElementById("now-showing");
const roomFull = document.getElementById("room-full");

// The dashboard shows this page in a frame so the streamer sees exactly what
// the room does. A few things change in there: no strip (the dashboard is
// the strip), no link home, no notices.
const framed = window.top !== window.self;
if (framed) document.body.classList.add("framed");

let me = null;
let strip = null;
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

// The count is "watching" while live and "in chat" while off air (people can
// hang out in chat between streams). The number is on the board; the words
// ride along for a screen reader.
function setViewerLabel() {
  const noun = streamOnline ? "watching" : "in chat";
  viewerCount.textContent = String(lastViewerCount);
  viewerCount.setAttribute("aria-label", `${lastViewerCount} ${noun}`);
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

// What the monitor is showing. This used to be a boolean (offline or not),
// which stopped being enough once a theater session could be running: "no
// video right now" then means intermission, not "the stream is offline", and
// the two need different cards. streamOnline stays derived from it so
// everything keyed on live/offline is unchanged.
//
// Nothing is known until the first status poll, which is also what makes the
// first move to "offline" count as a change.
let stage = "boot";
let theaterActive = false;
let theaterState = "off";
let theaterNow = null;

function setStage(next) {
  const was = stage;
  stage = next;
  const playing = next === "live" || next === "theater_playing";
  streamOnline = playing;
  document.body.dataset.stage = next;
  offline.hidden = next !== "offline";
  // Only for a channel that is plainly off air. An intermission has its own
  // card explaining the gap.
  if (chatOffline) chatOffline.hidden = next !== "offline";
  if (theaterInter) theaterInter.hidden = next !== "theater_intermission";
  video.style.visibility = playing ? "visible" : "hidden";
  // Clipping only makes sense while the stream is live and being recorded, and
  // during theater nothing is recorded and nothing on screen is ours to cut.
  if (clipBtn.isConnected) {
    const canClip = next === "live";
    clipBtn.disabled = !canClip;
    const label = canClip ? "Clip the last minute" : "Clips are off while nobody is on air";
    clipBtn.setAttribute("aria-label", label);
    clipBtn.title = label;
  }
  soundBtn.hidden = !playing;
  fullBtn.hidden = !playing;
  renderSound();
  if (next !== "theater_playing" && next !== "theater_intermission") hideNowShowing();
  setViewerLabel();
  renderSlate();
  if (next === "offline" && was !== "offline") loadOffair();
  offair.hidden = next !== "offline" || !me;
  // A highlight needs a live stream to show on, so its send follows the live
  // state too.
  if (typeof updateHighlightSend === "function" && pointsBtn && pointsBtn.isConnected) {
    updateHighlightSend();
  }
}

// The two stages the video path itself can put us in. Which one depends on
// whether a theater session is running, so both go through here rather than
// being written out at each call site.
function stageForOnline() { return theaterActive ? "theater_playing" : "live"; }
function stageForOffline() { return theaterActive ? "theater_intermission" : "offline"; }

// The server refuses the video when the channel is at its viewer limit, and it
// says so with a 403 on the stream itself. hls.js hands us that status on the
// error and takes the short path above; Safari's native player does not, and
// neither does a fatal that arrives without a response, so those ask the stream
// directly. Anything but a refusal is a real outage and shows the offline card.
async function streamRefused() {
  try {
    const reply = await fetch(STREAM_URL, { method: "GET", cache: "no-store" });
    return reply.status === 403;
  } catch {
    return false;
  }
}

// After a refusal, the status poll leaves the video alone for a while rather
// than knocking on a full room every few seconds.
let fullUntil = 0;

function showRoomFull() {
  // Not the offline card and not the intermission card: the stream is fine,
  // this viewer just has no place in it. Chat is not capped, so the socket
  // stays up and the room stays readable behind this.
  setStage(stageForOffline());
  offline.hidden = true;
  offair.hidden = true;
  if (theaterInter) theaterInter.hidden = true;
  roomFull.hidden = false;
  fullUntil = Date.now() + 15000;
  scheduleStatus(15000);
}

function hideRoomFull() {
  roomFull.hidden = true;
}

function startVideo() {
  hideRoomFull();
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
      // Being refused is not a hiccup. There is nothing to recover from a 403,
      // and retrying it would only spend the three attempts below and then
      // report the stream as down, which is the wrong thing to tell somebody
      // whose only problem is that the room is full.
      if (data.response && data.response.code === 403) {
        hls.destroy();
        hls = null;
        showRoomFull();
        return;
      }
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
      // Recovery did not take. Either the room is full, which the stream says
      // with a 403, or the stream is actually down.
      hls.destroy();
      hls = null;
      streamRefused().then((refused) => {
        if (refused) {
          showRoomFull();
          return;
        }
        setStage(stageForOffline());
        scheduleStatus(5000);
      });
    });
  } else if (video.canPlayType("application/vnd.apple.mpegurl")) {
    // Safari on iOS plays HLS natively without hls.js. Bind the status
    // listeners only once; the restart path just re-points the source.
    if (!nativeListenersBound) {
      nativeListenersBound = true;
      video.addEventListener("loadedmetadata", () => setStage(stageForOnline()));
      video.addEventListener("error", () => {
        streamRefused().then((refused) => {
          if (refused) {
            showRoomFull();
            return;
          }
          setStage(stageForOffline());
          scheduleStatus(5000);
        });
      });
    }
    video.src = STREAM_URL;
  }
}

// The status poll. It feeds the lamp (through the strip) and the slate, and it
// is what starts the picture when the stream comes up. It never takes down a
// picture that is playing: a stream ending is noticed by the player itself,
// which is also what keeps a momentary API failure from blanking the room.
let statusTimer = null;
let lastStatus = null;

function scheduleStatus(ms) {
  clearTimeout(statusTimer);
  statusTimer = setTimeout(checkStream, ms);
}

async function checkStream() {
  let data;
  try {
    data = await (await fetch("/api/status")).json();
  } catch {
    // A failed poll must not end the polling loop: show the offline card and
    // try again, so the page recovers on its own when the server comes back.
    if (!hls) setStage(stageForOffline());
    scheduleStatus(5000);
    return;
  }
  lastStatus = data;
  if (strip) strip.setStatus(data);
  renderSlate();
  if (data.online && !hls && !videoHidden && Date.now() >= fullUntil) {
    startVideo();
    loadChannel();
  } else if (!data.online && !hls) {
    setStage(stageForOffline());
  }
  scheduleStatus(hls ? 15000 : 5000);
}

// ---- the slate ----

let channel = null;

async function loadChannel() {
  try {
    const reply = await fetch("/api/channel");
    if (reply.ok) channel = await reply.json();
  } catch {
    /* the slate keeps whatever it last said */
  }
  renderSlate();
  renderLastAir();
}

function renderSlate() {
  if (channel && channel.title) slateTitle.textContent = channel.title;
  const game = streamOnline && lastStatus && lastStatus.game ? lastStatus.game : "";
  slateSub.textContent = game ? `Playing ${game}` : "";
  slateSub.hidden = !game;
  // Off air the monitor says it all.
  const slate = slateTitle.closest(".slate");
  slate.hidden = stage === "offline";
}

// "Last on air Sunday, for 1 h 24 min." The day comes from the channel; the
// length from the newest recording, when it is the same broadcast.
let lastVod = null;

function dayWord(epoch) {
  const then = new Date(epoch * 1000);
  const now = new Date();
  const midnight = new Date(now.getFullYear(), now.getMonth(), now.getDate()).getTime();
  const diffDays = Math.floor((midnight - then.getTime()) / 86400000) + 1;
  if (then.getTime() >= midnight) return "today";
  if (diffDays === 1) return "yesterday";
  if (diffDays < 7) return then.toLocaleDateString(undefined, { weekday: "long" });
  return then.toLocaleDateString(undefined, { month: "long", day: "numeric" });
}

// "Watch Sunday's broadcast", or for an older one, the date it went out.
function lastBroadcastTitle(epoch) {
  const age = Date.now() / 1000 - epoch;
  if (age > 6 * 86400) {
    const when = new Date(epoch * 1000).toLocaleDateString(undefined, { month: "long", day: "numeric" });
    return `Watch the ${when} broadcast`;
  }
  return `Watch ${dayWord(epoch)}’s broadcast`;
}

function lengthWords(seconds) {
  const m = Math.round(seconds / 60);
  if (m < 60) return `${m} min`;
  const h = Math.floor(m / 60);
  return m % 60 ? `${h} h ${m % 60} min` : `${h} h`;
}

function renderLastAir() {
  const ended = channel && channel.last_air_ended_at;
  if (!ended) {
    lastAir.textContent = "It starts here on its own when it goes live.";
    return;
  }
  let line = `Last on air ${dayWord(ended)}`;
  if (lastVod && lastVod.duration && Math.abs(lastVod.started_at + lastVod.duration - ended) < 3600) {
    line += `, for ${lengthWords(lastVod.duration)}`;
  }
  lastAir.textContent = `${line}.`;
}

function durationClock(secs) {
  secs = Math.max(0, Math.round(secs || 0));
  const h = Math.floor(secs / 3600);
  const m = Math.floor((secs % 3600) / 60);
  const s = secs % 60;
  const pad = (n) => String(n).padStart(2, "0");
  return h > 0 ? `${h}:${pad(m)}:${pad(s)}` : `${m}:${pad(s)}`;
}

// ---- off air: hearing about the next one, and watching the last one ----

const pushRow = document.getElementById("push-row");
const pushChip = document.getElementById("push-chip");
const pushMsg = document.getElementById("push-msg");
const lastVodLink = document.getElementById("last-vod");
let pushChecked = false;

// One quiet chip, offered only where a tap can actually sign this device up.
// Never a pop-up: the browser asks for permission only after the tap.
function showPush(chip, line) {
  pushChip.hidden = !chip;
  pushMsg.textContent = line || "";
  pushMsg.hidden = !line;
  pushRow.hidden = !chip && !line;
}

async function renderPush() {
  if (pushChecked) return;
  pushChecked = true;
  showPush((await pushNotify.state()) === "off");
}

pushChip.addEventListener("click", () => {
  pushChip.disabled = true;
  pushNotify.turnOn().then((state) => {
    if (state === "on") showPush(false, "This device will be notified when it goes live.");
    else if (state === "blocked") showPush(false, pushNotify.explain("blocked"));
    else showPush(state === "off");
  }).catch(() => {
    showPush(true, "Could not turn it on here. Try again from Options.");
  }).finally(() => { pushChip.disabled = false; });
});

let vodsFetchedAt = 0;

async function loadOffair() {
  if (!me) return;
  renderPush();
  if (Date.now() - vodsFetchedAt < 60000) return;
  vodsFetchedAt = Date.now();
  let vods = [];
  try {
    const reply = await fetch("/api/vods");
    if (reply.ok) vods = (await reply.json()).vods || [];
  } catch {
    /* no library to point at is not worth a message */
  }
  lastVod = vods[0] || null;
  renderLastAir();
  if (!lastVod) {
    lastVodLink.hidden = true;
    return;
  }
  lastVodLink.href = `/media?type=vod&id=${lastVod.id}`;
  document.getElementById("last-vod-title").textContent = lastBroadcastTitle(lastVod.started_at);
  document.getElementById("last-vod-meta").textContent =
    [lastVod.title, durationClock(lastVod.duration)].filter(Boolean).join(" · ");
  const thumb = document.getElementById("last-vod-thumb");
  thumb.textContent = "";
  if (lastVod.poster) {
    const img = document.createElement("img");
    img.alt = "";
    img.src = `/media/vods/${lastVod.id}.jpg`;
    img.addEventListener("error", () => img.remove());
    thumb.appendChild(img);
  }
  lastVodLink.hidden = false;
}

// ---- sound and the full screen ----
// Browsers only allow autoplay when the video starts muted. The stream still
// carries its audio, so the slate offers it, and it stays out of the picture.

function renderSound() {
  const on = !video.muted;
  soundBtn.setAttribute("aria-pressed", on ? "true" : "false");
  soundBtn.setAttribute("aria-label", on ? "Sound is on. Mute" : "Turn the sound on");
  document.getElementById("sound-off").toggleAttribute("hidden", on);
  document.getElementById("sound-on").toggleAttribute("hidden", !on);
}

soundBtn.addEventListener("click", () => {
  video.muted = !video.muted;
  if (!video.muted) video.play().catch(() => {});
  renderSound();
});
video.addEventListener("volumechange", renderSound);

// Full screen is the picture alone, with the player's own controls: in there
// nothing else is drawn, so the controls can be.
fullBtn.addEventListener("click", () => {
  if (video.requestFullscreen) {
    video.requestFullscreen().catch(() => {});
  } else if (video.webkitEnterFullscreen) {
    video.webkitEnterFullscreen();       // iPhone Safari
  }
});
document.addEventListener("fullscreenchange", () => {
  video.controls = document.fullscreenElement === video;
});

// ---- theater ----
// A theater session is the operator playing something from their own library to
// the room. The video path is the ordinary one, so all this does is decide which
// card the monitor shows between titles and put a Now Showing panel over the
// first couple of seconds of one.

const nsArt = document.createElement("img");
nsArt.className = "ns-art";
nsArt.alt = "";
nsArt.hidden = true;
if (nowShowing) nowShowing.prepend(nsArt);
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
  // Two cards on the monitor read as clutter: while this one is up, the
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

// Theater is optional on the server. When it is off its routes answer 404, and
// the controls, the picker and the intermission card leave the page for good.
let theaterOff = false;

function dropTheater() {
  theaterOff = true;
  for (const id of ["host-strip", "theater-search-modal", "theater-inter", "now-showing"]) {
    const el = document.getElementById(id);
    if (el) el.remove();
  }
}

async function loadTheater() {
  if (theaterOff) return;
  try {
    const reply = await fetch("/api/theater");
    if (reply.status === 404) {
      dropTheater();
      return;
    }
    applyTheater(await reply.json());
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
const hostNoSubs = document.getElementById("host-nosubs");
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
  // Shown on the first theater state, not at setup, so a server with theater
  // off never flashes the strip before the 404 takes it away.
  hostStrip.hidden = false;
  const label = !theaterActive ? "Theater off"
    : theaterState === "playing" ? "Playing" : "Intermission";
  hostState.textContent = theaterNow ? `${label} · ${theaterNow.title}` : label;
  hostStart.hidden = theaterActive;
  hostPlay.hidden = !theaterActive;
  hostStop.hidden = !theaterActive || !theaterNow;
  // Only worth offering while there is a title to put back on.
  hostNoSubs.hidden = !theaterActive || !theaterNow;
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
        reply.status === 502 ? "Projector offline" : (data.error || "Could not do that"),
        false,
      );
      return null;
    }
    applyTheater(data);
    return data;
  } catch {
    showHostMsg("Could not reach the server", false);
    return null;
  }
}

// The rows live in theater-picker.js, shared with the dashboard's theater panel
// so hosting from a phone and hosting from the dashboard look the same and stay
// that way. This end owns where a play comes from and where a message goes.
let lastResults = [];

function pickerOptions() {
  return {
    onPlay: async (item) => {
      tsMsg.hidden = true;
      const done = await hostAction("/api/admin/theater/play", {
        jf_id: item.jf_id, subtitles: tsSubs.checked,
      });
      if (done) closeModal(searchModal);
      else {
        tsMsg.textContent = hostMsg.textContent;
        tsMsg.hidden = false;
      }
      return !!done;
    },
    onBack: () => renderSearchResults(lastResults),
    message: (text) => {
      tsMsg.textContent = text;
      tsMsg.hidden = !text;
    },
  };
}

function renderSearchResults(results) {
  if (!results.length) {
    tsResults.textContent = "";
    tsMsg.textContent = "Nothing matched.";
    tsMsg.hidden = false;
    return;
  }
  lastResults = results;
  theaterPicker.render(tsResults, results, pickerOptions());
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
        reply.status === 502 ? "Projector offline" : (data.error || "Could not search");
      return;
    }
    tsMsg.hidden = true;
    renderSearchResults(data.results || []);
  } catch {
    tsMsg.textContent = "Could not reach the server";
  }
}

function setUpHost() {
  if (!hostStrip || !hostStrip.isConnected) return;
  // Everything here is admin only, so for everyone else it is removed rather
  // than hidden.
  if (!me || !me.admin) {
    hostStrip.remove();
    if (searchModal) searchModal.remove();
    return;
  }
  hostStart.addEventListener("click", () => hostAction("/api/admin/theater/session"));
  hostStop.addEventListener("click", () => hostAction("/api/admin/theater/stop"));
  // One click, no confirmation: the room is watching subtitles run out of sync
  // while it takes, and the worst case is the same film from the start.
  hostNoSubs.addEventListener("click", () => hostAction("/api/admin/theater/restart"));
  hostEnd.addEventListener("click", () => {
    if (!confirm("End the theater session? Chat is kept.")) return;
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
  // The channel's subtitle default, read once. Unchecked is the safe answer, so
  // a failure here leaves the box alone rather than reporting anything.
  fetch("/api/admin/stream")
    .then((reply) => (reply.ok ? reply.json() : null))
    .then((data) => {
      if (data && typeof data.theater_subtitles === "boolean") {
        tsSubs.checked = data.theater_subtitles;
      }
    })
    .catch(() => {});
}

// ---- chat and the call board ----

// A soft color per person, for the first-letter squares and for a name
// nobody has chosen a color for. Light enough to read on the charcoal.
function hueOf(seed) {
  let hash = 0;
  for (let i = 0; i < seed.length; i++) hash = (hash * 31 + seed.charCodeAt(i)) % 360;
  return hash;
}
function avatarColor(seed) { return `hsl(${hueOf(seed)}, 34%, 66%)`; }
function nameColor(seed) { return `hsl(${hueOf(seed)}, 45%, 76%)`; }

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
    // If the image cannot load, fall back to the initials square.
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

// Role marks beside a name, in place of any "(admin)" text. The host (admin)
// carries a small camera in the accent; a moderator an engraved tag. An admin
// keeps every moderator power, so an admin shows only the camera.
const CAMERA_SVG =
  '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.75" ' +
  'stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">' +
  '<rect x="3" y="6" width="12" height="12" rx="1.5"/><path d="m15 10.5 5-3v9l-5-3"/></svg>';

function roleBadgeNode(admin, mod, big) {
  if (!admin && !mod) return null;
  const span = document.createElement("span");
  if (admin) {
    span.className = "role-tag host" + (big ? " role-tag-lg" : "");
    span.title = "Host";
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
  if (chatLive) node.classList.add("enter");   // rise in only for live lines
  messages.appendChild(node);
  // Keep only the most recent lines so chat stays static but bounded.
  while (messages.children.length > MAX_VISIBLE_MESSAGES) {
    messages.removeChild(messages.firstChild);
  }
  if (stick) messages.scrollTop = messages.scrollHeight;
}

// One person's line: their square, their name and the time, and what they
// said. Shared by chat and highlights so both carry the same identity.
function personLine(msg, text, className) {
  const line = document.createElement("div");
  line.className = className;
  if (msg.id != null) line.dataset.msgid = msg.id;
  line.appendChild(avatarNode(msg.user, msg.name, msg.avatar || 0, false, true));
  const bodyWrap = document.createElement("div");
  bodyWrap.className = "msg-body";
  const head = document.createElement("div");
  head.className = "msg-head";
  const name = document.createElement("span");
  name.className = msg.admin ? "name admin" : "name";
  name.textContent = msg.name;
  // Each person's chosen name color, if any; otherwise their own soft hue.
  // Chosen colors are guarded server-side for readability.
  name.style.color = msg.name_color || nameColor(msg.user || msg.name || "?");
  head.appendChild(name);
  const badge = roleBadgeNode(msg.admin, msg.mod, false);
  if (badge) head.appendChild(badge);
  const time = document.createElement("span");
  time.className = "msg-time";
  time.textContent = formatTimestamp(msg.ts);
  head.appendChild(time);
  const body = document.createElement("span");
  body.className = "body";
  if (msg.deleted) {
    markBodyDeleted(body);
  } else {
    body.textContent = text;        // textContent keeps any HTML inert
    // Each person's own font and message color ride along on their messages
    // for everyone to see.
    body.style.fontFamily = FONTS[msg.font] || "";
    if (msg.msg_color) body.style.color = msg.msg_color;
  }
  bodyWrap.append(head, body);
  line.appendChild(bodyWrap);
  return line;
}

const DEL_SVG =
  '<svg class="i" viewBox="0 0 24 24" aria-hidden="true"><path d="M6 6l12 12M18 6 6 18"/></svg>';

function renderChat(msg) {
  const line = personLine(msg, msg.text, "msg");
  // Host and moderators can remove one message for everyone by its id: the
  // key shows on hover with a mouse, and on a long press with a thumb.
  if (me && (me.admin || me.mod) && msg.id != null) {
    const del = document.createElement("button");
    del.type = "button";
    del.className = "msg-del";
    del.title = "Delete message";
    del.setAttribute("aria-label", `Delete ${msg.name}'s message`);
    del.innerHTML = DEL_SVG;         // a static icon
    del.addEventListener("click", (e) => {
      e.stopPropagation();
      line.classList.remove("is-armed");
      if (socket && socket.readyState === WebSocket.OPEN) {
        socket.send(JSON.stringify({ type: "moddelete", id: msg.id }));
      }
    });
    line.appendChild(del);
    armOnLongPress(line);
  }
  addLine(line);
}

// A long press on a line (a thumb has no hover) shows its delete key. One
// line at a time; a tap anywhere else puts it away.
function armOnLongPress(line) {
  let timer = null;
  const cancel = () => { clearTimeout(timer); timer = null; };
  line.addEventListener("touchstart", () => {
    cancel();
    timer = setTimeout(() => {
      messages.querySelectorAll(".msg.is-armed").forEach((m) => m.classList.remove("is-armed"));
      line.classList.add("is-armed");
    }, 450);
  }, { passive: true });
  line.addEventListener("touchend", cancel);
  line.addEventListener("touchmove", cancel, { passive: true });
  line.addEventListener("contextmenu", (e) => {
    if (line.classList.contains("is-armed")) e.preventDefault();
  });
}
document.addEventListener("touchstart", (e) => {
  if (!e.target.closest(".msg.is-armed")) {
    messages.querySelectorAll(".msg.is-armed").forEach((m) => m.classList.remove("is-armed"));
  }
}, { passive: true });

// A line removed by a moderator stays in place but its text is replaced, so the
// conversation does not visibly reflow and everyone sees it was moderated.
function markBodyDeleted(body) {
  body.textContent = "deleted by a moderator";
  body.classList.add("deleted");
  body.style.fontFamily = "";
  body.style.color = "";
}

function applyDelete(id) {
  if (id == null) return;
  const line = messages.querySelector(`[data-msgid="${id}"]`);
  if (line) {
    const body = line.querySelector(".body");
    if (body) markBodyDeleted(body);
    const del = line.querySelector(".msg-del");
    if (del) del.remove();
  }
}

function renderSystem(msg) {
  const line = document.createElement("div");
  line.className = "msg system";
  const time = document.createElement("span");
  time.className = "msg-time";
  // A page-made line (the wipe explanations, the too-many-sockets notice) has no
  // server timestamp; formatTimestamp falls back to now, which is when it was.
  time.textContent = formatTimestamp(msg.ts);
  // The text keeps a span of its own so the line's pre-wrap still folds a
  // multi-line reply (/help) instead of the timestamp sharing its box.
  const body = document.createElement("span");
  body.textContent = msg.text;
  line.append(time, body);
  // A command that asks before it acts sends its answer with the line, as a
  // button in chat: a browser confirm cannot be styled, reads badly on a phone,
  // and is the wrong shape for something the room is about to see happen.
  if (typeof msg.confirm_command === "string") {
    const confirm = document.createElement("button");
    confirm.type = "button";
    confirm.className = "msg-confirm";
    confirm.textContent = msg.confirm_label || "Confirm";
    confirm.addEventListener("click", () => {
      confirm.disabled = true;
      sendChatCommand(msg.confirm_command);
    });
    line.appendChild(confirm);
  }
  addLine(line);
}

// A highlight is a chat line somebody spent points on, lifted out of the run
// of chat. It carries a message id (it is logged like a chat line), which is
// what lets applyDelete find and blank it when a moderator deletes it.
function renderHighlight(msg) {
  addLine(personLine(msg, msg.message, "msg highlight"));
}

// The call board. One lit line per person in the room, the host's marked with
// the accent. Somebody who has just left stays on the
// board, dimmed, for a few minutes, so a phone that dropped for a moment does
// not read as a friend who went home.
const RECENT_LEFT_MS = 5 * 60 * 1000;
let present = [];
const departed = new Map();      // username -> { viewer, at }

function renderPresence(msg) {
  lastViewerCount = msg.count;
  setViewerLabel();
  const now = Date.now();
  const here = new Set(msg.viewers.map((v) => v.username));
  present.forEach((v) => {
    if (!here.has(v.username)) departed.set(v.username, { viewer: v, at: now });
  });
  here.forEach((name) => departed.delete(name));
  present = msg.viewers.slice();
  renderBoard();
}

function lineLabel(viewer, away) {
  let label = viewer.name;
  if (me && viewer.username === me.username) label += " (you)";
  if (viewer.admin) label += ", host";
  if (away) {
    const mins = Math.max(1, Math.round((Date.now() - away) / 60000));
    label += `, left ${mins} min ago`;
  }
  return label;
}

function lineNode(viewer, away) {
  const item = document.createElement("li");
  const line = document.createElement("button");
  line.type = "button";
  line.className = "line";
  if (viewer.admin) line.classList.add("host");
  if (away) line.classList.add("away");
  const label = lineLabel(viewer, away);
  line.setAttribute("aria-label", label);
  line.title = label;
  const initial = document.createElement("span");
  initial.className = "line-initial";
  initial.textContent = ((viewer.name || viewer.username || "?").trim().charAt(0) || "?").toUpperCase();
  const name = document.createElement("span");
  name.className = "line-name";
  name.textContent = me && viewer.username === me.username ? "You" : viewer.name;
  line.append(initial, name);
  line.addEventListener("click", () => openProfile(viewer.username));
  item.appendChild(line);
  return item;
}

function renderBoard() {
  const now = Date.now();
  for (const [name, gone] of departed) {
    if (now - gone.at > RECENT_LEFT_MS) departed.delete(name);
  }
  // The host first, then everybody in the order they arrived.
  const ordered = present.slice().sort((a, b) => (b.admin ? 1 : 0) - (a.admin ? 1 : 0));
  lines.textContent = "";
  ordered.forEach((v) => lines.appendChild(lineNode(v, null)));
  departed.forEach((gone) => lines.appendChild(lineNode(gone.viewer, gone.at)));
}
// Departures age off the board on their own.
setInterval(() => { if (departed.size) renderBoard(); }, 30000);

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
      messages.textContent = "";
      if (msg.reason === "new_night") {
        renderSystem({ text: "A new night. Chat starts fresh." });
      } else if (msg.reason === "idle") {
        renderSystem({ text: "Last night's chat has been cleared." });
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
  // valid account behind the cookie) and 4403 (country) are answers, not
  // blips, and retrying every three seconds forever would be a loop that only
  // stops when the tab closes.
  socket.addEventListener("close", (event) => {
    if (event.code === 4401 || event.code === 4403) return;
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

// The chat fonts people can pick (on the options page). Their messages carry
// the key; this maps it to the face.
const FONTS = {
  system: "",
  jetbrains: "'JetBrains Mono', monospace",
  grotesk: "'Space Grotesk', sans-serif",
  plex: "'IBM Plex Sans', sans-serif",
  sora: "'Sora', sans-serif",
};

const roomFullRetry = document.getElementById("room-full-retry");
if (roomFullRetry) {
  roomFullRetry.addEventListener("click", () => {
    hideRoomFull();
    fullUntil = 0;
    checkStream();
  });
}

// ---- hiding chat, on a wide screen ----
// The column can step aside so the monitor has the width, and the choice is
// remembered. It is a body class: the grid keys off it.

const CHAT_OPEN_KEY = "selfstream_chat_open";

function setChatOpen(open, remember) {
  document.body.classList.toggle("chat-collapsed", !open);
  chatToggle.setAttribute("aria-pressed", open ? "false" : "true");
  const label = open ? "Hide chat" : "Show chat";
  chatToggle.setAttribute("aria-label", label);
  chatToggle.title = label;
  if (remember) {
    try { localStorage.setItem(CHAT_OPEN_KEY, open ? "1" : "0"); } catch (e) {}
  }
}

const wide = window.matchMedia("(min-width: 960px)");
function renderChatToggle() {
  // Only where chat is a column beside the picture, and never in the frame,
  // where the dashboard decides what shows.
  chatToggle.hidden = !wide.matches || framed;
}
wide.addEventListener("change", renderChatToggle);
chatToggle.addEventListener("click", () => {
  setChatOpen(document.body.classList.contains("chat-collapsed"), true);
});
let chatOpenSaved = "1";
try { chatOpenSaved = localStorage.getItem(CHAT_OPEN_KEY) || "1"; } catch (e) {}
setChatOpen(framed || chatOpenSaved !== "0", false);

// ---- somebody's card (tap a name, a square, or a line on the board) ----

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

// Host and moderators get quick moderation actions inside a viewer's card:
// timeout, purge, ban, and (admin only) promote/demote. Each sends the
// matching chat command over the socket; the server authorizes it against a
// fresh role read and replies privately with the outcome.
function buildModActions(data) {
  profileModActions.hidden = true;
  profileModActions.textContent = "";
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
    btn.className = label === "Ban" ? "btn danger" : "btn";
    btn.textContent = label;
    btn.addEventListener("click", () => { fn(); closeModal(profileModal); });
    profileModActions.appendChild(btn);
  });
  profileModActions.hidden = false;
}

function formatJoined(ts) {
  if (!ts) return "";
  const d = new Date(ts * 1000);
  return `Joined ${d.toLocaleDateString(undefined, { month: "long", year: "numeric" })}`;
}

async function openProfile(username) {
  try {
    const data = await (await fetch(`/api/profile/${encodeURIComponent(username)}`)).json();
    profileAvatar.textContent = "";
    profileAvatar.appendChild(avatarNode(data.username, data.name, data.avatar || 0, true, false));
    const badge = roleBadgeNode(data.admin, data.mod, true);
    if (badge) profileAvatar.appendChild(badge);
    profileName.textContent = data.name;
    profileBio.textContent = data.bio || "";
    profileBio.hidden = !data.bio;
    profileJoined.textContent = formatJoined(data.joined);
    profilePoints.textContent = data.points ? `${data.points} points` : "";
    buildModActions(data);
    openModal(profileModal);
  } catch {
    /* a failed lookup just does nothing */
  }
}

// ---- modal helpers ----

let modalOpener = null;
function openModal(m) {
  modalOpener = document.activeElement;
  m.hidden = false;
  const focusable = m.querySelector("input, button:not(.modal-close)");
  if (focusable) focusable.focus();
}
function closeModal(m) {
  m.hidden = true;
  if (modalOpener && modalOpener.focus) modalOpener.focus();
  modalOpener = null;
}
document.querySelectorAll(".modal").forEach((m) => {
  m.addEventListener("click", (e) => {
    if (e.target === m || e.target.closest("[data-close]")) closeModal(m);
  });
});
document.addEventListener("keydown", (e) => {
  if (e.key === "Escape") {
    document.querySelectorAll(".modal:not([hidden])").forEach(closeModal);
  }
});

video.addEventListener("playing", () => {
  renderSound();
  // Safari plays HLS natively, with no FRAG_BUFFERED to hang the card off.
  armNowShowingHide();
});

// Keep the page exactly as tall as the visible viewport so the monitor, the last
// message, and the composer stay on screen on a phone as the address bar or the
// keyboard slides in and out. When the keyboard opens the ONLY thing that should
// change is the messages list shrinking - the monitor stays put and the input
// stays pinned above the keyboard. The body is overflow:hidden, but iOS still
// scrolls the visual viewport and displaces the whole layout, so we re-measure
// --vvh and force the window back to the top on every viewport change.
function lockHeight() {
  const vv = window.visualViewport;
  const height = (vv && vv.height) || window.innerHeight;
  document.documentElement.style.setProperty("--vvh", height + "px");
  // Counteract any page displacement the keyboard caused.
  if (getComputedStyle(document.body).overflow === "hidden"
      && (window.pageYOffset !== 0 || window.pageXOffset !== 0)) {
    window.scrollTo(0, 0);
  }
}
if (window.visualViewport) {
  // Some mobile browsers only fire "scroll" (not "resize") when the bottom
  // toolbar or the keyboard slides in or out, which changes the visible height,
  // so listen to both. Otherwise the composer can end up hidden behind them.
  window.visualViewport.addEventListener("resize", lockHeight);
  window.visualViewport.addEventListener("scroll", lockHeight);
}
window.addEventListener("resize", lockHeight);
window.addEventListener("orientationchange", lockHeight);
// Re-measure shortly after load too; the first value can be taken before the
// browser chrome has settled.
window.addEventListener("load", () => setTimeout(lockHeight, 200));
lockHeight();

// Focusing the chat input opens the keyboard. Once the viewport has settled,
// re-measure and pin the newest message to the bottom so it stays in view
// above the keyboard. Blur reverses it.
function settleAfterKeyboard() {
  // 300ms covers the keyboard slide-in on both iOS and Android; re-measure a
  // couple of times because the viewport height arrives in stages.
  [120, 300].forEach((t) => setTimeout(() => {
    lockHeight();
    messages.scrollTop = messages.scrollHeight;
  }, t));
}
chatInput.addEventListener("focus", settleAfterKeyboard);
chatInput.addEventListener("blur", settleAfterKeyboard);

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

// ---- channel points and the highlight ----
// The balance and the spend live behind the small tool beside the clip tool,
// so the composer stays one input and one Send.

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
  pointsBtn.hidden = false;
  const label = `Your points: ${n}. Highlight a message`;
  pointsBtn.setAttribute("aria-label", label);
  pointsBtn.title = label;
  highlightBalance.textContent = `${n} ${n === 1 ? "point" : "points"}`;
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
    highlightCostEl.textContent = String(highlightCost);
    setPoints(data.points || 0);
  } catch {
    /* leave the tool as it is */
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
      setPoints(data.points);
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

pointsBtn.addEventListener("click", () => {
  highlightMsg.hidden = true;
  openModal(highlightModal);
  loadPoints();   // fetch a fresh balance and cost each time it opens
  // Opening it between streams should say why the send is greyed out rather
  // than leave a dead button with no explanation.
  if (!streamOnline) {
    showHighlightMsg("Highlights show on stream, and the stream is offline right now.", false);
  }
});

// ---- embedded in the dashboard ----
// The dashboard shows this page in a frame so the streamer sees exactly what the
// room does, and can ask it to drop the video and keep chat alone. The frame is
// spoken to with postMessage rather than reloaded, so toggling the view never
// costs the chat socket. Only our own origin is listened to.

let videoHidden = false;

function setVideoShown(show) {
  if (show === !videoHidden) return;    // already there; ignore repeat asks
  videoHidden = !show;
  document.body.classList.toggle("chat-only", videoHidden);
  // Chat alone with chat hidden would leave the frame showing nothing, so
  // asking for chat alone opens it. The saved preference is left alone: this
  // is the dashboard's view, not the viewer's choice.
  if (videoHidden) setChatOpen(true, false);
  if (show) {
    // Rebuilds the player at the live edge, rather than resuming seconds behind
    // where it was paused.
    checkStream();
    return;
  }
  // Hidden means hidden: tear the player down so it is not still pulling the
  // stream behind an element nobody can see.
  try {
    video.pause();
    if (hls) { hls.destroy(); hls = null; }
  } catch (e) { /* nothing to stop */ }
}

// In the frame the dashboard draws the slate itself, so the sound key lives
// there and asks for the sound over the same channel.
function setSoundOn(on) {
  video.muted = !on;
  if (on) video.play().catch(() => {});
  renderSound();
}

window.addEventListener("message", (event) => {
  if (event.origin !== location.origin) return;
  const msg = event.data;
  if (!msg) return;
  if (msg.type === "video") setVideoShown(!!msg.show);
  else if (msg.type === "sound") setSoundOn(!!msg.on);
});

async function boot() {
  if (!(await requireAuth())) return;
  // The strip is fed by this page's own status poll rather than polling
  // alongside it. The room is a landing page: members who sign in while the
  // stream is live arrive here, so the one-time notices can show here too.
  strip = mountNav(me, { current: "watch", poll: false, landing: true });
  // Let moderators and admins know the commands exist, without cluttering chat
  // for everyone else.
  if (me && (me.admin || me.mod)) {
    chatInput.placeholder = "Chat, or /help";
  }
  // The server says up front whether theater is on at all; asking for its
  // state on a server without it would only be answered with a 404.
  if (me.theater === false) dropTheater();
  setUpHost();
  clipBtn.hidden = false;
  loadPoints();
  renderChatToggle();
  loadChannel();
  // Before the first status poll, so an intermission never flashes the offline
  // card on the way in.
  await loadTheater();
  connectChat();
  checkStream();
}

boot();
