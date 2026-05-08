import requests
from app.config.settings import (
    JIRA_BASE_URL,
    JIRA_EMAIL,
    JIRA_API_TOKEN,
    JIRA_CUSTOM_FIELDS,
    TIMEZONE,
    JIRA_PROJECT_KEY,      # ✅ ADD THIS
    JIRA_ISSUE_TYPE        # ✅ ADD THIS
)
from app.db.mongo import failed_jobs_collection
from datetime import datetime
import re
from app.config.settings import JIRA_ONPREM_URL, JIRA_ONPREM_USER, JIRA_ONPREM_PASS

def create_jira_ticket(data, rule_actions, attachments=None):
    url = f"{JIRA_BASE_URL}/rest/api/3/issue"

    auth = (JIRA_EMAIL, JIRA_API_TOKEN)

    headers = {
        "Accept": "application/json",
        "Content-Type": "application/json"
    }

    # ✅ Step 1: Base fields
    fields = {
        "project": {"key": JIRA_PROJECT_KEY},
        "summary": data.get("subject"),

        "description": {
            "type": "doc",
            "version": 1,
            "content": [
                {
                    "type": "paragraph",
                    "content": [
                        {
                            "text": data.get("description", ""),
                            "type": "text"
                        }
                    ]
                }
            ]
        },

        "issuetype": {
            "name": JIRA_ISSUE_TYPE
        },

        # ✅ FIXED SOURCE (your requirement)
        "customfield_10095": {"value": "EMAIL"},

        # ✅ NEW FIELD (Infra_App)
        "customfield_10099": {"value": "App"}
    }

    # ✅ Step 2: Apply rule-based fields

    if rule_actions.get("application"):
        fields["customfield_10085"] = {
            "value": rule_actions["application"]
        }

    if rule_actions.get("geography"):
        fields["customfield_10097"] = {
            "value": rule_actions["geography"]
        }

    if rule_actions.get("country"):
        fields["customfield_10091"] = {
            "value": rule_actions["country"]
        }

    if rule_actions.get("unit"):
        fields["customfield_10086"] = {
            "value": rule_actions["unit"]
        }
    
    # ✅ PRIORITY SUPPORT
    if rule_actions.get("priority"):
        fields["priority"] = {
            "name": rule_actions["priority"]
        }
    
    # ✅ Step 3: Final payload
    payload = {
        "fields": fields
    }

    # ✅ DEBUG LINE (ADD THIS HERE)
    print("Final Jira Fields:", fields)

    # ✅ Step 4: API call
    response = requests.post(url, json=payload, headers=headers, auth=auth)

    if response.status_code == 201:
        issue_key = response.json().get("key")

        # 🔥 UPLOAD ATTACHMENTS
        if attachments:
            upload_attachments(issue_key, attachments)

        return issue_key
    else:
        print("Jira Error:", response.text)

        # ✅ STORE FAILED JOB
        failed_jobs_collection.insert_one({
            "type": "jira",
            "payload": {
                "data": data,
                "rule_actions": rule_actions
            },
            "retry_count": 0,
            "status": "pending",
            "error": response.text,
            "created_at": datetime.utcnow()
        })

        return None
    
def get_latest_comment(issue_key):
    url = f"{JIRA_BASE_URL}/rest/api/3/issue/{issue_key}/comment"

    auth = (JIRA_EMAIL, JIRA_API_TOKEN)
    response = requests.get(url, auth=auth)

    if response.status_code != 200:
        return ""

    comments = response.json().get("comments", [])
    if not comments:
        return ""

    latest = comments[-1]
    return latest.get("body", {}).get("content", [{}])[0].get("content", [{}])[0].get("text", "")


def get_attachments(issue_key, skip_files=None):
    url = f"{JIRA_BASE_URL}/rest/api/3/issue/{issue_key}"

    auth = (JIRA_EMAIL, JIRA_API_TOKEN)
    response = requests.get(url, auth=auth)

    if response.status_code != 200:
        return []

    attachments = response.json()["fields"].get("attachment", [])

    files = []
    for att in attachments:
        filename = att["filename"]

        # 🔥 SKIP EMAIL FILES
        if skip_files and filename in skip_files:
            continue

        file_resp = requests.get(att["content"], auth=auth)
        if file_resp.status_code == 200:
            files.append((filename, file_resp.content))

    return files


