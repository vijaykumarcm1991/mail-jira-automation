import requests
from app.db.mongo import emails_collection
from app.config.settings import JIRA_BASE_URL, JIRA_EMAIL, JIRA_API_TOKEN


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

        if latest_status and latest_status != ticket.get("status"):
            emails_collection.update_one(
                {"internal_id": ticket["internal_id"]},
                {"$set": {"status": latest_status}}
            )

    print("Jira status sync completed.")