"""
Media routes: the live preview thumbnail, VOD and clip listings and playback
metadata, view counting, clip creation, and the stream status the player polls.

The media files themselves are served straight from disk by Caddy at /media/*
(behind the same session check as the live video), so large files never pass
through this Python service; only metadata and view counting live here.
"""

import html
import logging
import os
import secrets
import time

from fastapi import APIRouter, Request, Response
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse

import db
from auth import (
    GUEST_REFUSED, admin_user, can_moderate, member_user, read_session,
    session_user,
)
from config import (
    CLIP_DIR, CLIP_LENGTHS, COOKIE_NAME, MAX_CLIP_NAME, MAX_COMMENT_LENGTH,
    SCHEDULE_GRACE, SITE_URL, THUMB_INTERVAL, THUMB_PATH, VERSION, VOD_DIR,
    WEB_DIR,
)
from hub import hub
from media import (
    fetch_path, link_shared, make_clip, ready_epoch, unlink_shared,
    _remove_media_files,
)

logger = logging.getLogger("upperroom.media")

router = APIRouter()


# ---- Thumbnail ------------------------------------------------------------

@router.get("/api/thumbnail")
def thumbnail(request: Request):
    # The home card preview. Signed in viewers only, and never cached so the
    # frame stays current. 404 means the stream is offline (no fresh frame).
    if not read_session(request.cookies.get(COOKIE_NAME, "")):
        return Response(status_code=401)
    if not os.path.exists(THUMB_PATH):
        return Response(status_code=404)
    return FileResponse(
        THUMB_PATH,
        media_type="image/jpeg",
        headers={"Cache-Control": "no-store"},
    )


# ---- Link preview ---------------------------------------------------------
# Sharing the watch page in a chat app should say who is streaming what, with a
# frame of it. That means the page and its picture have to answer a fetcher that
# has no session, because a preview fetcher never has one. Only these two
# routes are public; the video, the chat socket and the library behind them are
# unchanged and still need an account.

_OG_START = "<!-- og:start -->"
_OG_END = "<!-- og:end -->"

# The watch page split around its preview block, cached by modification time.
# web/ is live mounted, so a front-end deploy changes the file under a running
# gate; keying on mtime picks that up without a restart and without reading the
# file on every request.
_watch_page = {"mtime": None, "head": "", "tail": None}


def _watch_template():
    """(head, tail) of web/watch.html around its preview markers. A tail of None
    means the markers are missing and the page should be served untouched, rather
    than guessing where the tags belong."""
    path = os.path.join(WEB_DIR, "watch.html")
    stamp = os.path.getmtime(path)
    if _watch_page["mtime"] != stamp:
        with open(path, encoding="utf-8") as fh:
            text = fh.read()
        start = text.find(_OG_START)
        end = text.find(_OG_END)
        if start == -1 or end == -1:
            _watch_page.update(mtime=stamp, head=text, tail=None)
        else:
            _watch_page.update(
                mtime=stamp, head=text[:start], tail=text[end + len(_OG_END):],
            )
    return _watch_page["head"], _watch_page["tail"]


def _absolute(request, path):
    """An absolute URL for a preview tag. A fetcher is not a browser and will not
    resolve a relative og:image, so it has to be spelled out. The operator's
    configured site URL wins; otherwise use the host this request arrived on."""
    if SITE_URL:
        return f"{SITE_URL}{path}"
    host = request.headers.get("host", "")
    if not host:
        return path
    proto = request.headers.get("x-forwarded-proto") or request.url.scheme
    return f"{proto}://{host}{path}"


def _preview_text():
    """(site, title, description) for the preview, from the channel's own
    settings. The streamer is the longest-standing admin, the same account the
    home card calls the channel owner, so this needs no setting of its own."""
    info = db.get_stream_info()
    site = info["site_name"] or "upperroom"
    owner = db.channel_owner() or {}
    who = owner.get("display_name") or owner.get("username") or site
    game = db.get_now_playing()
    if hub.is_live():
        title = (
            f"{site}: {who} is streaming {game}!" if game
            else f"{site}: {who} is live now!"
        )
        description = (
            info["stream_title"] or info["stream_description"] or "Live now."
        )
    else:
        title = site
        description = (
            info["stream_description"] or "The stream is offline right now."
        )
    return site, title, description


