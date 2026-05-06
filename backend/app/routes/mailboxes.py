from datetime import datetime

import pytz
from bson import ObjectId
from fastapi import APIRouter, HTTPException, Request
from pymongo.errors import DuplicateKeyError

from app.config.settings import TIMEZONE
from app.db.mongo import mailboxes_collection
from app.services.audit_service import log_audit
from app.services.auth_service import require_admin
from app.services.mailbox_service import (
    get_mailbox_by_id,
    serialize_mailbox,
    test_mailbox,
    validate_mailbox_payload,
)

router = APIRouter()
IST = pytz.timezone(TIMEZONE)


@router.get("/api/mailboxes")
def list_mailboxes(request: Request):
    require_admin(request)
    return [
        serialize_mailbox(mailbox)
        for mailbox in mailboxes_collection.find({}).sort("email", 1)
    ]


@router.post("/api/mailboxes")
def create_mailbox(request: Request, data: dict):
    actor = require_admin(request)
    mailbox = validate_mailbox_payload(data)
    mailbox["created_at"] = datetime.now(IST)

    try:
        result = mailboxes_collection.insert_one(mailbox)
    except DuplicateKeyError:
        raise HTTPException(status_code=409, detail="Mailbox email already exists")

    log_audit(
        request,
        "create",
        "mailbox",
        mailbox["email"],
        {"enabled": mailbox["enabled"], "imap_server": mailbox["imap_server"]},
        actor,
    )
    saved = mailboxes_collection.find_one({"_id": result.inserted_id})
    return serialize_mailbox(saved)


@router.put("/api/mailboxes/{mailbox_id}")
def update_mailbox(mailbox_id: str, request: Request, data: dict):
    actor = require_admin(request)
    existing = get_raw_mailbox(mailbox_id)
    mailbox = validate_mailbox_payload(data, existing)

    try:
        mailboxes_collection.update_one({"_id": existing["_id"]}, {"$set": mailbox})
    except DuplicateKeyError:
        raise HTTPException(status_code=409, detail="Mailbox email already exists")

    log_audit(
        request,
        "update",
        "mailbox",
        mailbox["email"],
        {
            "enabled": mailbox["enabled"],
            "imap_server": mailbox["imap_server"],
            "password_changed": bool(data.get("password") or data.get("smtp_password")),
        },
        actor,
    )
    saved = mailboxes_collection.find_one({"_id": existing["_id"]})
    return serialize_mailbox(saved)


@router.delete("/api/mailboxes/{mailbox_id}")
def delete_mailbox(mailbox_id: str, request: Request):
    actor = require_admin(request)
    existing = get_raw_mailbox(mailbox_id)
    mailboxes_collection.delete_one({"_id": existing["_id"]})
    log_audit(request, "delete", "mailbox", existing.get("email"), actor=actor)
    return {"message": "Mailbox deleted"}


@router.post("/api/mailboxes/{mailbox_id}/test")
def test_mailbox_connection(mailbox_id: str, request: Request, data: dict):
    actor = require_admin(request)
    mailbox = get_mailbox_by_id(mailbox_id)
    if not mailbox:
        raise HTTPException(status_code=404, detail="Mailbox not found")

    try:
        test_mailbox(mailbox, data.get("recipient"))
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    log_audit(
        request,
        "test",
        "mailbox",
        mailbox.get("email"),
        {"recipient": data.get("recipient")},
        actor,
    )
    return {"message": "IMAP connection succeeded and test email was sent"}


def get_raw_mailbox(mailbox_id):
    try:
        mailbox = mailboxes_collection.find_one({"_id": ObjectId(mailbox_id)})
    except Exception:
        mailbox = None

    if not mailbox:
        raise HTTPException(status_code=404, detail="Mailbox not found")

    return mailbox
