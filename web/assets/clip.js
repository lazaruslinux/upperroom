// The public clip page.
//
// Reachable without a session, which makes it the one page on the site with no
// account behind it. Two rules follow from that and both are deliberate:
//
//   1. It calls exactly one endpoint, /api/shared/<token>, and that endpoint
//      returns only a title, a length and a file name. No creator, because that
//      is an account username. No chat replay, because a replay carries every
//      chatter's display name and avatar and none of them agreed to be
//      published. No viewer list, no library, no points.
//
//   2. It never calls anything that expects a session. A 401 here would be a
//      broken page for the visitor and a hint about what exists for anyone
//      else, so the page simply does not know those endpoints are there.
//
// The token in the URL is the whole credential. There is nothing to sign in to
// and nothing to guess at: an unknown token is answered the same way a revoked
// one is.

const loading = document.getElementById("loading");
const body = document.getElementById("clip-body");
const missing = document.getElementById("missing");

function showMissing() {
  loading.hidden = true;
  body.hidden = true;
  missing.hidden = false;
}

function formatDuration(seconds) {
  const s = Math.max(0, Math.round(seconds || 0));
  const m = Math.floor(s / 60);
  return m > 0
    ? `${m}:${String(s % 60).padStart(2, "0")}`
    : `${s} seconds`;
}

(async () => {
  // /clip/<token>. Caddy serves this one page for every token and the token is
  // read back off the path here.
  const token = decodeURIComponent(
    window.location.pathname.replace(/^\/clip\/?/, "")
  ).trim();
  if (!token) {
    showMissing();
    return;
  }

  let clip;
  try {
    const reply = await fetch(`/api/shared/${encodeURIComponent(token)}`);
    if (!reply.ok) {
      showMissing();
      return;
    }
    clip = await reply.json();
  } catch {
    showMissing();
    return;
  }

  document.getElementById("clip-title").textContent = clip.name || "A clip";
  // The date it was made is fine to show; who made it is not.
  const when = clip.created_at
    ? new Date(clip.created_at * 1000).toLocaleDateString()
    : "";
  document.getElementById("clip-meta").textContent =
    [formatDuration(clip.duration), when].filter(Boolean).join(" · ");

  const video = document.getElementById("clip-video");
  video.src = clip.video;
  if (clip.poster) video.poster = clip.poster;
  // The title is the clip's name, which the operator chose, so it is safe here
  // in a way the streamer's own identity is not.
  document.title = clip.name || "A clip";

  loading.hidden = true;
  body.hidden = false;
})();