def _og_block(request):
    """The preview tags themselves. Every value is operator-entered text going
    into an HTML attribute, so every one of them is escaped."""
    site, title, description = _preview_text()
    # A frame the picture worker refreshes on an interval, so the buster moves at
    # the same rate: fresh enough to be the current picture, stable enough that
    # two shares in the same window do not both re-fetch.
    image = _absolute(
        request, f"/api/og-image.jpg?t={int(time.time()) // max(THUMB_INTERVAL, 1)}"
    )
    page = _absolute(request, "/watch")

    def esc(value):
        return html.escape(str(value), quote=True)

    return "\n    ".join([
        '<meta property="og:type" content="video.other">',
        f'<meta property="og:site_name" content="{esc(site)}">',
        f'<meta property="og:title" content="{esc(title)}">',
        f'<meta property="og:description" content="{esc(description)}">',
        f'<meta property="og:url" content="{esc(page)}">',
        f'<meta property="og:image" content="{esc(image)}">',
        '<meta property="og:image:width" content="640">',
        '<meta property="og:image:height" content="360">',
        # Without this the frame renders as a small square chip beside the text
        # rather than the wide picture the share is for.
        '<meta name="twitter:card" content="summary_large_image">',
        f'<meta name="twitter:title" content="{esc(title)}">',
        f'<meta name="twitter:description" content="{esc(description)}">',
        f'<meta name="twitter:image" content="{esc(image)}">',
    ])


@router.api_route("/watch", methods=["GET", "HEAD"])
def watch_page(request: Request):
    """The watch page, rendered rather than served from disk, so its preview can
    name the stream. Public deliberately: a page a fetcher cannot read previews
    as nothing. What comes back is only the shell. The video, the chat socket and
    the library each check the session on their own, and watch.js sends a visitor
    without one to the sign-in page."""
    try:
        head, tail = _watch_template()
    except OSError:
        # Nothing to serve and nothing to fake. Caddy still has the file, so let
        # its static handler answer instead of returning a broken page.
        logger.warning("watch page unreadable under %s", WEB_DIR, exc_info=True)
        return Response(status_code=404)
    body = head if tail is None else head + _og_block(request) + tail
    # No cache header here: Caddy sets one policy for every dynamic path, and a
    # second one from this end only produces a duplicate to disagree with later.
    return HTMLResponse(body)


@router.api_route("/api/og-image.jpg", methods=["GET", "HEAD"])
def og_image():
    """The picture a link preview shows, public for the same reason the page is.

    While a broadcast is running this is the current frame, the same one the home
    card shows. The picture worker deletes that file when the stream ends, so
    between broadcasts this falls back to the channel's static card and no frame
    of anything is reachable."""
    # Caching is Caddy's policy for both of these, as it is for every other
    # dynamic path; the timestamp on the og:image URL is what decides how often
    # a fetcher asks for a new frame.
    if os.path.exists(THUMB_PATH):
        return FileResponse(THUMB_PATH, media_type="image/jpeg")
    fallback = os.path.join(WEB_DIR, "assets", "icons", "og-default.png")
    if os.path.exists(fallback):
        return FileResponse(fallback, media_type="image/png")
    return Response(status_code=404)


# ---- VODs and clips -------------------------------------------------------
# Metadata and view counting live here; the media files themselves are served
# straight from disk by Caddy at /media/* (behind the same session check as the
# live video), so large files never pass through this Python service.

def _signed_in(request):
    """Whether this request may see the library.

    Members only: the recordings and clips, and the chat replay attached to
    them, are not part of what a guest pass buys. A guest is watching a
    broadcast, not browsing an archive of the ones they missed."""
    return member_user(request) is not None


def _media_summary(row, kind):
    """Shape a VOD or clip row for a listing, including whether a poster exists."""
    folder = VOD_DIR if kind == "vod" else CLIP_DIR
    has_poster = bool(row.get("id")) and os.path.exists(
        os.path.join(folder, f"{row['id']}.jpg")
    )
    out = {
        "id": row["id"],
        "filename": row.get("filename"),
        "duration": row.get("duration") or 0,
        "views": row.get("views") or 0,
        "poster": has_poster,
        "keep": bool(row.get("keep")),
    }
    if kind == "vod":
        out.update(
            title=row["title"], description=row.get("description") or "",
            started_at=row["started_at"],
        )
    else:
        out.update(
            name=row["name"], creator=row.get("creator"),
            created_at=row["created_at"],
            # Whether it is public, and the link if so. Only ever sent to
            # signed-in members; the public page gets its own shape from
            # /api/shared and never sees this one.
            shared=bool(row.get("share_token")),
            share_url=(f"/clip/{row['share_token']}" if row.get("share_token") else None),
        )
    return out


