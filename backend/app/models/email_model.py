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
        "mailbox_id": data.get("mailbox_id"),
        "mailbox_email": data.get("mailbox_email"),
        "status": data.get("status", "Open"),
        "message_id": data.get("message_id"),
        "system_message_id": data.get("system_message_id"),
        "email_attachments": data.get("email_attachments", []),
        "l3_jira_id": data.get("l3_jira_id"),
        "l3_status": data.get("l3_status"),
        "resolved_email_sent": data.get("resolved_email_sent", False),
        "l3_resolved_email_sent": data.get("l3_resolved_email_sent", False),
        "resolution_source": data.get("resolution_source"),
        "created_at": datetime.now(IST)
    }
