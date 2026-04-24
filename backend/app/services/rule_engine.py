from app.db.mongo import db

rules_collection = db["rules"]


def match_conditions(email_data, conditions):
    sender = email_data.get("from", "").lower()
    subject = email_data.get("subject", "").lower()

    # Sender condition
    if "sender_contains" in conditions:
        if conditions["sender_contains"].lower() not in sender:
            return False

    # Subject condition (ANY match)
    if "subject_contains" in conditions:
        keywords = conditions["subject_contains"]
        if not any(k.lower() in subject for k in keywords):
            return False

    return True


def apply_rules(email_data):
    rules = list(
        rules_collection.find({"active": True}).sort("priority", 1)
    )

    for rule in rules:
        if match_conditions(email_data, rule.get("conditions", {})):
            return rule.get("actions", {})

    return {}