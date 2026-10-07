"""Microsoft 365 OAuth2 (client credentials + XOAUTH2) mailbox support.

Covers the token cache, AAD error surfacing, the XOAUTH2 exchange against the
real smtplib/imaplib auth loops (servers are faked at the socket-command level),
the fresh-token retry, payload validation and the env fallback mailbox.
"""

import base64
import imaplib
import smtplib
from unittest.mock import MagicMock, patch

import pytest
from fastapi import HTTPException

import app.services.mailbox_service as mbs

CLIENT_ID = "11111111-2222-3333-4444-555555555555"
TENANT_ID = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
SECRET = "abc~DEF.secret-value_123"

MAILBOX = {
    "auth_type": "oauth2",
    "email": "support@contoso.com",
    "imap_server": "outlook.office365.com",
    "smtp_host": "smtp.office365.com",
    "smtp_port": 587,
    "ms_client_id": CLIENT_ID,
    "ms_client_secret": SECRET,
    "ms_tenant_id": TENANT_ID,
}


@pytest.fixture(autouse=True)
def _clear_token_cache():
    mbs._token_cache.clear()
    yield
    mbs._token_cache.clear()


def _token_resp(token="tok-1", status=200, payload=None):
    r = MagicMock()
    r.status_code = status
    r.json.return_value = payload if payload is not None else {"access_token": token, "expires_in": 3599}
    r.text = str(r.json.return_value)
    return r


# ── token acquisition ─────────────────────────────────────────────────────────

def test_token_is_cached_per_credentials():
    with patch.object(mbs._req, "post", return_value=_token_resp("tok-1")) as post:
        assert mbs.get_oauth2_token(CLIENT_ID, SECRET, TENANT_ID) == "tok-1"
        assert mbs.get_oauth2_token(CLIENT_ID, SECRET, TENANT_ID) == "tok-1"
        assert post.call_count == 1

        sent = post.call_args.kwargs["data"]
        assert sent["grant_type"] == "client_credentials"
        assert sent["scope"] == "https://outlook.office365.com/.default"
        assert TENANT_ID in post.call_args.args[0]


def test_rotated_secret_does_not_reuse_cached_token():
    with patch.object(mbs._req, "post", side_effect=[_token_resp("old"), _token_resp("new")]) as post:
        assert mbs.get_oauth2_token(CLIENT_ID, SECRET, TENANT_ID) == "old"
        assert mbs.get_oauth2_token(CLIENT_ID, "rotated-secret", TENANT_ID) == "new"
        assert post.call_count == 2


def test_token_error_surfaces_aad_description():
    err = _token_resp(status=401, payload={
        "error": "invalid_client",
        "error_description": "AADSTS7000215: Invalid client secret provided.\r\nTrace ID: x\r\nCorrelation ID: y",
    })
    with patch.object(mbs._req, "post", return_value=err):
        with pytest.raises(RuntimeError) as exc:
            mbs.get_oauth2_token(CLIENT_ID, SECRET, TENANT_ID)

    assert "AADSTS7000215: Invalid client secret provided." in str(exc.value)
    assert "Trace ID" not in str(exc.value)


# ── SMTP XOAUTH2 through the real smtplib.SMTP.auth loop ──────────────────────

class FakeSMTP(smtplib.SMTP):
    """smtplib.SMTP with the network replaced: `replies` is a queue of
    (code, bytes) answers to docmd; every command sent is recorded."""

    instances = []

    def __init__(self, host="", port=0, timeout=None, replies=None):
        self.sent = []
        self.replies = list(replies or [])
        self.closed = False
        self.init_args = (host, port)
        FakeSMTP.instances.append(self)

    def ehlo(self, name=""):
        return (250, b"ok")

    def starttls(self, *args, **kwargs):
        return (220, b"ready")

    def docmd(self, cmd, args=""):
        self.sent.append((cmd, args))
        return self.replies.pop(0)

    def quit(self):
        self.closed = True

    def close(self):
        self.closed = True


def _decode_auth_arg(arg):
    mech, b64 = arg.split(" ", 1)
    return mech, base64.b64decode(b64).decode()


def test_connect_smtp_oauth2_sends_xoauth2_initial_response():
    FakeSMTP.instances = []
    with patch.object(mbs._req, "post", return_value=_token_resp("tok-1")), \
         patch.object(mbs.smtplib, "SMTP", lambda h, p, timeout=None: FakeSMTP(h, p, replies=[(235, b"2.7.0 Authentication successful")])):
        server = mbs.connect_smtp(MAILBOX)

    assert server.init_args == ("smtp.office365.com", 587)
    cmd, arg = server.sent[0]
    assert cmd == "AUTH"
    mech, decoded = _decode_auth_arg(arg)
    assert mech == "XOAUTH2"
    assert decoded == "user=support@contoso.com\x01auth=Bearer tok-1\x01\x01"


