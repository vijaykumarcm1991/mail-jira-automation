from app.db.mongo import emails_collection
from app.config.settings import JIRA_BASE_URL, JIRA_EMAIL, JIRA_API_TOKEN
from app.services.mail_service import send_email
from app.db.mongo import db
from app.services.jira_service import get_latest_customer_visible_comment
from app.services.jira_service import get_l3_ticket_from_jsm, fetch_l3_status, add_comment_to_jira
from app.services.mailbox_service import get_mailbox_for_email_doc
from jinja2 import Template
from datetime import datetime, timedelta, timezone
import requests

RESOLUTION_EMAIL_START_AT = datetime.now(timezone.utc) - timedelta(minutes=10)


def get_resolution_source(jira_id):
    resolved_doc = emails_collection.find_one(
        {
            "jira_id": jira_id,
            "$or": [
                {"resolved_email_sent": True},
                {"l3_resolved_email_sent": True},
                {"resolution_source": {"$in": ["JSM", "L3"]}}
            ]
        },
        {"resolution_source": 1, "resolved_email_sent": 1, "l3_resolved_email_sent": 1}
    )

    if not resolved_doc:
        return None

    if resolved_doc.get("resolution_source"):
        return resolved_doc["resolution_source"]
    if resolved_doc.get("resolved_email_sent"):
        return "JSM"
    if resolved_doc.get("l3_resolved_email_sent"):
        return "L3"

    return None


def fetch_jira_status(issue_key):
    url = f"{JIRA_BASE_URL}/rest/api/3/issue/{issue_key}"

    auth = (JIRA_EMAIL, JIRA_API_TOKEN)

    headers = {
        "Accept": "application/json"
    }

    response = requests.get(url, headers=headers, auth=auth)

    if response.status_code != 200:
        print(f"Failed to fetch {issue_key}", response.text)
        return None

    return response.json()["fields"]["status"]["name"]


def parse_jira_datetime(value):
    if not value:
        return None

    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None

    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def fetch_jira_issue_state(issue_key):
    url = f"{JIRA_BASE_URL}/rest/api/3/issue/{issue_key}"

    auth = (JIRA_EMAIL, JIRA_API_TOKEN)

    headers = {
        "Accept": "application/json"
    }

    response = requests.get(url, headers=headers, auth=auth)

    if response.status_code != 200:
        print(f"Failed to fetch {issue_key}", response.text)
        return None

    fields = response.json().get("fields", {})
    return {
        "status": fields.get("status", {}).get("name"),
        "updated_at": parse_jira_datetime(fields.get("updated"))
    }


def should_send_resolution_email(ticket_data, customer_visible_comment=""):
    """
    Determine if a resolution email should be sent to the customer.

    Only sends emails when:
    1. Ticket is resolved
    2. No resolution email has been sent yet
    3. There's actually a customer-visible Reply to customer comment

    Args:
        ticket_data: Dictionary containing ticket information
        customer_visible_comment: Latest customer-visible Reply to customer comment

    Returns:
        bool: True if resolution email should be sent
    """
    if ticket_data.get('status', '').lower() != 'resolved':
        return False

    if ticket_data.get('resolved_email_sent'):
        return False

    return bool(customer_visible_comment)


def sync_jira_status():
    print("Syncing Jira ticket statuses...")

    tickets = emails_collection.aggregate([
        {
            "$match": {
                "jira_id": {"$ne": None},
                "status": {
                    "$nin": ["Resolved", "Cancelled", "Canceled"]
                }
            }
        },
        {
            "$sort": {
                "created_at": 1
            }
        },
        {
            "$group": {
                "_id": "$jira_id",
                "doc": {"$first": "$$ROOT"}
            }
        }
    ])

    for item in tickets:
        ticket = item["doc"]

        jira_id = ticket.get("jira_id")

        if not jira_id:
            continue

        latest_state = fetch_jira_issue_state(jira_id)
        latest_status = latest_state.get("status") if latest_state else None
        latest_updated_at = latest_state.get("updated_at") if latest_state else None
        old_status = ticket.get("status")
        resolution_source = get_resolution_source(jira_id)

        if latest_status and latest_status != old_status:

            emails_collection.update_many(
                {"jira_id": jira_id},
                {"$set": {"status": latest_status}}
            )

        if latest_status and latest_status != old_status:
            # 🔥 BLOCK if already resolved by L3
            if resolution_source == "L3":
                continue

            if (
                latest_status.lower() == "resolved"
                and not ticket.get("resolved_email_sent")
                and resolution_source is None
            ):
                if latest_updated_at and latest_updated_at < RESOLUTION_EMAIL_START_AT:
                    print(f"Skipping resolution email for {jira_id} - resolved before this service run")
                    continue

                # Only send customer mail from the latest Reply to customer comment.
                latest_visible_comment = get_latest_customer_visible_comment(jira_id)
                if not should_send_resolution_email({**ticket, "status": latest_status}, latest_visible_comment):
                    print(f"Skipping resolution email for {jira_id} - no customer-visible reply comment")
                    continue

                template = db["email_templates"].find_one({"type": "resolved"})

                if not template:
                    print("Resolved template not found")
                    continue

                context = {
                    "jira_id": jira_id,
                    "status": latest_status,
                    "comment": latest_visible_comment,
                    "l3_jira_id": ticket.get("l3_jira_id") or ""
                }

                body = Template(template["body"]).render(**context)

                sent_msg_id = send_email(
                    to_list=[ticket.get("from")],
                    cc_list=ticket.get("cc", []),
                    subject=f"Re: {ticket.get('subject')}",
                    body=body,
                    message_id=ticket.get("message_id"),
                    mailbox=get_mailbox_for_email_doc(ticket),
                    metadata={"purpose": "resolution", "jira_id": jira_id}
                )

                if not sent_msg_id:
                    print(f"Resolution email failed for {jira_id}")
                    continue

                # ✅ mark email sent
                emails_collection.update_many(
                    {"jira_id": jira_id},
                    {
                        "$set": {
                            "status": latest_status,
                            "resolved_email_sent": True,
                            "resolution_source": "JSM"
                        }
                    }
                )
                resolution_source = "JSM"

        # ✅ GET L3 TICKET
        l3_jira_id = ticket.get("l3_jira_id")

        l3_status_val = ticket.get("l3_status")

        if l3_status_val and l3_status_val.lower() in ["resolved", "cancelled", "canceled"]:
            continue

        if not l3_jira_id:
            l3_jira_id = get_l3_ticket_from_jsm(jira_id)

        if l3_jira_id:
            emails_collection.update_many(
                {"jira_id": jira_id},
                {"$set": {"l3_jira_id": l3_jira_id}}
            )

            l3_status = fetch_l3_status(l3_jira_id)

            if l3_status and l3_status != ticket.get("l3_status"):

                emails_collection.update_many(
                    {"jira_id": jira_id},
                    {"$set": {"l3_status": l3_status}}
                )

                # 🔥 ADD INTERNAL COMMENT FOR L3 STATUS UPDATE (no customer notification)
                if l3_status.lower() in ["resolved", "cancelled", "canceled"]:
                    internal_comment = f"L3 ticket status updated to: {l3_status}"
                    add_comment_to_jira(jira_id, internal_comment, is_customer_visible=False)
                    print(f"Added internal comment for L3 status update: {jira_id} → {l3_status}")

    print("Jira status sync completed.")
