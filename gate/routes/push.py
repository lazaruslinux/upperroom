"""
The go-live push, from the viewer's side.

A signed-in browser fetches the server's public key, subscribes with its own
push service, and hands the subscription here. Only subscriptions on the known
push services are kept (webpush.endpoint_allowed), since the server posts to
whatever URL is stored.
"""

import time

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

import db
import webpush
from auth import client_ip, session_user, too_many_push_changes

router = APIRouter()

NOT_READY = "Notifications are not set up on this server."


async def _body(request):
    try:
        body = await request.json()
    except ValueError:
        return None
    return body if isinstance(body, dict) else None


@router.get("/api/push/key")
def push_key(request: Request):
    if not session_user(request):
        return JSONResponse({"error": "Sign in first."}, status_code=401)
    if not webpush.ready():
        return {"ready": False, "key": ""}
    return {"ready": True, "key": webpush.public_key()}


@router.post("/api/push/subscribe")
async def push_subscribe(request: Request):
    user = session_user(request)
    if not user:
        return JSONResponse({"error": "Sign in first."}, status_code=401)
    if too_many_push_changes(client_ip(request)):
        return JSONResponse(
            {"error": "Too many tries. Wait a minute and try again."}, status_code=429
        )
    if not webpush.ready():
        return JSONResponse({"error": NOT_READY}, status_code=503)
    body = await _body(request)
    keys = body.get("keys") if body else None
    if not isinstance(keys, dict):
        return JSONResponse({"error": "That subscription is incomplete."}, status_code=400)
    endpoint, p256dh, auth = body.get("endpoint"), keys.get("p256dh"), keys.get("auth")
    problem = webpush.check_subscription(endpoint, p256dh, auth)
    if problem:
        return JSONResponse({"error": problem}, status_code=400)
    db.add_push_subscription(user["username"], endpoint, p256dh, auth, int(time.time()))
    return {"ok": True}


@router.post("/api/push/unsubscribe")
async def push_unsubscribe(request: Request):
    user = session_user(request)
    if not user:
        return JSONResponse({"error": "Sign in first."}, status_code=401)
    if too_many_push_changes(client_ip(request)):
        return JSONResponse(
            {"error": "Too many tries. Wait a minute and try again."}, status_code=429
        )
    body = await _body(request)
    endpoint = body.get("endpoint") if body else None
    if not isinstance(endpoint, str) or not endpoint:
        return JSONResponse({"error": "Which subscription?"}, status_code=400)
    # Only the caller's own rows. Someone else's stays until its push service
    # reports it gone.
    removed = db.remove_push_subscription(user["username"], endpoint)
    return {"ok": True, "removed": removed}
