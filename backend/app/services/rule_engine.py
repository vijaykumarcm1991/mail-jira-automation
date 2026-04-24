from app.db.mongo import db

rules_collection = db["rules"]


def match_conditions(email_data, conditions):
    sender = email_data.get("from", "").lower()
    subject = email_data.get("subject", "").lower()
    body = email_data.get("description", "").lower()

    condition_type = conditions.get("type", "AND")

    results = []

    # Sender condition
    if "sender_contains" in conditions:
        results.append(
            conditions["sender_contains"].lower() in sender
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

    for rule in rules:
        if match_conditions(email_data, rule.get("conditions", {})):
            return rule.get("actions", {})

    return {}