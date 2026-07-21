"""
Moderator dashboard routes.

A moderator can review watch and chat history and lift bans they set, but cannot
add or edit accounts and never sees admin accounts. Admins pass these same
checks (admin is a superset of mod), so the admin dashboard can reuse the ban
endpoints; the admin's own listing uses the fuller /api/admin/* routes.
"""

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

import db
from auth import _clean_username, mod_actor
from hub import hub

router = APIRouter()


@router.get("/api/mod/users")
def mod_users(request: Request):
    if not mod_actor(request):
        return JSONResponse({"error": "Moderators only."}, status_code=403)
    return {"users": db.admin_list_users(include_admins=False)}


@router.get("/api/mod/users/{username}/activity")
def mod_activity(username: str, request: Request):
    if not mod_actor(request):
        return JSONResponse({"error": "Moderators only."}, status_code=403)
    username = _clean_username(username)
    target = db.get_user(username) if username else None
    if not target:
        return JSONResponse({"error": "No such user."}, status_code=404)
    # Admin accounts are invisible in the moderator area.
    if target["is_admin"]:
        return JSONResponse({"error": "No such user."}, status_code=404)
    return db.user_activity(username)


@router.get("/api/mod/chat")
def mod_chat(request: Request):
    if not mod_actor(request):
        return JSONResponse({"error": "Moderators only."}, status_code=403)
    return {"messages": db.recent_chat(exclude_admins=True)}


@router.get("/api/mod/bans")
def mod_bans(request: Request):
    actor = mod_actor(request)
    if not actor:
        return JSONResponse({"error": "Moderators only."}, status_code=403)
    # Tell the page whether the viewer may lift each ban: an admin may lift any,
    # a moderator only the ones they set.
    bans = db.list_bans()
    is_admin = bool(actor["is_admin"])
    for ban in bans:
        ban["can_lift"] = is_admin or ban["banned_by"] == actor["username"]
    return {"bans": bans, "is_admin": is_admin}


@router.post("/api/mod/unban")
async def mod_unban(request: Request):
    actor = mod_actor(request)
    if not actor:
        return JSONResponse({"error": "Moderators only."}, status_code=403)
    body = await request.json()
    username = _clean_username(body.get("username"))
    existing = db.get_ban(username) if username else None
    if not existing:
        return JSONResponse({"error": "That user is not banned."}, status_code=404)
    if not actor["is_admin"] and existing["banned_by"] != actor["username"]:
        return JSONResponse(
            {"error": "Only the moderator who set this ban, or an admin, can lift it."},
            status_code=403,
        )
    db.remove_ban(username)
    hub.remove_ban_local(username)
    return {"ok": True}
