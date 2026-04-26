from app.db.mongo import db
from datetime import datetime
import pytz
from app.config.settings import TIMEZONE

rules_collection = db["rules"]


def match_conditions(email_data, conditions):
    sender = email_data.get("from", "").lower()
    subject = email_data.get("subject", "").lower()
    body = email_data.get("description", "").lower()

    condition_type = conditions.get("type", "AND")

    results = []

    # Sender conditions (multiple)
    if "sender_contains" in conditions:
        sender_list = conditions["sender_contains"]

        results.append(
            any(s.lower() in sender for s in sender_list)
        )

    # Subject/Body keywords
    if "subject_contains" in conditions:
        keywords = conditions["subject_contains"]
        results.append(
            any(k.lower() in subject or k.lower() in body for k in keywords)
        )

    # Apply AND / OR
    if condition_type == "OR":
        return any(results)
    else:
        return all(results)


def apply_rules(email_data):
    rules = list(
        rules_collection.find({"active": True}).sort("priority", 1)
    )

    log_collection = db["rule_logs"]
    IST = pytz.timezone(TIMEZONE)

    for rule in rules:
        matched = match_conditions(email_data, rule.get("conditions", {}))

        if matched:
            log_collection.insert_one({
                "internal_id": email_data.get("internal_id"),
                "rule_name": rule.get("rule_name"),
                "matched": True,
                "actions": rule.get("actions", {}),
                "timestamp": datetime.now(IST)
            })

            return rule.get("actions", {})

    # no match case
    log_collection.insert_one({
        "internal_id": email_data.get("internal_id"),
        "rule_name": None,
        "matched": False,
        "timestamp": datetime.now(IST)
    })

    return {}