def test_connect_smtp_oauth2_retries_once_with_fresh_token_then_hints():
    FakeSMTP.instances = []
    error_challenge = base64.b64encode(b'{"status":"401","schemes":"bearer"}')
    replies = lambda: [(334, error_challenge), (535, b"5.7.3 Authentication unsuccessful")]

    with patch.object(mbs._req, "post", side_effect=[_token_resp("stale"), _token_resp("fresh")]) as post, \
         patch.object(mbs.smtplib, "SMTP", lambda h, p, timeout=None: FakeSMTP(h, p, replies=replies())):
        with pytest.raises(smtplib.SMTPAuthenticationError) as exc:
            mbs.connect_smtp(MAILBOX)

    assert post.call_count == 2  # second attempt bypassed the cache
    first, second = FakeSMTP.instances
    assert _decode_auth_arg(first.sent[0][1])[1].endswith("Bearer stale\x01\x01")
    assert _decode_auth_arg(second.sent[0][1])[1].endswith("Bearer fresh\x01\x01")
    # the error challenge is answered with an empty response, not the token again
    assert first.sent[1][0] in ("", b"")
    assert first.closed and second.closed

    message = exc.value.smtp_error.decode()
    assert "5.7.3 Authentication unsuccessful" in message
    assert '"status":"401"' in message
    assert "SMTP.SendAsApp" in message


def test_oauth2_smtp_port_465_is_normalised_to_587():
    FakeSMTP.instances = []
    with patch.object(mbs._req, "post", return_value=_token_resp()), \
         patch.object(mbs.smtplib, "SMTP", lambda h, p, timeout=None: FakeSMTP(h, p, replies=[(235, b"ok")])), \
         patch.object(mbs.smtplib, "SMTP_SSL") as smtp_ssl:
        server = mbs.connect_smtp({**MAILBOX, "smtp_port": 465})

    smtp_ssl.assert_not_called()
    assert server.init_args[1] == 587


# ── IMAP XOAUTH2 ──────────────────────────────────────────────────────────────

class FakeIMAP:
    instances = []

    def __init__(self, host, timeout=None, fail_with=None):
        self.host = host
        self.timeout = timeout
        self.fail_with = fail_with
        self.responses = []
        self.logged_out = False
        FakeIMAP.instances.append(self)

    def authenticate(self, mechanism, authobject):
        assert mechanism == "XOAUTH2"
        self.responses.append(authobject(b""))
        if self.fail_with is not None:
            self.responses.append(authobject(self.fail_with))
            raise imaplib.IMAP4.error("AUTHENTICATE failed.")
        return ("OK", [b"AUTHENTICATE completed."])

    def logout(self):
        self.logged_out = True


def test_connect_imap_oauth2_authenticates_with_token():
    FakeIMAP.instances = []
    with patch.object(mbs._req, "post", return_value=_token_resp("tok-1")), \
         patch.object(mbs.imaplib, "IMAP4_SSL", lambda h, timeout=None: FakeIMAP(h, timeout)):
        mail = mbs.connect_imap(MAILBOX)

    assert mail.host == "outlook.office365.com"
    assert mail.timeout == mbs.CONNECT_TIMEOUT
    assert mail.responses == [b"user=support@contoso.com\x01auth=Bearer tok-1\x01\x01"]


def test_connect_imap_oauth2_failure_retries_and_reports_hint():
    FakeIMAP.instances = []
    with patch.object(mbs._req, "post", side_effect=[_token_resp("stale"), _token_resp("fresh")]), \
         patch.object(mbs.imaplib, "IMAP4_SSL", lambda h, timeout=None: FakeIMAP(h, timeout, fail_with=b'{"status":"401"}')):
        with pytest.raises(imaplib.IMAP4.error) as exc:
            mbs.connect_imap(MAILBOX)

    first, second = FakeIMAP.instances
    assert first.responses[0].endswith(b"Bearer stale\x01\x01")
    assert first.responses[1] == b""
    assert second.responses[0].endswith(b"Bearer fresh\x01\x01")
    assert first.logged_out and second.logged_out
    assert '{"status":"401"}' in str(exc.value)
    assert "IMAP.AccessAsApp" in str(exc.value)


# ── payload validation ────────────────────────────────────────────────────────

def _oauth_payload(**overrides):
    return {
        "auth_type": "oauth2",
        "email": "Support@Contoso.com",
        "ms_client_id": CLIENT_ID,
        "ms_client_secret": SECRET,
        "ms_tenant_id": TENANT_ID,
        **overrides,
    }


