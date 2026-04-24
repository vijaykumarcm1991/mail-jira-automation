from datetime import datetime

def create_email_doc(data):
    return {
        "internal_id": data.get("internal_id"),
        "subject": data.get("subject"),
        "from": data.get("from"),
        "cc": data.get("cc", []),
        "jira_id": data.get("jira_id"),
        "status": data.get("status", "Open"),
        "created_at": datetime.utcnow()
    }