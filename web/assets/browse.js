// Past broadcasts: recordings on one tab, viewer clips on the other, each card
// opening that item on the media page. Search lives here too: titles only,
// matched in the browser across both kinds, because the listings are small and
// already fetched whole for the grid.

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
  me = data;
  return true;
}

const libGrid = document.getElementById("lib-grid");
const libEmpty = document.getElementById("lib-empty");
const clipFilter = document.getElementById("clip-filter");
const mineOnlyToggle = document.getElementById("mine-only");
const search = document.getElementById("lib-search");
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
  if (secs < 3600) return `${Math.max(1, Math.floor(secs / 60))} min ago`;
  if (secs < 86400) return `${Math.floor(secs / 3600)} h ago`;
  if (secs < 2592000) return `${Math.floor(secs / 86400)} d ago`;
  return new Date(epoch * 1000).toLocaleDateString();
}

function mediaCard(item, kind, tagged) {
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
    // A poster the store cannot serve leaves a dark screen, not a broken icon.
    img.addEventListener("error", () => img.remove());
    thumb.appendChild(img);
  }
  if (item.duration) {
    const dur = document.createElement("span");
    dur.className = "media-dur";
    dur.textContent = durationClock(item.duration);
    thumb.appendChild(dur);
  }
  a.appendChild(thumb);

  const title = document.createElement("div");
  title.className = "media-title";
  title.textContent = (kind === "vod" ? item.title : item.name) || "Untitled";
  const sub = document.createElement("div");
  sub.className = "media-sub";
  const bits = [];
  if (tagged) bits.push(kind === "vod" ? "Broadcast" : "Clip");
  bits.push(item.views === 1 ? "1 view" : `${item.views} views`);
  bits.push(relDate(kind === "vod" ? item.started_at : item.created_at));
  if (kind === "clip" && item.creator) bits.push(`@${item.creator}`);
  sub.textContent = bits.join(" · ");
  a.append(title, sub);
  return a;
}

async function load(tab) {
  if (libCache[tab] === null) {
    try {
      const reply = await fetch(`/api/${tab}`);
      libCache[tab] = reply.ok ? (await reply.json())[tab] || [] : [];
    } catch {
      libCache[tab] = [];
    }
  }
  return libCache[tab];
}

async function renderLibrary() {
  const query = search.value.trim().toLowerCase();
  libGrid.textContent = "";
  // A search looks through both kinds at once, newest first within each.
  if (query.length >= 2) {
    clipFilter.hidden = true;
    const [vods, clips] = await Promise.all([load("vods"), load("clips")]);
    const hits = []
      .concat(vods.filter((v) => (v.title || "").toLowerCase().includes(query)).map((v) => [v, "vod"]))
      .concat(clips.filter((c) => (c.name || "").toLowerCase().includes(query)).map((c) => [c, "clip"]));
    libEmpty.hidden = hits.length > 0;
    libEmpty.textContent = "Nothing is called that.";
    hits.forEach(([item, kind]) => libGrid.appendChild(mediaCard(item, kind, true)));
    return;
  }
  const kind = libTab === "vods" ? "vod" : "clip";
  const items = await load(libTab);
  // The "my clips only" filter applies to the clips tab for every role.
  clipFilter.hidden = libTab !== "clips";
  let display = items;
  if (libTab === "clips" && mineOnly && me) {
    display = items.filter((c) => c.creator === me.username);
  }
  if (!display.length) {
    libEmpty.hidden = false;
    if (libTab === "vods") {
      libEmpty.textContent = "No past broadcasts yet. They appear here after a stream ends.";
    } else if (mineOnly) {
      libEmpty.textContent = "You haven't made any clips yet.";
    } else {
      libEmpty.textContent = "No clips yet. Anyone watching can clip the stream while it is live.";
    }
    return;
  }
  libEmpty.hidden = true;
  display.forEach((item) => libGrid.appendChild(mediaCard(item, kind, false)));
}

document.querySelectorAll(".lib-tab").forEach((tab) => {
  tab.addEventListener("click", () => {
    libTab = tab.dataset.tab;
    document.querySelectorAll(".lib-tab").forEach((t) => {
      t.classList.toggle("selected", t === tab);
      t.setAttribute("aria-selected", t === tab ? "true" : "false");
    });
    search.value = "";
    renderLibrary();
  });
});

mineOnlyToggle.addEventListener("change", () => {
  mineOnly = mineOnlyToggle.checked;
  renderLibrary();
});

let searchTimer = null;
search.addEventListener("input", () => {
  clearTimeout(searchTimer);
  searchTimer = setTimeout(renderLibrary, 200);
});

async function boot() {
  if (!(await requireAuth())) return;
  mountNav(me, { current: "browse", pageName: "past broadcasts" });
  renderLibrary();
}

boot();
