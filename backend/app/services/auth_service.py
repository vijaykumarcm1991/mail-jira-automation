import base64
import hashlib
import hmac
import json
import secrets
from datetime import datetime, timedelta

import pytz
from fastapi import HTTPException, Request

from app.config.settings import (
    AUTH_COOKIE_NAME,
    DEFAULT_ADMIN_PASSWORD,
    DEFAULT_ADMIN_USERNAME,
    JWT_EXPIRE_MINUTES,
    JWT_SECRET_KEY,
    TIMEZONE,
)
from app.db.mongo import users_collection

IST = pytz.timezone(TIMEZONE)


def _b64encode(data):
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _b64decode(data):
    padding = "=" * (-len(data) % 4)
    return base64.urlsafe_b64decode(data + padding)


def hash_password(password, salt=None):
    salt = salt or secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt.encode(), 120000)
    return f"{salt}${digest.hex()}"


def verify_password(password, stored_hash):
    try:
        salt, _ = stored_hash.split("$", 1)
    except ValueError:
        return False
    return hmac.compare_digest(hash_password(password, salt), stored_hash)


def create_token(user):
    now = datetime.utcnow()
    header = {"alg": "HS256", "typ": "JWT"}
    payload = {
        "sub": user["username"],
        "role": user["role"],
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(minutes=JWT_EXPIRE_MINUTES)).timestamp()),
    }

    signing_input = ".".join([
        _b64encode(json.dumps(header, separators=(",", ":")).encode()),
        _b64encode(json.dumps(payload, separators=(",", ":")).encode()),
    ])
    signature = hmac.new(
        JWT_SECRET_KEY.encode(),
        signing_input.encode(),
        hashlib.sha256,
    ).digest()
    return f"{signing_input}.{_b64encode(signature)}"


def decode_token(token):
    try:
        header_b64, payload_b64, signature_b64 = token.split(".")
        signing_input = f"{header_b64}.{payload_b64}"
        expected = hmac.new(
            JWT_SECRET_KEY.encode(),
            signing_input.encode(),
            hashlib.sha256,
        ).digest()
        if not hmac.compare_digest(_b64encode(expected), signature_b64):
            return None

        payload = json.loads(_b64decode(payload_b64))
        if payload.get("exp", 0) < int(datetime.utcnow().timestamp()):
            return None
        return payload
    except Exception:
        return None


def public_user():
    return {"username": None, "role": "public", "authenticated": False}


def sanitize_user(user):
    if not user:
        return None
    return {
        "username": user.get("username"),
        "role": user.get("role", "public"),
        "active": user.get("active", True),
        "created_at": user.get("created_at"),
        "updated_at": user.get("updated_at"),
    }


def get_user_from_request(request: Request):
    token = request.cookies.get(AUTH_COOKIE_NAME)
    payload = decode_token(token) if token else None
    if not payload:
        return public_user()

    user = users_collection.find_one(
        {"username": payload.get("sub"), "active": True},
        {"_id": 0},
    )
    if not user:
        return public_user()

    return {
        "username": user["username"],
        "role": user.get("role", "public"),
        "authenticated": True,
    }


def require_admin(request: Request):
    user = get_user_from_request(request)
    if user.get("role") != "admin":
        raise HTTPException(status_code=403, detail="Admin access required")
    return user


def ensure_default_admin():
    if users_collection.count_documents({"role": "admin", "active": True}) > 0:
        return

    now = datetime.now(IST)
    users_collection.update_one(
        {"username": DEFAULT_ADMIN_USERNAME},
        {
            "$set": {
                "password_hash": hash_password(DEFAULT_ADMIN_PASSWORD),
                "role": "admin",
                "active": True,
                "updated_at": now,
            },
            "$setOnInsert": {
                "created_at": now,
            },
        },
        upsert=True,
    )