def test_validate_oauth2_applies_m365_defaults():
    result = mbs.validate_mailbox_payload(_oauth_payload())

    assert result["email"] == "support@contoso.com"
    assert result["imap_server"] == "outlook.office365.com"
    assert result["smtp_host"] == "smtp.office365.com"
    assert result["smtp_port"] == 587
    assert "password" not in result


def test_switching_basic_to_oauth2_drops_basic_server_settings():
    existing = {
        "auth_type": "basic",
        "email": "support@contoso.com",
        "imap_server": "imap.gmail.com",
        "smtp_host": "smtp.gmail.com",
        "smtp_port": 465,
        "password": "pw",
    }
    result = mbs.validate_mailbox_payload(_oauth_payload(smtp_port=465), existing)

    assert result["imap_server"] == "outlook.office365.com"
    assert result["smtp_host"] == "smtp.office365.com"
    assert result["smtp_port"] == 587
    assert mbs.mailbox_unset_fields("oauth2") == {"password": "", "smtp_password": ""}


def test_editing_oauth2_keeps_stored_secret_when_blank():
    existing = {**MAILBOX, "imap_server": "outlook.office365.com"}
    payload = _oauth_payload()
    del payload["ms_client_secret"]

    result = mbs.validate_mailbox_payload(payload, existing)

    assert result["ms_client_secret"] == SECRET


@pytest.mark.parametrize("overrides, fragment", [
    ({"ms_client_secret": "99999999-8888-7777-6666-555555555555"}, "secret ID"),
    ({"ms_client_id": "not-a-guid"}, "Client ID"),
    ({"ms_tenant_id": "contoso"}, "Tenant ID"),
    ({"ms_tenant_id": ""}, "required"),
    ({"auth_type": "kerberos"}, "Connection type"),
])
def test_validate_oauth2_rejects_bad_input(overrides, fragment):
    with pytest.raises(HTTPException) as exc:
        mbs.validate_mailbox_payload(_oauth_payload(**overrides))
    assert exc.value.status_code == 400
    assert fragment in exc.value.detail


def test_tenant_domain_is_accepted():
    result = mbs.validate_mailbox_payload(_oauth_payload(ms_tenant_id="contoso.onmicrosoft.com"))
    assert result["ms_tenant_id"] == "contoso.onmicrosoft.com"


def test_switching_to_basic_unsets_oauth2_fields():
    assert mbs.mailbox_unset_fields("basic") == {
        "ms_client_id": "", "ms_client_secret": "", "ms_tenant_id": ""
    }


def test_serialize_hides_client_secret():
    data = mbs.serialize_mailbox({"_id": "x", **MAILBOX})
    assert "ms_client_secret" not in data
    assert data["has_ms_client_secret"] is True


# ── env fallback mailbox ──────────────────────────────────────────────────────

def test_env_mailbox_oauth2(monkeypatch):
    monkeypatch.setattr(mbs, "EMAIL_AUTH_TYPE", "oauth2")
    monkeypatch.setattr(mbs, "EMAIL_ACCOUNT", "support@contoso.com")
    monkeypatch.setattr(mbs, "EMAIL_PASSWORD", None)
    monkeypatch.setattr(mbs, "IMAP_SERVER", None)
    monkeypatch.setattr(mbs, "SMTP_HOST", None)
    monkeypatch.setattr(mbs, "SMTP_PORT", 465)
    monkeypatch.setattr(mbs, "MS_CLIENT_ID", CLIENT_ID)
    monkeypatch.setattr(mbs, "MS_CLIENT_SECRET", SECRET)
    monkeypatch.setattr(mbs, "MS_TENANT_ID", TENANT_ID)

    mailbox = mbs.env_mailbox()

    assert mailbox["auth_type"] == "oauth2"
    assert mailbox["imap_server"] == "outlook.office365.com"
    assert mailbox["smtp_host"] == "smtp.office365.com"
    assert mailbox["smtp_port"] == 587
    assert "password" not in mailbox


def test_env_mailbox_oauth2_incomplete_returns_none(monkeypatch):
    monkeypatch.setattr(mbs, "EMAIL_AUTH_TYPE", "oauth2")
    monkeypatch.setattr(mbs, "EMAIL_ACCOUNT", "support@contoso.com")
    monkeypatch.setattr(mbs, "MS_CLIENT_ID", CLIENT_ID)
    monkeypatch.setattr(mbs, "MS_CLIENT_SECRET", None)
    monkeypatch.setattr(mbs, "MS_TENANT_ID", TENANT_ID)

    assert mbs.env_mailbox() is None