@router.get("/api/vods")
def list_vods(request: Request):
    if not _signed_in(request):
        return Response(status_code=401)
    return {"vods": [_media_summary(v, "vod") for v in db.list_vods()]}


@router.get("/api/clips")
def list_clips(request: Request):
    if not _signed_in(request):
        return Response(status_code=401)
    return {"clips": [_media_summary(c, "clip") for c in db.list_clips()]}


@router.get("/api/vods/{vod_id}")
def get_vod(vod_id: int, request: Request):
    if not _signed_in(request):
        return Response(status_code=401)
    vod = db.get_vod(vod_id)
    if not vod or not vod["ready"]:
        return JSONResponse({"error": "No such VOD."}, status_code=404)
    return _media_summary(vod, "vod")


@router.get("/api/clips/{clip_id}")
def get_clip(clip_id: int, request: Request):
    if not _signed_in(request):
        return Response(status_code=401)
    clip = db.get_clip(clip_id)
    if not clip:
        return JSONResponse({"error": "No such clip."}, status_code=404)
    return _media_summary(clip, "clip")


@router.post("/api/vods/{vod_id}/view")
def view_vod(vod_id: int, request: Request):
    user = member_user(request)
    if not user:
        return Response(status_code=401)
    if not db.get_vod(vod_id):
        return JSONResponse({"error": "No such VOD."}, status_code=404)
    return {"views": db.add_view("vod", vod_id, user["username"])}


@router.post("/api/clips/{clip_id}/view")
def view_clip(clip_id: int, request: Request):
    user = member_user(request)
    if not user:
        return Response(status_code=401)
    if not db.get_clip(clip_id):
        return JSONResponse({"error": "No such clip."}, status_code=404)
    return {"views": db.add_view("clip", clip_id, user["username"])}


@router.get("/api/vods/{vod_id}/chat")
def vod_chat(vod_id: int, request: Request):
    if not _signed_in(request):
        return Response(status_code=401)
    return {"messages": db.get_replay("vod", vod_id)}


@router.get("/api/clips/{clip_id}/chat")
def clip_chat(clip_id: int, request: Request):
    if not _signed_in(request):
        return Response(status_code=401)
    return {"messages": db.get_replay("clip", clip_id)}


@router.post("/api/clip")
async def create_clip_endpoint(request: Request):
    if not session_user(request):
        return JSONResponse({"error": "Sign in first."}, status_code=401)
    # A clip outlives the broadcast and carries its maker's name, so it needs an
    # account that will still be there tomorrow.
    user = member_user(request)
    if not user:
        return JSONResponse({"error": GUEST_REFUSED}, status_code=403)
    body = await request.json()
    # The instant the viewer pressed Clip, as epoch seconds, taken from what the
    # player was actually showing. Optional: a browser that cannot work it out
    # sends nothing and the server falls back to its own clock. Anything
    # unparseable is treated as absent rather than rejected, since a bad clock
    # should cost accuracy, not the clip. make_clip clamps it to the recording,
    # so this value can never reach outside the current broadcast.
    try:
        at = float(body["at"]) if body.get("at") is not None else None
    except (TypeError, ValueError):
        at = None
    # How much of the live edge to take. Unlike `at`, a bad value is refused
    # rather than ignored: it means the client and the server disagree about
    # what may be asked for, and silently cutting a different length than the
    # viewer picked is worse than saying no. Absent means "use the setting".
    seconds = body.get("seconds")
    if seconds is not None and seconds not in CLIP_LENGTHS:
        return JSONResponse(
            {"error": "Pick one of the offered clip lengths."}, status_code=400
        )
    clip_id, error = await make_clip(
        user, body.get("name"), at=at, seconds=seconds
    )
    if error:
        return JSONResponse({"error": error}, status_code=400)
    return {"ok": True, "id": clip_id}


