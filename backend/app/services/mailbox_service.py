from datetime import datetime
import imaplib
import smtplib
import uuid
import base64
import time
from email.mime.text import MIMEText
import requests as _req

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
        data.pop("ms_client_secret", None)
    data["has_password"] = bool(mailbox.get("password"))
    data["has_smtp_password"] = bool(mailbox.get("smtp_password"))
    data["has_ms_client_secret"] = bool(mailbox.get("ms_client_secret"))
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


def get_default_outbound_mailbox():
    mailbox = mailboxes_collection.find_one({"enabled": True}, sort=[("email", 1)])
    if mailbox:
        return serialize_mailbox(mailbox, include_secret=True)

    return env_mailbox()


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

    return get_default_outbound_mailbox()


def validate_mailbox_payload(data, existing=None):
    existing = existing or {}
    auth_type = str(data.get("auth_type") or existing.get("auth_type") or "basic").strip()
    email = clean_email(data.get("email", existing.get("email")))
    name = str(data.get("name", existing.get("name") or email)).strip()

    if auth_type == "oauth2":
        ms_client_id = str(data.get("ms_client_id") or existing.get("ms_client_id") or "").strip()
        ms_client_secret = data.get("ms_client_secret") or existing.get("ms_client_secret")
        ms_tenant_id = str(data.get("ms_tenant_id") or existing.get("ms_tenant_id") or "").strip()
        imap_server = str(data.get("imap_server") or existing.get("imap_server") or "outlook.office365.com").strip()
        smtp_host = str(data.get("smtp_host") or existing.get("smtp_host") or "smtp.office365.com").strip()
        smtp_port = int(data.get("smtp_port") or existing.get("smtp_port") or 587)
        smtp_user = str(data.get("smtp_user") or existing.get("smtp_user") or email).strip()

        if not email or not ms_client_id or not ms_client_secret or not ms_tenant_id:
            raise HTTPException(
                status_code=400,
                detail="Email, client ID, client secret, and tenant ID are required for Microsoft 365 OAuth2",
            )

        return {
            "auth_type": "oauth2",
            "name": name,
            "email": email,
            "imap_server": imap_server,
            "smtp_host": smtp_host,
            "smtp_port": smtp_port,
            "smtp_user": smtp_user,
            "ms_client_id": ms_client_id,
            "ms_client_secret": ms_client_secret,
            "ms_tenant_id": ms_tenant_id,
            "enabled": bool(data.get("enabled", existing.get("enabled", True))),
            "updated_at": datetime.now(IST),
        }

    # basic auth
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
        "auth_type": "basic",
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


_token_cache = {}


def get_oauth2_token(client_id, client_secret, tenant_id):
    """Return a cached-or-fresh Microsoft OAuth2 access token (client credentials flow)."""
    key = (client_id, tenant_id)
    cached = _token_cache.get(key)
    if cached and time.time() < cached["expires_at"] - 60:
        return cached["token"]

    url = f"https://login.microsoftonline.com/{tenant_id}/oauth2/v2.0/token"
    resp = _req.post(url, data={
        "grant_type": "client_credentials",
        "client_id": client_id,
        "client_secret": client_secret,
        "scope": "https://outlook.office365.com/.default",
    }, timeout=15)
    resp.raise_for_status()
    token_data = resp.json()
    token = token_data["access_token"]
    _token_cache[key] = {
        "token": token,
        "expires_at": time.time() + token_data.get("expires_in", 3600),
    }
    return token


def connect_imap(mailbox):
    """Return an authenticated imaplib.IMAP4_SSL instance."""
    mail = imaplib.IMAP4_SSL(mailbox["imap_server"])
    if mailbox.get("auth_type") == "oauth2":
        token = get_oauth2_token(
            mailbox["ms_client_id"],
            mailbox["ms_client_secret"],
            mailbox["ms_tenant_id"],
        )
        # imaplib.authenticate base64-encodes the callback's return value before sending
        raw = f"user={mailbox['email']}\x01auth=Bearer {token}\x01\x01".encode()
        mail.authenticate("XOAUTH2", lambda challenge: raw)
    else:
        mail.login(mailbox["email"], mailbox["password"])
    return mail


def connect_smtp(mailbox):
    """Return an authenticated smtplib.SMTP(SSL) instance."""
    smtp_host = mailbox.get("smtp_host", "")
    smtp_port = int(mailbox.get("smtp_port") or 465)

    if mailbox.get("auth_type") == "oauth2":
        server = smtplib.SMTP(smtp_host, smtp_port, timeout=30)
        server.ehlo()
        server.starttls()
        server.ehlo()
        token = get_oauth2_token(
            mailbox["ms_client_id"],
            mailbox["ms_client_secret"],
            mailbox["ms_tenant_id"],
        )
        raw = f"user={mailbox['email']}\x01auth=Bearer {token}\x01\x01".encode()
        server.auth("XOAUTH2", lambda x: raw, initial_response_ok=True)
    else:
        if smtp_port == 465:
            server = smtplib.SMTP_SSL(smtp_host, smtp_port, timeout=30)
        else:
            server = smtplib.SMTP(smtp_host, smtp_port, timeout=30)
            server.ehlo()
            server.starttls()
            server.ehlo()
        server.login(
            mailbox.get("smtp_user") or mailbox.get("email", ""),
            mailbox.get("smtp_password", ""),
        )
    return server


def test_imap_connection(mailbox):
    mail = connect_imap(mailbox)
    try:
        mail.select("inbox", readonly=True)
    finally:
        try:
            mail.logout()
        except Exception:
            pass


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

    with connect_smtp(mailbox) as server:
        server.sendmail(mailbox["email"], [recipient], msg.as_string())


def test_mailbox(mailbox, recipient):
    test_imap_connection(mailbox)
    send_test_email(mailbox, recipient)
