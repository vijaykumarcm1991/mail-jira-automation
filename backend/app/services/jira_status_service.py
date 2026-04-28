import requests
from app.db.mongo import emails_collection
from app.config.settings import JIRA_BASE_URL, JIRA_EMAIL, JIRA_API_TOKEN
from app.services.mail_service import send_email
from app.db.mongo import db
from app.services.jira_service import get_latest_comment, get_attachments
from app.services.jira_service import get_l3_ticket_from_jsm, fetch_l3_status, get_l3_comment, get_l3_attachments

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
        {"$match": {"jira_id": {"$ne": None}}},
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

            if (
                latest_status.lower() == "resolved"
                and not ticket.get("resolved_email_sent")
            ):

                template = db["email_templates"].find_one({"type": "resolved"})

                latest_comment = get_latest_comment(jira_id)

                body = template["body"] \
                    .replace("{{jira_id}}", jira_id) \
                    .replace("{{status}}", latest_status) \
                    .replace("{{comment}}", latest_comment)

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
                    {"$set": {"resolved_email_sent": True}}
                )

        # ✅ GET L3 TICKET
        l3_jira_id = ticket.get("l3_jira_id")
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

                if (
                    l3_status.lower() == "resolved"
                    and not ticket.get("l3_resolved_email_sent")
                ):

                    latest_comment = get_l3_comment(l3_jira_id)
                    attachments = get_l3_attachments(l3_jira_id)

                    template = db["email_templates"].find_one({"type": "resolved"})

                    body = template["body"] \
                        .replace("{{jira_id}}", l3_jira_id) \
                        .replace("{{status}}", l3_status) \
                        .replace("{{comment}}", latest_comment)

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
                        {"$set": {"l3_resolved_email_sent": True}}
                    )

    print("Jira status sync completed.")