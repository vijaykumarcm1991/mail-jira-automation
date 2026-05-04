from datetime import datetime

import pytz
from fastapi import APIRouter, Form, HTTPException, Request
from fastapi.responses import RedirectResponse
from pymongo.errors import DuplicateKeyError

from app.config.settings import AUTH_COOKIE_NAME, TIMEZONE
from app.db.mongo import users_collection
from app.services.audit_service import log_audit
from app.services.auth_service import (
    create_token,
    get_user_from_request,
    hash_password,
    require_admin,
    sanitize_user,
    verify_password,
)

router = APIRouter()
IST = pytz.timezone(TIMEZONE)
VALID_ROLES = {"admin", "public"}


def _serialize_user(user):
    data = sanitize_user(user)
    for key in ("created_at", "updated_at"):
        if data.get(key):
            data[key] = data[key].isoformat()
    return data


@router.post("/login")
def login(request: Request, username: str = Form(...), password: str = Form(...)):
    user = users_collection.find_one({"username": username, "active": True})

    if not user or not verify_password(password, user.get("password_hash", "")):
        log_audit(
            request,
            "login_failed",
            "auth",
            clean_username(username),
            {"reason": "invalid_credentials"},
            {"username": clean_username(username), "role": "unknown"},
        )
        return RedirectResponse("/login?error=1", status_code=302)

    response = RedirectResponse("/dashboard", status_code=302)
    response.set_cookie(
        AUTH_COOKIE_NAME,
        create_token(user),
        httponly=True,
        samesite="lax",
    )
    log_audit(
        request,
        "login",
        "auth",
        user["username"],
        {"role": user.get("role", "public")},
        {"username": user["username"], "role": user.get("role", "public")},
    )
    return response


@router.get("/logout")
def logout(request: Request):
    user = get_user_from_request(request)
    response = RedirectResponse("/dashboard", status_code=302)
    response.delete_cookie(AUTH_COOKIE_NAME)
    log_audit(request, "logout", "auth", user.get("username"), actor=user)
    return response


@router.get("/api/me")
def current_user(request: Request):
    return get_user_from_request(request)


@router.get("/api/admin-users")
def list_users(request: Request):
    require_admin(request)
    return [
        _serialize_user(user)
        for user in users_collection.find({}, {"_id": 0, "password_hash": 0}).sort("username", 1)
    ]


@router.post("/api/admin-users")
def create_user(request: Request, data: dict):
    actor = require_admin(request)

    username = clean_username(data.get("username"))
    password = data.get("password", "")
    role = data.get("role", "public")

    if not username or not password:
        raise HTTPException(status_code=400, detail="Username and password are required")
    if role not in VALID_ROLES:
        raise HTTPException(status_code=400, detail="Invalid role")

    now = datetime.now(IST)
    try:
        users_collection.insert_one({
            "username": username,
            "password_hash": hash_password(password),
            "role": role,
            "active": bool(data.get("active", True)),
            "created_at": now,
            "updated_at": now,
        })
    except DuplicateKeyError:
        raise HTTPException(status_code=409, detail="Username already exists")

    log_audit(
        request,
        "create",
        "admin_user",
        username,
        {"role": role, "active": bool(data.get("active", True))},
        actor,
    )
    return {"message": "User created"}


@router.put("/api/admin-users/{username}")
def update_user(username: str, request: Request, data: dict):
    current = require_admin(request)

    username = clean_username(username)
    existing = users_collection.find_one({"username": username})
    if not existing:
        raise HTTPException(status_code=404, detail="User not found")

    update = {
        "role": data.get("role", existing.get("role", "public")),
        "active": bool(data.get("active", existing.get("active", True))),
        "updated_at": datetime.now(IST),
    }
    if update["role"] not in VALID_ROLES:
        raise HTTPException(status_code=400, detail="Invalid role")
    if username == current.get("username") and (update["role"] != "admin" or not update["active"]):
        raise HTTPException(status_code=400, detail="You cannot remove your own admin access")
    if data.get("password"):
        update["password_hash"] = hash_password(data["password"])

    users_collection.update_one({"username": username}, {"$set": update})
    log_audit(
        request,
        "update",
        "admin_user",
        username,
        {
            "role": update["role"],
            "active": update["active"],
            "password_changed": bool(data.get("password")),
        },
        current,
    )
    return {"message": "User updated"}


@router.delete("/api/admin-users/{username}")
def delete_user(username: str, request: Request):
    current = require_admin(request)
    username = clean_username(username)

    if username == current.get("username"):
        raise HTTPException(status_code=400, detail="You cannot delete your own user")

    users_collection.delete_one({"username": username})
    log_audit(request, "delete", "admin_user", username, actor=current)
    return {"message": "User deleted"}


def clean_username(username):
    return str(username or "").strip().lower()
