from datetime import datetime
import pytz

IST = pytz.timezone("Asia/Kolkata")

def create_email_doc(data):
    return {
        "internal_id": data.get("internal_id"),
        "subject": data.get("subject"),
        "from": data.get("from"),
        "cc": data.get("cc", []),
        "jira_id": data.get("jira_id"),
        "status": data.get("status", "Open"),
        "message_id": data.get("message_id"),
        "system_message_id": data.get("system_message_id"),
        "created_at": datetime.now(IST)
    }