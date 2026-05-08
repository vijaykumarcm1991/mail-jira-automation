import requests
from app.db.mongo import emails_collection
from app.config.settings import JIRA_BASE_URL, JIRA_EMAIL, JIRA_API_TOKEN
from app.services.mail_service import send_email
from app.db.mongo import db
from app.services.jira_service import get_latest_comment
import requests
from app.db.mongo import emails_collection
from app.config.settings import JIRA_BASE_URL, JIRA_EMAIL, JIRA_API_TOKEN
from app.services.mail_service import send_email
from app.db.mongo import db
from app.services.jira_service import get_latest_comment
from app.services.jira_service import get_l3_ticket_from_jsm, fetch_l3_status, get_l3_comment, add_comment_to_jira
from app.services.mailbox_service import get_mailbox_for_email_doc
from jinja2 import Template
from app.services.mailbox_service import get_mailbox_for_email_doc
from jinja2 import Template


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


def should_send_resolution_email(ticket_data, has_customer_visible_resolution=False):
    """
    Determine if a resolution email should be sent to the customer.

    Only sends emails when:
    1. Ticket is resolved
    2. No resolution email has been sent yet
    3. There's actually a customer-visible resolution comment

    Args:
        ticket_data: Dictionary containing ticket information
        has_customer_visible_resolution: Whether there's a customer-visible resolution comment

    Returns:
        bool: True if resolution email should be sent
    """
    if ticket_data.get('status', '').lower() != 'resolved':
        return False

    if ticket_data.get('resolved_email_sent'):
        return False

    # Only send email if there's a actual customer-visible resolution comment
    return has_customer_visible_resolution


def is_resolution_comment(comment_text):
    """Check if a comment indicates resolution (closed, resolved, fixed, etc.)"""
    if not comment_text:
        return False

    resolution_keywords = ['resolved', 'closed', 'fixed', 'completed', 'done', 'cancelled', 'canceled']
    comment_lower = comment_text.lower()

    return any(keyword in comment_lower for keyword in resolution_keywords)


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

        latest_status = fetch_jira_status(jira_id)
        old_status = ticket.get("status")
        resolution_source = get_resolution_source(jira_id)

        if latest_status and latest_status != old_status:

            emails_collection.update_many(
                {"jira_id": jira_id},
                {"$set": {"status": latest_status}}
            )

            # 🔥 BLOCK if already resolved by L3
            if resolution_source == "L3":
                continue

            if (
                latest_status.lower() == "resolved"
                and not ticket.get("resolved_email_sent")
                and resolution_source is None
            ):

                # Check if there's actually a customer-visible resolution comment
                latest_visible_comment = get_latest_comment(jira_id, include_internal=False)
                if not latest_visible_comment or not is_resolution_comment(latest_visible_comment):
                    # No customer-visible resolution comment, skip sending email
                    print(f"Skipping resolution email for {jira_id} - resolution not in customer-visible comment")
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

                send_email(
                    to_list=[ticket.get("from")],
                    cc_list=ticket.get("cc", []),
                    subject=f"Re: {ticket.get('subject')}",
                    body=body,
                    message_id=ticket.get("message_id"),
                    mailbox=get_mailbox_for_email_doc(ticket)
                )

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