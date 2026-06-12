from pymongo import ReturnDocument
from app.db.mongo import db, emails_collection

# ✅ Monotonic counter so internal_ids are NEVER reused — a count-based id
# (INT-{count+1}) collapses after any deletion and reuses old ids, which made
# downstream lookups match stale records.
counters_collection = db["counters"]


def _ensure_counter_seeded():
    # Seed once from the highest existing INT-xxx so we never hand out an id that
    # already exists (important for databases created before this counter).
    if counters_collection.find_one({"_id": "internal_id"}):
        return

    max_seq = 0
    for doc in emails_collection.find(
        {"internal_id": {"$regex": r"^INT-\d+$"}},
        {"internal_id": 1}
    ):
        try:
            max_seq = max(max_seq, int(doc["internal_id"].split("-", 1)[1]))
        except (IndexError, ValueError):
            continue

    counters_collection.update_one(
        {"_id": "internal_id"},
        {"$setOnInsert": {"seq": max_seq}},
        upsert=True
    )


def generate_internal_id():
    _ensure_counter_seeded()
    counter = counters_collection.find_one_and_update(
        {"_id": "internal_id"},
        {"$inc": {"seq": 1}},
        upsert=True,
        return_document=ReturnDocument.AFTER
    )
    return f"INT-{counter['seq']:03d}"
