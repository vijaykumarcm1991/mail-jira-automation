"""Jira creation / retry idempotency.

Verifies the two behaviours the design promises:
  - a NEW email always creates a fresh ticket and never runs the idempotency
    search (Case 2 — same subject must still create a new ticket);
  - a RETRY reuses an already-created ticket (matched by the stable Message-ID
    label) instead of creating a duplicate (the "201 created but response lost"
    case).
"""

from unittest.mock import MagicMock, patch

import app.services.jira_service as jira

DATA = {
    "internal_id": "INT-010",
    "subject": "Printer down",
    "description": "It is broken",
    "message_id": "abc123@mail.example.com",
}


def _resp(status, payload=None, text=""):
    r = MagicMock()
    r.status_code = status
    r.json.return_value = payload or {}
    r.text = text
    return r


def test_new_email_creates_ticket_and_skips_idempotency_search():
    with patch.object(jira, "requests") as req:
        req.post.return_value = _resp(201, {"key": "SUP-100"})

        key = jira.create_jira_ticket(DATA, {}, from_retry=False)

        assert key == "SUP-100"
        req.get.assert_not_called()  # no lookup on a fresh create
        req.post.assert_called_once()
        # ticket is stamped with the Message-ID label for future retries
        sent_fields = req.post.call_args.kwargs["json"]["fields"]
        assert sent_fields["labels"] == [jira._message_id_label(DATA["message_id"])]


def test_retry_reuses_existing_ticket_instead_of_duplicating():
    with patch.object(jira, "requests") as req:
        req.get.return_value = _resp(200, {"issues": [{"key": "SUP-55"}]})

        key = jira.create_jira_ticket(DATA, {}, from_retry=True)

        assert key == "SUP-55"
        req.get.assert_called_once()      # idempotency search ran
        req.post.assert_not_called()      # and prevented a duplicate create


def test_retry_creates_when_no_existing_ticket_found():
    with patch.object(jira, "requests") as req:
        req.get.return_value = _resp(200, {"issues": []})
        req.post.return_value = _resp(201, {"key": "SUP-77"})

        key = jira.create_jira_ticket(DATA, {}, from_retry=True)

        assert key == "SUP-77"
        req.post.assert_called_once()


def test_failed_create_records_job_once_and_retry_does_not_duplicate_it():
    from app.db.mongo import failed_jobs_collection

    with patch.object(jira, "requests") as req:
        # first (non-retry) failure stores exactly one failed-job record
        req.post.return_value = _resp(400, {}, text="bad request")
        assert jira.create_jira_ticket(DATA, {}, from_retry=False) is None
        assert failed_jobs_collection.count_documents({}) == 1

        # a failing retry must NOT insert a second failed-job record
        req.get.return_value = _resp(200, {"issues": []})
        assert jira.create_jira_ticket(DATA, {}, from_retry=True) is None
        assert failed_jobs_collection.count_documents({}) == 1


def test_message_id_label_is_deterministic_and_space_free():
    label_a = jira._message_id_label("abc123@mail.example.com")
    label_b = jira._message_id_label("abc123@mail.example.com")
    assert label_a == label_b
    assert " " not in label_a
    assert label_a != jira._message_id_label("other@mail.example.com")
