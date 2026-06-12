"""Stable internal_id generation.

The old generator used a live document count (INT-{count+1}), so it reused old
ids after any deletion. These tests pin the new behaviour: a monotonic counter
that is seeded from the current max and never goes backwards.
"""

import app.utils.helpers as helpers
from app.db.mongo import emails_collection


def test_internal_ids_increment_monotonically():
    assert helpers.generate_internal_id() == "INT-001"
    assert helpers.generate_internal_id() == "INT-002"
    assert helpers.generate_internal_id() == "INT-003"


def test_counter_is_seeded_from_existing_max():
    emails_collection.insert_one({"internal_id": "INT-005"})
    emails_collection.insert_one({"internal_id": "INT-002"})

    # next id continues after the highest existing one, not from 1
    assert helpers.generate_internal_id() == "INT-006"


def test_ids_are_not_reused_after_deletion():
    assert helpers.generate_internal_id() == "INT-001"
    assert helpers.generate_internal_id() == "INT-002"

    # simulate the admin deleting every email/ticket
    emails_collection.clear()

    # counter must keep climbing — the old count-based generator would have
    # handed back INT-001 here and collided with a still-existing Jira ticket
    assert helpers.generate_internal_id() == "INT-003"