@router.post("/api/clips/{clip_id}/name")
async def rename_clip(clip_id: int, request: Request):
    """Name a clip after it exists. The watch page saves first and asks for a
    name second, so the clip is never lost to a slow typist; the clip's own page
    offers the same edit later."""
    if not session_user(request):
        return JSONResponse({"error": "Sign in first."}, status_code=401)
    user = member_user(request)
    if not user:
        return JSONResponse({"error": GUEST_REFUSED}, status_code=403)
    clip = db.get_clip(clip_id)
    if not clip:
        return JSONResponse({"error": "No such clip."}, status_code=404)
    if clip["creator"] != user["username"] and not can_moderate(user):
        return JSONResponse({"error": "Not yours to rename."}, status_code=403)
    body = await request.json()
    name = str(body.get("name") or "").strip()[:MAX_CLIP_NAME]
    # A blank name keeps the one it has. Nothing on the site copes with a clip
    # called nothing at all, and the default "Clip" is already the floor.
    if name:
        db.rename_clip(clip_id, name)
    return {"ok": True, "name": name or clip["name"]}


@router.post("/api/clips/{clip_id}/share")
async def set_clip_share(clip_id: int, request: Request):
    """Publish or unpublish one clip. Admin only, and one clip at a time: there
    is deliberately no way to make the whole library public at once."""
    if not admin_user(request):
        return JSONResponse({"error": "Admins only."}, status_code=403)
    try:
        body = await request.json()
        share = bool(body["share"])
    except (ValueError, TypeError, KeyError):
        return JSONResponse(
            {"error": "Say whether to share it."}, status_code=400
        )
    clip = db.get_clip(clip_id)
    if not clip:
        return JSONResponse({"error": "No such clip."}, status_code=404)

    if not share:
        token = db.unpublish_clip(clip_id)
        unlink_shared(token)
        return {"ok": True, "shared": False, "url": None}

    if clip.get("share_token"):
        # Already public. Return the existing link rather than minting a second
        # one, so a link already sent to somebody keeps working.
        return {"ok": True, "shared": True, "url": f"/clip/{clip['share_token']}"}

    # 16 random bytes. The token is the entire credential for the clip, so it
    # has to be long enough that it cannot be found by trying.
    token = secrets.token_urlsafe(16)
    if not db.publish_clip(clip_id, token):
        return JSONResponse({"error": "Could not share that clip."}, status_code=409)
    if not link_shared(clip_id, clip.get("filename"), token):
        # The file could not be linked, so undo the row rather than advertise a
        # link that will 404.
        db.unpublish_clip(clip_id)
        return JSONResponse(
            {"error": "That clip's file is missing."}, status_code=409
        )
    logger.info("clip %s published as %s", clip_id, token)
    return {"ok": True, "shared": True, "url": f"/clip/{token}"}


@router.get("/api/shared/{token}")
def shared_clip(token: str):
    """What the public clip page shows. No session needed, so this is the one
    endpoint that answers a stranger about content.

    It returns the title, the length and the file name, and nothing else. In
    particular NOT the creator, which is an account username: the clip row
    carries it and this is the one place it must not travel. There is no chat
    replay here either, since a replay carries every chatter's display name and
    avatar, and none of them agreed to be published."""
    clip = db.get_clip_by_token(token)
    if not clip:
        return JSONResponse({"error": "No such clip."}, status_code=404)
    return {
        "name": clip["name"],
        "duration": clip.get("duration") or 0,
        "created_at": clip["created_at"],
        "video": f"/shared/{token}.mp4",
        "poster": f"/shared/{token}.jpg",
    }


# ---- Likes and comments ---------------------------------------------------
# Accounts only. Guests may watch and chat, and both of these outlive a guest's
# half hour, so they are refused the same way clipping is. Deliberately separate
# from the chat replay, which is untouched: the replay is what was said live,
# this is what people say afterwards.

_KINDS = {"vods": "vod", "clips": "clip"}


def _media_exists(kind, ref_id):
    return bool(db.get_vod(ref_id) if kind == "vod" else db.get_clip(ref_id))