def add_comment_to_jira(issue_key, comment, is_customer_visible=True):
    """Add a comment to a JSM ticket with visibility control.

    Args:
        issue_key: The JSM ticket key (e.g., 'TICKET-123')
        comment: The comment text to add
        is_customer_visible: If True, comment is visible to customers and may trigger notifications.
                           If False, comment is internal (internal note) and won't trigger notifications.

    Returns:
        bool: True if comment was successfully added, False otherwise
    """
    url = f"{JIRA_BASE_URL}/rest/api/3/issue/{issue_key}/comment"

    auth = (JIRA_EMAIL, JIRA_API_TOKEN)

    headers = {
        "Accept": "application/json",
        "Content-Type": "application/json"
    }

    payload = {
        "body": {
            "type": "doc",
            "version": 1,
            "content": [
                {
                    "type": "paragraph",
                    "content": [
                        {
                            "type": "text",
                            "text": comment
                        }
                    ]
                }
            ]
        }
    }

    # Add visibility control for JSM internal notes
    if not is_customer_visible:
        payload["properties"] = [
            {
                "key": "sd.public.comment",
                "value": {"internal": True}
            }
        ]

    response = requests.post(url, json=payload, headers=headers, auth=auth)

    if response.status_code == 201:
        return True
    else:
        print(f"Failed to add comment to {issue_key}: {response.text}")
        return False

def get_l3_ticket_from_jsm(issue_key):
    url = f"{JIRA_BASE_URL}/rest/api/3/issue/{issue_key}"

    auth = (JIRA_EMAIL, JIRA_API_TOKEN)
    response = requests.get(url, auth=auth)

    if response.status_code != 200:
        return None

    # ✅ ONLY USE REMOTE LINKS (CORRECT)
    url = f"{JIRA_BASE_URL}/rest/api/3/issue/{issue_key}/remotelink"
    response = requests.get(url, auth=auth)

    if response.status_code != 200:
        return None

    links = response.json()

    for link in links:
        url = link.get("object", {}).get("url", "")
        match = re.search(r'[A-Z]+-\d+', url)
        if match:
            return match.group(0)

    return None

def fetch_l3_status(issue_key):

    url = f"{JIRA_ONPREM_URL}/rest/api/2/issue/{issue_key}"

    response = requests.get(url, auth=(JIRA_ONPREM_USER, JIRA_ONPREM_PASS))

    if response.status_code != 200:
        return None

    return response.json()["fields"]["status"]["name"]

def get_l3_comment(issue_key):

    url = f"{JIRA_ONPREM_URL}/rest/api/2/issue/{issue_key}/comment"

    response = requests.get(url, auth=(JIRA_ONPREM_USER, JIRA_ONPREM_PASS))

    if response.status_code != 200:
        return ""

    comments = response.json().get("comments", [])
    if not comments:
        return ""

    return comments[-1].get("body", "")

def get_l3_attachments(issue_key):

    url = f"{JIRA_ONPREM_URL}/rest/api/2/issue/{issue_key}"

    response = requests.get(url, auth=(JIRA_ONPREM_USER, JIRA_ONPREM_PASS))

    if response.status_code != 200:
        return []

    attachments = response.json()["fields"].get("attachment", [])

    files = []
    for att in attachments:
        file_resp = requests.get(att["content"], auth=(JIRA_ONPREM_USER, JIRA_ONPREM_PASS))
        if file_resp.status_code == 200:
            files.append((att["filename"], file_resp.content))

    return files

def upload_attachments(issue_key, attachments):
    url = f"{JIRA_BASE_URL}/rest/api/3/issue/{issue_key}/attachments"

    auth = (JIRA_EMAIL, JIRA_API_TOKEN)

    headers = {
        "X-Atlassian-Token": "no-check"
    }

    files = []
    for filename, content in attachments:
        files.append((
            "file",
            (
                filename,
                content,
                "application/octet-stream",
                {"X-Source": "email"}  # 🔥 TAG
            )
        ))

    response = requests.post(url, headers=headers, files=files, auth=auth)

    if response.status_code not in [200, 201]:
        print(f"Attachment upload failed for {issue_key}:", response.text)