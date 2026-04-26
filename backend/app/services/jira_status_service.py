import requests
from app.db.mongo import emails_collection
from app.config.settings import JIRA_BASE_URL, JIRA_EMAIL, JIRA_API_TOKEN
from app.services.mail_service import send_email
from app.db.mongo import db
from app.services.jira_service import get_latest_comment, get_attachments

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

    tickets = emails_collection.find({
        "jira_id": {"$ne": None}
    })

    for ticket in tickets:
        jira_id = ticket.get("jira_id")

        if not jira_id:
            continue

        latest_status = fetch_jira_status(jira_id)

        old_status = ticket.get("status")

        if latest_status and latest_status != old_status:

            # ✅ 1. Update DB
            emails_collection.update_one(
                {"internal_id": ticket["internal_id"]},
                {"$set": {"status": latest_status}}
            )

            # ✅ 2. SEND EMAIL ONLY IF RESOLVED
            if latest_status.lower() == "resolved":

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
                    subject=template["subject"].replace("{{jira_id}}", jira_id),
                    body=body,
                    attachments=attachments
                )

    print("Jira status sync completed.")