@router.get("/api/{plural}/{ref_id}/reactions")
def get_reactions(plural: str, ref_id: int, request: Request):
    """The like count, whether this viewer liked it, and the comment thread."""
    kind = _KINDS.get(plural)
    if not kind:
        return Response(status_code=404)
    user = member_user(request)
    if not user:
        return Response(status_code=401)
    if not _media_exists(kind, ref_id):
        return JSONResponse({"error": "No such item."}, status_code=404)
    total, mine = db.like_state(kind, ref_id, user["username"])
    return {
        "likes": total,
        "liked": mine,
        "comments": db.list_comments(kind, ref_id),
        # So the page knows whether to offer a delete on someone else's comment.
        "can_moderate": bool(user["is_admin"] or user["is_moderator"]),
    }


@router.post("/api/{plural}/{ref_id}/like")
async def set_like(plural: str, ref_id: int, request: Request):
    kind = _KINDS.get(plural)
    if not kind:
        return Response(status_code=404)
    if not session_user(request):
        return JSONResponse({"error": "Sign in first."}, status_code=401)
    user = member_user(request)
    if not user:
        return JSONResponse({"error": GUEST_REFUSED}, status_code=403)
    if not _media_exists(kind, ref_id):
        return JSONResponse({"error": "No such item."}, status_code=404)
    try:
        body = await request.json()
        liked = bool(body["liked"])
    except (ValueError, TypeError, KeyError):
        return JSONResponse({"error": "Say whether you like it."}, status_code=400)
    total = db.set_like(kind, ref_id, user["username"], liked, int(time.time()))
    return {"ok": True, "likes": total, "liked": liked}


@router.post("/api/{plural}/{ref_id}/comments")
async def post_comment(plural: str, ref_id: int, request: Request):
    kind = _KINDS.get(plural)
    if not kind:
        return Response(status_code=404)
    if not session_user(request):
        return JSONResponse({"error": "Sign in first."}, status_code=401)
    user = member_user(request)
    if not user:
        return JSONResponse({"error": GUEST_REFUSED}, status_code=403)
    if not _media_exists(kind, ref_id):
        return JSONResponse({"error": "No such item."}, status_code=404)
    # A comment is a chat message by another name, so it obeys the same
    # moderation: someone banned or timed out in chat cannot post one here
    # instead. Reusing the hub's own checks keeps the two from drifting.
    if hub.is_banned(user["username"]):
        return JSONResponse(
            {"error": "You are banned from chat."}, status_code=403
        )
    if hub.is_timed_out(user["username"]):
        return JSONResponse(
            {"error": "You are timed out."}, status_code=403
        )
    body = await request.json()
    text = str(body.get("text") or "").strip()[:MAX_COMMENT_LENGTH]
    if not text:
        return JSONResponse({"error": "Say something first."}, status_code=400)
    db.add_comment(kind, ref_id, user["username"], text, int(time.time()))
    return {"ok": True, "comments": db.list_comments(kind, ref_id)}


@router.delete("/api/comments/{comment_id}")
def delete_comment(comment_id: int, request: Request):
    """An author may remove their own; a moderator or admin may remove any.
    Soft delete, so the thread shows that something was removed rather than
    silently closing the gap."""
    user = member_user(request)
    if not user:
        return JSONResponse({"error": "Sign in first."}, status_code=401)
    comment = db.get_comment(comment_id)
    if not comment:
        return JSONResponse({"error": "No such comment."}, status_code=404)
    is_author = comment["username"] == user["username"]
    if not (is_author or user["is_admin"] or user["is_moderator"]):
        return JSONResponse({"error": "Not yours to delete."}, status_code=403)
    db.delete_comment(comment_id, user["username"])
    return {"ok": True}


@router.delete("/api/vods/{vod_id}")
def delete_vod(vod_id: int, request: Request):
    if not admin_user(request):
        return JSONResponse({"error": "Admins only."}, status_code=403)
    row = db.delete_media("vod", vod_id)
    if not row:
        return JSONResponse({"error": "No such VOD."}, status_code=404)
    _remove_media_files(VOD_DIR, row.get("filename"), vod_id)
    return {"ok": True}


@router.delete("/api/clips/{clip_id}")
def delete_clip(clip_id: int, request: Request):
    if not admin_user(request):
        return JSONResponse({"error": "Admins only."}, status_code=403)
    row = db.delete_media("clip", clip_id)
    if not row:
        return JSONResponse({"error": "No such clip."}, status_code=404)
    # The public copy first. It is a second name for the same bytes, so leaving
    # it behind would keep a deleted clip playing for anyone holding the link.
    unlink_shared(row.get("share_token"))
    _remove_media_files(CLIP_DIR, row.get("filename"), clip_id)
    return {"ok": True}


