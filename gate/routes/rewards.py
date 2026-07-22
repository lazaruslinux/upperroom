"""
Channel points and rewards routes.

Viewers earn points by watching the stream live and spend them on rewards the
admin defines. The viewer-facing balance, reward catalog, and redeem action live
here alongside the admin-only reward management (create, list, delete), which
sits behind the same admin guard as the other admin routes.
"""

import logging

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

import db
from auth import admin_user, read_session
from config import COOKIE_NAME, MAX_REWARD_COST, MAX_REWARD_LABEL
from hub import hub

logger = logging.getLogger("upperroom.rewards")

router = APIRouter()


@router.get("/api/rewards")
def list_rewards(request: Request):
    # Any signed-in viewer: their own balance and the reward catalog, so the
    # watch page can show the points chip and the redeem panel.
    session = read_session(request.cookies.get(COOKIE_NAME, ""))
    if not session:
        return JSONResponse({"error": "Sign in first."}, status_code=401)
    return {"points": db.get_points(session["sub"]), "rewards": db.list_rewards()}


@router.post("/api/redeem")
async def redeem(request: Request):
    session = read_session(request.cookies.get(COOKIE_NAME, ""))
    if not session:
        return JSONResponse({"error": "Sign in first."}, status_code=401)
    username = session["sub"]
    body = await request.json()
    try:
        reward_id = int(body.get("reward_id"))
    except (TypeError, ValueError):
        return JSONResponse({"detail": "unknown reward"}, status_code=404)
    reward = db.get_reward(reward_id)
    if not reward:
        return JSONResponse({"detail": "unknown reward"}, status_code=404)
    # The spend is atomic: the balance is deducted only if it covers the cost, so
    # two redemptions racing on a balance that covers one can never both win.
    balance = db.spend_points(username, reward["cost"])
    if balance is None:
        return JSONResponse({"detail": "not enough points"}, status_code=400)
    # Announce the redemption to every watch page and the overlay, the same hub
    # broadcast path the clip alert takes. Best effort: a broadcast failure must
    # never undo a spend that already went through.
    user = db.get_user(username)
    try:
        await hub.broadcast({
            "type": "redeem",
            "user": user["display_name"] if user else username,
            "label": reward["label"],
            "cost": reward["cost"],
        })
    except Exception:
        logger.debug("redeem broadcast failed", exc_info=True)
    return {"ok": True, "points": balance}


# ---- Admin: reward management ---------------------------------------------

@router.get("/api/admin/rewards")
def admin_rewards_list(request: Request):
    if not admin_user(request):
        return JSONResponse({"error": "Admins only."}, status_code=403)
    return {"rewards": db.list_rewards()}


@router.post("/api/admin/rewards")
async def admin_rewards_create(request: Request):
    if not admin_user(request):
        return JSONResponse({"error": "Admins only."}, status_code=403)
    body = await request.json()
    label = str(body.get("label") or "").strip()[:MAX_REWARD_LABEL]
    if not label:
        return JSONResponse(
            {"error": "Reward label cannot be empty."}, status_code=400
        )
    try:
        cost = int(body.get("cost"))
    except (TypeError, ValueError):
        return JSONResponse(
            {"error": "Cost must be a whole number."}, status_code=400
        )
    if cost < 1 or cost > MAX_REWARD_COST:
        return JSONResponse(
            {"error": f"Cost must be between 1 and {MAX_REWARD_COST}."},
            status_code=400,
        )
    reward_id = db.add_reward(label, cost)
    return {"ok": True, "id": reward_id}


@router.delete("/api/admin/rewards/{reward_id}")
def admin_rewards_delete(reward_id: int, request: Request):
    if not admin_user(request):
        return JSONResponse({"error": "Admins only."}, status_code=403)
    if not db.delete_reward(reward_id):
        return JSONResponse({"error": "No such reward."}, status_code=404)
    return {"ok": True}
