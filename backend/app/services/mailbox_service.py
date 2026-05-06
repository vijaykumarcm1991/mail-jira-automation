from datetime import datetime
import imaplib
import smtplib
import uuid
from email.mime.text import MIMEText

import pytz
from bson import ObjectId
from fastapi import HTTPException

from app.config.settings import (
    EMAIL_ACCOUNT,
    EMAIL_PASSWORD,
    IMAP_SERVER,
    SMTP_HOST,
    SMTP_PASS,
    SMTP_PORT,
    SMTP_USER,
    TIMEZONE,
)
from app.db.mongo import mailboxes_collection

IST = pytz.timezone(TIMEZONE)


def clean_email(value):
    return str(value or "").strip().lower()


def serialize_mailbox(mailbox, include_secret=False):
    data = dict(mailbox)
    data["_id"] = str(data["_id"])
    if data.get("created_at"):
        data["created_at"] = data["created_at"].isoformat()
    if data.get("updated_at"):
        data["updated_at"] = data["updated_at"].isoformat()
    if not include_secret:
        data.pop("password", None)
        data.pop("smtp_password", None)
    data["has_password"] = bool(mailbox.get("password"))
    data["has_smtp_password"] = bool(mailbox.get("smtp_password"))
    return data


def env_mailbox():
    if not EMAIL_ACCOUNT or not EMAIL_PASSWORD or not IMAP_SERVER:
        return None

    return {
        "_id": "env-default",
        "name": "Default mailbox",
        "email": EMAIL_ACCOUNT,
        "password": EMAIL_PASSWORD,
        "imap_server": IMAP_SERVER,
        "smtp_host": SMTP_HOST,
        "smtp_port": SMTP_PORT,
        "smtp_user": SMTP_USER or EMAIL_ACCOUNT,
        "smtp_password": SMTP_PASS or EMAIL_PASSWORD,
        "enabled": True,
        "source": "env",
    }


def get_enabled_mailboxes():
    mailboxes = list(mailboxes_collection.find({"enabled": True}).sort("email", 1))
    if mailboxes:
        return [serialize_mailbox(mailbox, include_secret=True) for mailbox in mailboxes]
    if mailboxes_collection.count_documents({}) > 0:
        return []

    fallback = env_mailbox()
    return [fallback] if fallback else []


def get_mailbox_by_id(mailbox_id):
    if not mailbox_id:
        return None
    if mailbox_id == "env-default":
        return env_mailbox()
    try:
        mailbox = mailboxes_collection.find_one({"_id": ObjectId(mailbox_id)})
    except Exception:
        return None
    return serialize_mailbox(mailbox, include_secret=True) if mailbox else None


def get_mailbox_for_email_doc(email_doc):
    mailbox = get_mailbox_by_id(email_doc.get("mailbox_id"))
    if mailbox:
        return mailbox

    mailbox_email = clean_email(email_doc.get("mailbox_email"))
    if mailbox_email:
        mailbox = mailboxes_collection.find_one({"email": mailbox_email})
        if mailbox:
            return serialize_mailbox(mailbox, include_secret=True)

    return env_mailbox()


def validate_mailbox_payload(data, existing=None):
    existing = existing or {}
    email = clean_email(data.get("email", existing.get("email")))
    name = str(data.get("name", existing.get("name") or email)).strip()
    imap_server = str(data.get("imap_server", existing.get("imap_server", ""))).strip()
    password = data.get("password") or existing.get("password")

    smtp_host = str(data.get("smtp_host") or existing.get("smtp_host") or SMTP_HOST or "").strip()
    smtp_port = int(data.get("smtp_port") or existing.get("smtp_port") or SMTP_PORT or 465)
    smtp_user = str(data.get("smtp_user") or existing.get("smtp_user") or email).strip()
    smtp_password = data.get("smtp_password") or existing.get("smtp_password") or password

    if not email or not imap_server or not password:
        raise HTTPException(status_code=400, detail="Email, IMAP server, and password are required")
    if not smtp_host or not smtp_user or not smtp_password:
        raise HTTPException(status_code=400, detail="SMTP host, user, and password are required")

    return {
        "name": name,
        "email": email,
        "password": password,
        "imap_server": imap_server,
        "smtp_host": smtp_host,
        "smtp_port": smtp_port,
        "smtp_user": smtp_user,
        "smtp_password": smtp_password,
        "enabled": bool(data.get("enabled", existing.get("enabled", True))),
        "updated_at": datetime.now(IST),
    }


def test_imap_connection(mailbox):
    with imaplib.IMAP4_SSL(mailbox["imap_server"]) as client:
        client.login(mailbox["email"], mailbox["password"])
        client.select("inbox", readonly=True)


def send_test_email(mailbox, recipient):
    recipient = clean_email(recipient)
    if not recipient:
        raise HTTPException(status_code=400, detail="Test recipient is required")

    msg = MIMEText(
        f"Mailbox connection test succeeded for {mailbox['email']}.",
        "plain",
    )
    msg["Subject"] = "Mail to Jira mailbox test"
    msg["From"] = mailbox["email"]
    msg["To"] = recipient
    msg["Message-ID"] = f"<{uuid.uuid4()}@mail-jira.local>"

    with smtplib.SMTP_SSL(mailbox["smtp_host"], int(mailbox["smtp_port"])) as server:
        server.login(mailbox["smtp_user"], mailbox["smtp_password"])
        server.sendmail(mailbox["email"], [recipient], msg.as_string())


def test_mailbox(mailbox, recipient):
    test_imap_connection(mailbox)
    send_test_email(mailbox, recipient)
