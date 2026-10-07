from datetime import datetime
import imaplib
import smtplib
import uuid
import base64
import time
import hashlib
import re
import threading
from email.mime.text import MIMEText
import requests as _req

import pytz
from bson import ObjectId
from fastapi import HTTPException

from app.config.settings import (
    EMAIL_ACCOUNT,
    EMAIL_AUTH_TYPE,
    EMAIL_PASSWORD,
    IMAP_SERVER,
    MS_CLIENT_ID,
    MS_CLIENT_SECRET,
    MS_TENANT_ID,
    SMTP_HOST,
    SMTP_PASS,
    SMTP_PORT,
    SMTP_USER,
    TIMEZONE,
)
from app.db.mongo import mailboxes_collection

IST = pytz.timezone(TIMEZONE)

AUTH_TYPES = ("basic", "oauth2")
BASIC_ONLY_FIELDS = ("password", "smtp_password")
OAUTH2_ONLY_FIELDS = ("ms_client_id", "ms_client_secret", "ms_tenant_id")

_GUID_RE = re.compile(r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$")
_TENANT_DOMAIN_RE = re.compile(r"^[A-Za-z0-9-]+(\.[A-Za-z0-9-]+)+$")


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
    if EMAIL_AUTH_TYPE == "oauth2":
        if not EMAIL_ACCOUNT or not MS_CLIENT_ID or not MS_CLIENT_SECRET or not MS_TENANT_ID:
            return None

        return {
            "_id": "env-default",
            "name": "Default mailbox",
            "auth_type": "oauth2",
            "email": EMAIL_ACCOUNT,
            "imap_server": IMAP_SERVER or OAUTH2_DEFAULT_IMAP,
            "smtp_host": SMTP_HOST or OAUTH2_DEFAULT_SMTP,
            "smtp_port": oauth2_smtp_port(SMTP_PORT),
            "smtp_user": EMAIL_ACCOUNT,
            "ms_client_id": MS_CLIENT_ID,
            "ms_client_secret": MS_CLIENT_SECRET,
            "ms_tenant_id": MS_TENANT_ID,
            "enabled": True,
            "source": "env",
        }

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
    if auth_type not in AUTH_TYPES:
        raise HTTPException(status_code=400, detail="Connection type must be basic or oauth2")

    email = clean_email(data.get("email", existing.get("email")))
    name = str(data.get("name", existing.get("name") or email)).strip()

    if auth_type == "oauth2":
        # Values left over from basic auth (e.g. port 465, a Gmail IMAP host) must
        # not leak into an M365 mailbox when the connection type is switched.
        switching = existing.get("auth_type", "basic") != "oauth2"
        carried = {} if switching else existing

        ms_client_id = str(data.get("ms_client_id") or existing.get("ms_client_id") or "").strip()
        ms_client_secret = str(data.get("ms_client_secret") or existing.get("ms_client_secret") or "").strip()
        ms_tenant_id = str(data.get("ms_tenant_id") or existing.get("ms_tenant_id") or "").strip()
        imap_server = str(data.get("imap_server") or carried.get("imap_server") or OAUTH2_DEFAULT_IMAP).strip()
        smtp_host = str(data.get("smtp_host") or carried.get("smtp_host") or OAUTH2_DEFAULT_SMTP).strip()
        smtp_port = oauth2_smtp_port(data.get("smtp_port") or carried.get("smtp_port"))
        smtp_user = email

        if not email or not ms_client_id or not ms_client_secret or not ms_tenant_id:
            raise HTTPException(
                status_code=400,
                detail="Email, client ID, client secret, and tenant ID are required for Microsoft 365 OAuth2",
            )
        if not _GUID_RE.match(ms_client_id):
            raise HTTPException(status_code=400, detail="Client ID must be the application (client) ID GUID")
        if not (_GUID_RE.match(ms_tenant_id) or _TENANT_DOMAIN_RE.match(ms_tenant_id)):
            raise HTTPException(
                status_code=400,
                detail="Tenant ID must be the directory (tenant) ID GUID or a tenant domain such as contoso.onmicrosoft.com",
            )
        if _GUID_RE.match(ms_client_secret):
            raise HTTPException(
                status_code=400,
                detail="Client secret looks like the secret ID. Paste the secret Value from Certificates & secrets instead",
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


OAUTH2_SCOPE = "https://outlook.office365.com/.default"
OAUTH2_DEFAULT_IMAP = "outlook.office365.com"
OAUTH2_DEFAULT_SMTP = "smtp.office365.com"
OAUTH2_DEFAULT_SMTP_PORT = 587
CONNECT_TIMEOUT = 30


def oauth2_smtp_port(port):
    # Exchange Online accepts client submission only via STARTTLS (587); it has no
    # implicit-TLS 465 listener, which is the basic-auth default elsewhere.
    port = int(port or OAUTH2_DEFAULT_SMTP_PORT)
    return OAUTH2_DEFAULT_SMTP_PORT if port == 465 else port

IMAP_AUTH_HINT = (
    "Check that the app has the Office 365 Exchange Online application permission "
    "IMAP.AccessAsApp with admin consent, that its service principal is registered in "
    "Exchange Online (New-ServicePrincipal), and that it has FullAccess on this mailbox "
    "(Add-MailboxPermission)."
)
SMTP_AUTH_HINT = (
    "Check that the app has the Office 365 Exchange Online application permission "
    "SMTP.SendAsApp with admin consent, that SMTP AUTH is enabled for this mailbox "
    "(Set-CASMailbox -SmtpClientAuthenticationDisabled $false), and that the service "
    "principal has FullAccess or SendAs on the mailbox."
)

_token_cache = {}
_token_lock = threading.Lock()


def _token_cache_key(client_id, client_secret, tenant_id):
    # Include a digest of the secret so a rotated secret never reuses a token
    # issued for the old one.
    secret_digest = hashlib.sha256(str(client_secret).encode("utf-8")).hexdigest()
    return (tenant_id, client_id, secret_digest)


def _token_error_message(resp):
    try:
        body = resp.json()
    except ValueError:
        body = {}
    description = body.get("error_description") or body.get("error") or resp.text
    # AAD descriptions carry trace/correlation ids on extra lines; the first line is the useful part.
    description = str(description).strip().splitlines()[0] if description else ""
    return f"Microsoft 365 token request failed (HTTP {resp.status_code}): {description}"


def get_oauth2_token(client_id, client_secret, tenant_id, force_refresh=False):
    """Return a cached-or-fresh Microsoft OAuth2 access token (client credentials flow)."""
    key = _token_cache_key(client_id, client_secret, tenant_id)

    with _token_lock:
        cached = _token_cache.get(key)
        if cached and not force_refresh and time.time() < cached["expires_at"] - 60:
            return cached["token"]

        url = f"https://login.microsoftonline.com/{tenant_id}/oauth2/v2.0/token"
        try:
            resp = _req.post(url, data={
                "grant_type": "client_credentials",
                "client_id": client_id,
                "client_secret": client_secret,
                "scope": OAUTH2_SCOPE,
            }, timeout=15)
        except _req.RequestException as exc:
            raise RuntimeError(f"Microsoft 365 token request failed: {exc}") from exc

        if resp.status_code != 200:
            raise RuntimeError(_token_error_message(resp))

        token_data = resp.json()
        token = token_data.get("access_token")
        if not token:
            raise RuntimeError("Microsoft 365 token response did not include an access token")

        _token_cache[key] = {
            "token": token,
            "expires_at": time.time() + int(token_data.get("expires_in", 3600)),
        }
        return token


def invalidate_oauth2_token(mailbox):
    key = _token_cache_key(mailbox.get("ms_client_id"), mailbox.get("ms_client_secret"), mailbox.get("ms_tenant_id"))
    with _token_lock:
        _token_cache.pop(key, None)


def _mailbox_token(mailbox, force_refresh=False):
    return get_oauth2_token(
        mailbox["ms_client_id"],
        mailbox["ms_client_secret"],
        mailbox["ms_tenant_id"],
        force_refresh=force_refresh,
    )


def xoauth2_string(user, token):
    return f"user={user}\x01auth=Bearer {token}\x01\x01"


def _decode_xoauth2_challenge(challenge):
    """On a failed XOAUTH2 exchange the server sends a (base64-decoded by the
    caller) JSON status before rejecting; return it as text for error messages."""
    if not challenge:
        return ""
    if isinstance(challenge, bytes):
        challenge = challenge.decode("utf-8", errors="replace")
    return challenge.strip()


class _XOAuth2Responder:
    """Auth callback for imaplib/smtplib XOAUTH2. Sends the token on the first
    call and an empty response to any error challenge, as RFC 7628 requires, so
    the server finishes with a clean NO/535 instead of looping."""

    def __init__(self, user, token, as_bytes):
        self.initial = xoauth2_string(user, token)
        self.as_bytes = as_bytes
        self.sent = False
        self.server_error = ""

    def __call__(self, challenge=None):
        if not self.sent:
            self.sent = True
            return self.initial.encode() if self.as_bytes else self.initial
        self.server_error = _decode_xoauth2_challenge(challenge)
        return b"" if self.as_bytes else ""


def _imap_xoauth2(mail, mailbox, token):
    responder = _XOAuth2Responder(mailbox["email"], token, as_bytes=True)
    try:
        mail.authenticate("XOAUTH2", responder)
    except imaplib.IMAP4.error as exc:
        detail = responder.server_error or str(exc)
        raise imaplib.IMAP4.error(f"IMAP XOAUTH2 authentication failed for {mailbox['email']}: {detail}") from exc


def _smtp_xoauth2(server, mailbox, token):
    responder = _XOAuth2Responder(mailbox["email"], token, as_bytes=False)
    try:
        server.auth("XOAUTH2", responder, initial_response_ok=True)
    except smtplib.SMTPAuthenticationError as exc:
        detail = exc.smtp_error.decode("utf-8", errors="replace") if isinstance(exc.smtp_error, bytes) else str(exc.smtp_error)
        if responder.server_error:
            detail = f"{detail} ({responder.server_error})"
        raise smtplib.SMTPAuthenticationError(
            exc.smtp_code,
            f"SMTP XOAUTH2 authentication failed for {mailbox['email']}: {detail}".encode(),
        ) from exc


def connect_imap(mailbox):
    """Return an authenticated imaplib.IMAP4_SSL instance."""
    mail = imaplib.IMAP4_SSL(mailbox["imap_server"], timeout=CONNECT_TIMEOUT)
    try:
        if mailbox.get("auth_type") == "oauth2":
            try:
                _imap_xoauth2(mail, mailbox, _mailbox_token(mailbox))
            except imaplib.IMAP4.error:
                # A cached token can be rejected before its expiry (consent revoked,
                # permissions changed). Retry once on a fresh connection and token.
                _safe_logout(mail)
                invalidate_oauth2_token(mailbox)
                mail = imaplib.IMAP4_SSL(mailbox["imap_server"], timeout=CONNECT_TIMEOUT)
                try:
                    _imap_xoauth2(mail, mailbox, _mailbox_token(mailbox, force_refresh=True))
                except imaplib.IMAP4.error as exc:
                    raise imaplib.IMAP4.error(f"{exc}. {IMAP_AUTH_HINT}") from exc
        else:
            mail.login(mailbox["email"], mailbox["password"])
    except Exception:
        _safe_logout(mail)
        raise
    return mail


def _open_smtp(smtp_host, smtp_port):
    if smtp_port == 465:
        return smtplib.SMTP_SSL(smtp_host, smtp_port, timeout=CONNECT_TIMEOUT)

    server = smtplib.SMTP(smtp_host, smtp_port, timeout=CONNECT_TIMEOUT)
    server.ehlo()
    server.starttls()
    server.ehlo()
    return server


def connect_smtp(mailbox):
    """Return an authenticated smtplib.SMTP(SSL) instance."""
    smtp_host = mailbox.get("smtp_host", "")

    if mailbox.get("auth_type") == "oauth2":
        smtp_host = smtp_host or OAUTH2_DEFAULT_SMTP
        smtp_port = oauth2_smtp_port(mailbox.get("smtp_port"))
        server = _open_smtp(smtp_host, smtp_port)
        try:
            try:
                _smtp_xoauth2(server, mailbox, _mailbox_token(mailbox))
            except smtplib.SMTPAuthenticationError:
                _safe_quit(server)
                invalidate_oauth2_token(mailbox)
                server = _open_smtp(smtp_host, smtp_port)
                try:
                    _smtp_xoauth2(server, mailbox, _mailbox_token(mailbox, force_refresh=True))
                except smtplib.SMTPAuthenticationError as exc:
                    message = exc.smtp_error.decode("utf-8", errors="replace")
                    raise smtplib.SMTPAuthenticationError(exc.smtp_code, f"{message}. {SMTP_AUTH_HINT}".encode()) from exc
        except Exception:
            _safe_quit(server)
            raise
        return server

    smtp_port = int(mailbox.get("smtp_port") or 465)
    server = _open_smtp(smtp_host, smtp_port)
    try:
        server.login(
            mailbox.get("smtp_user") or mailbox.get("email", ""),
            mailbox.get("smtp_password", ""),
        )
    except Exception:
        _safe_quit(server)
        raise
    return server


def _safe_logout(mail):
    try:
        mail.logout()
    except Exception:
        pass


def _safe_quit(server):
    try:
        server.quit()
    except Exception:
        try:
            server.close()
        except Exception:
            pass


def mailbox_unset_fields(auth_type):
    """Credential fields that belong to the other connection type and should be
    removed from the stored mailbox when saving as `auth_type`."""
    fields = BASIC_ONLY_FIELDS if auth_type == "oauth2" else OAUTH2_ONLY_FIELDS
    return {field: "" for field in fields}


def test_imap_connection(mailbox):
    mail = connect_imap(mailbox)
    try:
        status, _ = mail.select("inbox", readonly=True)
        if status != "OK":
            raise RuntimeError(f"Unable to open INBOX for {mailbox['email']}")
    finally:
        _safe_logout(mail)


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
    if not clean_email(recipient):
        raise HTTPException(status_code=400, detail="Test recipient is required")

    try:
        test_imap_connection(mailbox)
    except Exception as exc:
        raise RuntimeError(f"IMAP check failed: {exc}") from exc

    try:
        send_test_email(mailbox, recipient)
    except HTTPException:
        raise
    except Exception as exc:
        raise RuntimeError(f"IMAP succeeded, but SMTP send failed: {exc}") from exc