async def _set_keep(kind, ref_id, request, missing):
    # Pinning is an admin action on channel content, the same guard the deletes
    # above use. A pinned item is exempt from every retention limit.
    if not admin_user(request):
        return JSONResponse({"error": "Admins only."}, status_code=403)
    try:
        body = await request.json()
        keep = bool(body["keep"])
    except (ValueError, TypeError, KeyError):
        return JSONResponse({"error": "Say whether to keep it."}, status_code=400)
    if not db.set_media_keep(kind, ref_id, keep):
        return JSONResponse({"error": missing}, status_code=404)
    return {"ok": True, "keep": keep}


@router.post("/api/vods/{vod_id}/keep")
async def set_vod_keep(vod_id: int, request: Request):
    return await _set_keep("vod", vod_id, request, "No such VOD.")


@router.post("/api/clips/{clip_id}/keep")
async def set_clip_keep(clip_id: int, request: Request):
    return await _set_keep("clip", clip_id, request, "No such clip.")


# The accent flavors as hex, for the manifest's theme color. The same four
# values are in style.css (as CSS custom properties) and on the admin swatches;
# a phone's task switcher cannot read CSS, so they are repeated here.
_ACCENT_HEX = {
    "green": "#6ab48a",
    "amber": "#c2a05c",
    "blue": "#7aa3c0",
    "ghost": "#9aa39a",
}


@router.get("/api/manifest.webmanifest")
def manifest():
    # Rendered rather than served as a static file, because the installed app
    # should carry the operator's own name, and because everything under
    # /assets is cached as immutable for a year. Public, like /api/status: a
    # browser fetches the manifest before anyone has signed in.
    info = db.get_stream_info()
    site_name = info["site_name"] or "upperroom"
    return JSONResponse(
        {
            "name": site_name,
            "short_name": site_name[:12],
            "description": "A private livestream.",
            "start_url": "/home",
            "scope": "/",
            "display": "standalone",
            "background_color": "#0b0c0b",
            "theme_color": _ACCENT_HEX.get(info["accent"], _ACCENT_HEX["green"]),
            "icons": [
                {"src": "/assets/icons/icon-192.png?v=1", "sizes": "192x192",
                 "type": "image/png"},
                {"src": "/assets/icons/icon-512.png?v=1", "sizes": "512x512",
                 "type": "image/png"},
                {"src": "/assets/icons/icon-512-maskable.png?v=1", "sizes": "512x512",
                 "type": "image/png", "purpose": "maskable"},
            ],
        },
        media_type="application/manifest+json",
    )


@router.get("/api/status")
async def status():
    # Ask MediaMTX whether a publisher is connected to our stream path, so the
    # player can show an offline card instead of a broken video. When live we
    # also return when the stream started, so the landing page can show how
    # long it has been running.
    data = await fetch_path()
    watching = len(hub.viewers())
    # accent and site_name are public here so pre-login pages (the login screen)
    # can paint the channel's brand color and show the operator's site name before
    # any session exists.
    info = db.get_stream_info()
    accent = info["accent"]
    site_name = info["site_name"]
    if data and data.get("ready", False):
        return {
            "online": True,
            "since": ready_epoch(data.get("readyTime")),
            "watching": watching,
            "accent": accent,
            "site_name": site_name,
            # What is being played, for the home card. Only while live: the label
            # itself persists between broadcasts, but "Playing" on an offline
            # card would be describing a stream that is not running. It is public
            # here because it is already public in the watch page's link preview,
            # which is the whole reason it exists.
            "game": db.get_now_playing(),
            # The running release, public here so the dashboard footer and any
            # external check can read it without a number baked into the markup.
            "version": VERSION,
        }
    body = {
        "online": False,
        "watching": watching,
        "accent": accent,
        "site_name": site_name,
        "version": VERSION,
    }
    # The time of the next announced broadcast is public, so the login page can
    # count down to it. The note that goes with it is not: it says what the
    # gathering is, and that stays behind the sign in. Only sent while the
    # schedule is still worth showing.
    schedule = db.get_schedule()
    when = schedule["next_stream_at"]
    if when and time.time() < when + SCHEDULE_GRACE:
        body["next_stream_at"] = when
    return body
