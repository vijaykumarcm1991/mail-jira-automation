"""Test bootstrap.

The app connects to MongoDB at import time (app/db/mongo.py builds a
MongoClient and creates indexes), and the real Jira/SMTP endpoints are remote.
Per AGENTS.md we mock Jira, mail, and MongoDB. To do that without a live
database we inject a fake `app.db.mongo` module into sys.modules BEFORE any app
code is imported, so the real module never runs.
"""

import os
import sys
import copy
import re as _re
import types

import pytest

# ── make `import app.*` work when running `pytest` from backend/ ──────────────
_BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _BACKEND_DIR not in sys.path:
    sys.path.insert(0, _BACKEND_DIR)

# ── deterministic settings (set before app.config.settings is imported) ───────
os.environ.setdefault("JIRA_BASE_URL", "https://jira.test")
os.environ.setdefault("JIRA_EMAIL", "bot@test.local")
os.environ.setdefault("JIRA_API_TOKEN", "test-token")
os.environ.setdefault("JIRA_PROJECT_KEY", "SUP")
os.environ.setdefault("JIRA_ISSUE_TYPE", "Incident")
os.environ.setdefault("TIMEZONE", "Asia/Kolkata")


# ── a minimal in-memory stand-in for a pymongo collection ─────────────────────
class FakeCollection:
    def __init__(self):
        self.docs = []

    def _matches(self, doc, flt):
        for key, cond in flt.items():
            val = doc.get(key)
            if isinstance(cond, dict):
                if "$in" in cond and val not in cond["$in"]:
                    return False
                if "$ne" in cond and val == cond["$ne"]:
                    return False
                if "$lt" in cond and not (val is not None and val < cond["$lt"]):
                    return False
                if "$regex" in cond and (val is None or not _re.search(cond["$regex"], str(val))):
                    return False
            elif val != cond:
                return False
        return True

    def _apply_update(self, doc, update, inserted):
        if "$set" in update:
            doc.update(update["$set"])
        if "$inc" in update:
            for k, amt in update["$inc"].items():
                doc[k] = doc.get(k, 0) + amt
        if inserted and "$setOnInsert" in update:
            for k, v in update["$setOnInsert"].items():
                doc.setdefault(k, v)
        return doc

    @staticmethod
    def _seed_from_filter(flt):
        return {k: v for k, v in flt.items() if not isinstance(v, dict)}

    def find_one(self, flt=None, projection=None):
        flt = flt or {}
        for doc in self.docs:
            if self._matches(doc, flt):
                return copy.deepcopy(doc)
        return None

    def find(self, flt=None, projection=None):
        flt = flt or {}
        return [copy.deepcopy(d) for d in self.docs if self._matches(d, flt)]

    def insert_one(self, doc):
        self.docs.append(copy.deepcopy(doc))

    def count_documents(self, flt=None):
        flt = flt or {}
        return sum(1 for d in self.docs if self._matches(d, flt))

    def update_one(self, flt, update, upsert=False):
        for doc in self.docs:
            if self._matches(doc, flt):
                self._apply_update(doc, update, inserted=False)
                return
        if upsert:
            self.docs.append(self._apply_update(self._seed_from_filter(flt), update, inserted=True))

    def find_one_and_update(self, flt, update, upsert=False, return_document=True, projection=None):
        for doc in self.docs:
            if self._matches(doc, flt):
                self._apply_update(doc, update, inserted=False)
                return copy.deepcopy(doc)
        if upsert:
            new = self._apply_update(self._seed_from_filter(flt), update, inserted=True)
            self.docs.append(new)
            return copy.deepcopy(new)
        return None

    def clear(self):
        self.docs = []


class FakeDB:
    def __init__(self):
        self._cols = {}

    def __getitem__(self, name):
        return self._cols.setdefault(name, FakeCollection())


# ── build + inject the fake app.db.mongo module ───────────────────────────────
_fake_db = FakeDB()
_fake_mongo = types.ModuleType("app.db.mongo")
_fake_mongo.db = _fake_db
_fake_mongo.client = None
_fake_mongo.emails_collection = _fake_db["emails"]
_fake_mongo.failed_jobs_collection = _fake_db["failed_jobs"]
_fake_mongo.users_collection = _fake_db["users"]
_fake_mongo.audit_logs_collection = _fake_db["audit_logs"]
_fake_mongo.mailboxes_collection = _fake_db["mailboxes"]
sys.modules["app.db.mongo"] = _fake_mongo


@pytest.fixture(autouse=True)
def _reset_db():
    """Every test starts with empty collections (including the id counter)."""
    for col in _fake_db._cols.values():
        col.clear()
    yield


@pytest.fixture
def fake_db():
    return _fake_db
