import requests
from app.db.mongo import emails_collection
from app.config.settings import JIRA_BASE_URL, JIRA_EMAIL, JIRA_API_TOKEN
from app.services.mail_service import send_email
from app.db.mongo import db
from app.services.jira_service import get_latest_comment, get_attachments
from app.services.jira_service import get_l3_ticket_from_jsm, fetch_l3_status, get_l3_comment, get_l3_attachments
from jinja2 import Template

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

        if latest_status and latest_status != old_status:

            emails_collection.update_one(
                {"internal_id": ticket["internal_id"]},
                {"$set": {"status": latest_status}}
            )

            # 🔥 BLOCK if already resolved by L3
            if ticket.get("resolution_source") == "L3":
                continue

            # ❗ SKIP JSM EMAIL IF L3 EXISTS
            if ticket.get("l3_jira_id"):
                continue

            if (
                latest_status.lower() == "resolved"
                and not ticket.get("resolved_email_sent")
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

                attachments = get_attachments(jira_id)

                send_email(
                    to_list=[ticket.get("from")],
                    cc_list=ticket.get("cc", []),
                    subject=f"Re: {ticket.get('subject')}",
                    body=body,
                    attachments=attachments,
                    message_id=ticket.get("message_id")
                )

                # ✅ mark email sent
                emails_collection.update_one(
                    {"_id": ticket["_id"]},
                    {
                        "$set": {
                            "resolved_email_sent": True,
                            "resolution_source": "JSM"   # 🔥 NEW
                        }
                    }
                )

        # ✅ GET L3 TICKET
        l3_jira_id = ticket.get("l3_jira_id")
        
        l3_status_val = ticket.get("l3_status")

        if l3_status_val and l3_status_val.lower() in ["resolved", "cancelled", "canceled"]:
            continue

        if not l3_jira_id:
            l3_jira_id = get_l3_ticket_from_jsm(jira_id)

        if l3_jira_id:
            emails_collection.update_one(
                {"_id": ticket["_id"]},
                {"$set": {"l3_jira_id": l3_jira_id}}
            )

            l3_status = fetch_l3_status(l3_jira_id)

            if l3_status and l3_status != ticket.get("l3_status"):

                emails_collection.update_one(
                    {"_id": ticket["_id"]},
                    {"$set": {"l3_status": l3_status}}
                )

                # 🔥 BLOCK if already resolved by JSM (rare but possible)
                if ticket.get("resolution_source") == "JSM":
                    continue

                if (
                    l3_status
                    and l3_status.lower() == "resolved"
                    and not ticket.get("l3_resolved_email_sent")
                ):

                    latest_comment = get_l3_comment(l3_jira_id)
                    attachments = get_l3_attachments(l3_jira_id)

                    template = db["email_templates"].find_one({"type": "resolved"})

                    if not template:
                        print("Resolved template not found")
                        continue
                    
                    jsm_id = ticket.get("jira_id")

                    context = {
                        "jira_id": jsm_id,
                        "status": l3_status,
                        "comment": latest_comment,
                        "l3_jira_id": ticket.get("l3_jira_id") or ""
                    }

                    body = Template(template["body"]).render(**context)

                    send_email(
                        to_list=[ticket.get("from")],
                        cc_list=ticket.get("cc", []),
                        subject=f"Re: {ticket.get('subject')}",
                        body=body,
                        attachments=attachments,
                        message_id=ticket.get("message_id")
                    )

                    emails_collection.update_one(
                        {"_id": ticket["_id"]},
                        {
                            "$set": {
                                "l3_resolved_email_sent": True,
                                "resolution_source": "L3"   # 🔥 NEW
                            }
                        }
                    )

    print("Jira status sync completed.")