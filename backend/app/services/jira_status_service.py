import requests
from app.db.mongo import emails_collection
from app.config.settings import JIRA_BASE_URL, JIRA_EMAIL, JIRA_API_TOKEN
from app.services.mail_service import send_email
from app.db.mongo import db
from app.services.jira_service import get_latest_comment
from app.services.jira_service import get_l3_ticket_from_jsm, fetch_l3_status, get_l3_comment
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


def sync_jira_status():
    print("Syncing Jira ticket statuses...")

    tickets = emails_collection.aggregate([
        {
            "$match": {
                "jira_id": {"$ne": None},
                "status": {
                    "$nin": ["Resolved", "Cancelled", "Canceled"]   # 🔥 NEW
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

                template = db["email_templates"].find_one({"type": "resolved"})

                if not template:
                    print("Resolved template not found")
                    continue

                latest_comment = get_latest_comment(jira_id)

                context = {
                    "jira_id": jira_id,
                    "status": latest_status,
                    "comment": latest_comment,
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
                            "resolution_source": "JSM"   # 🔥 NEW
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

                # 🔥 NO LONGER SEND EMAIL ON L3 RESOLUTION
                # Keep L3 status for dashboard visibility only

    print("Jira status sync completed.")
