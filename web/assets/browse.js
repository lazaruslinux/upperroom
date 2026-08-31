// Browse page. The archive: past broadcasts on one tab, viewer clips on the
// other, each card opening that item on the media page.
//
// This was a section at the bottom of the home page. It is its own page now,
// so home can be about what is happening right this minute and the library gets
// the whole width.

let me = null;

async function requireAuth() {
  let data;
  try {
    data = await (await fetch("/api/me")).json();
  } catch {
    data = { authed: false };
  }
  if (!data.authed) {
    window.location.href = "/";
    return false;
  }
  // A guest pass buys the stream and chat, not the archive of what they missed.
  // Send them where their pass actually works rather than rendering a page whose
  // every request will 401.
  if (data.guest) {
    window.location.href = "/watch";
    return false;
  }
  me = data;
  return true;
}

// The accent is the channel's brand and is server-driven. The head bootstrap
// paints the last-seen value from localStorage; this syncs it with the server
// and remembers it for the next no-flash paint.
function applyAccent(value) {
  if (!["green", "amber", "blue", "ghost"].includes(value)) return;
  if (document.documentElement.dataset.accent !== value) {
    document.documentElement.dataset.accent = value;
    try { localStorage.setItem("selfstream_accent", value); } catch (e) {}
  }
}

// ---- library (past VODs + clips) ----

const libGrid = document.getElementById("lib-grid");
const libEmpty = document.getElementById("lib-empty");
const clipFilter = document.getElementById("clip-filter");
const mineOnlyToggle = document.getElementById("mine-only");
let libTab = "vods";
let mineOnly = false;
const libCache = { vods: null, clips: null };

function durationClock(secs) {
  secs = Math.max(0, Math.round(secs || 0));
  const h = Math.floor(secs / 3600);
  const m = Math.floor((secs % 3600) / 60);
  const s = secs % 60;
  const pad = (n) => String(n).padStart(2, "0");
  return h > 0 ? `${h}:${pad(m)}:${pad(s)}` : `${m}:${pad(s)}`;
}

function relDate(epoch) {
  if (!epoch) return "";
  const secs = Math.floor(Date.now() / 1000) - epoch;
  if (secs < 3600) return `${Math.max(1, Math.floor(secs / 60))}m ago`;
  if (secs < 86400) return `${Math.floor(secs / 3600)}h ago`;
  if (secs < 2592000) return `${Math.floor(secs / 86400)}d ago`;
  return new Date(epoch * 1000).toLocaleDateString();
}

function mediaCard(item, kind) {
  const a = document.createElement("a");
  a.className = "media-card";
  a.href = `/media?type=${kind}&id=${item.id}`;

  const thumb = document.createElement("div");
  thumb.className = "media-thumb";
  if (item.poster) {
    const img = document.createElement("img");
    img.src = `/media/${kind}s/${item.id}.jpg`;
    img.alt = "";
    img.loading = "lazy";
    thumb.appendChild(img);
  } else {
    thumb.classList.add("media-thumb-fallback");
    const mark = document.createElement("span");
    mark.className = "thumb-mark";
    mark.textContent = "no signal";
    thumb.appendChild(mark);
  }
  if (item.duration) {
    const dur = document.createElement("span");
    dur.className = "media-dur";
    dur.textContent = durationClock(item.duration);
    thumb.appendChild(dur);
  }
  a.appendChild(thumb);

  const meta = document.createElement("div");
  meta.className = "media-meta";
  const title = document.createElement("div");
  title.className = "media-title";
  title.textContent = kind === "vod" ? item.title : item.name;
  const sub = document.createElement("div");
  sub.className = "media-sub muted";
  const views = item.views === 1 ? "1 view" : `${item.views} views`;
  const when = relDate(kind === "vod" ? item.started_at : item.created_at);
  let line = `${views} · ${when}`;
  if (kind === "clip" && item.creator) line += ` · @${item.creator}`;
  sub.textContent = line;
  meta.append(title, sub);
  a.appendChild(meta);
  return a;
}

async function renderLibrary() {
  const kind = libTab === "vods" ? "vod" : "clip";
  let items = libCache[libTab];
  if (items === null) {
    try { items = (await (await fetch(`/api/${libTab}`)).json())[libTab] || []; }
    catch { items = []; }
    libCache[libTab] = items;
  }
  // The "my clips only" filter applies to the clips tab for every role.
  clipFilter.hidden = libTab !== "clips";
  let display = items;
  if (libTab === "clips" && mineOnly && me) {
    display = items.filter((c) => c.creator === me.username);
  }

  libGrid.innerHTML = "";
  if (!display.length) {
    libEmpty.hidden = false;
    if (libTab === "vods") {
      libEmpty.textContent = "No past broadcasts yet. They appear here after a stream ends.";
    } else if (mineOnly) {
      libEmpty.textContent = "You haven't made any clips yet.";
    } else {
      libEmpty.textContent = "No clips yet. Viewers can clip the recent stream while live.";
    }
    return;
  }
  libEmpty.hidden = true;
  display.forEach((item) => libGrid.appendChild(mediaCard(item, kind)));
}

document.querySelectorAll(".lib-tab").forEach((tab) => {
  tab.addEventListener("click", () => {
    libTab = tab.dataset.tab;
    document.querySelectorAll(".lib-tab").forEach((t) => t.classList.toggle("selected", t === tab));
    renderLibrary();
  });
});

mineOnlyToggle.addEventListener("change", () => {
  mineOnly = mineOnlyToggle.checked;
  renderLibrary();
});

async function boot() {
  if (!(await requireAuth())) return;
  // One status call covers the accent and the site name the bar wants, rather
  // than letting the bar fetch the same thing again.
  let status = {};
  try { status = await (await fetch("/api/status")).json(); } catch (e) {}
  applyAccent(status.accent);
  mountNav(me, { current: "browse", siteName: status.site_name });
  renderLibrary();
}

boot();
