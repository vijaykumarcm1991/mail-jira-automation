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
    
def _extract_adf_text(node):
    if isinstance(node, dict):
        text = node.get("text", "")
        child_text = [_extract_adf_text(child) for child in node.get("content", [])]
        return "\n".join(part for part in [text, *child_text] if part)

    if isinstance(node, list):
        return "\n".join(part for child in node for part in [_extract_adf_text(child)] if part)

    return ""


def _extract_comment_text(comment):
    body = comment.get("body", "")
    if isinstance(body, str):
        return body.strip()
    return _extract_adf_text(body).strip()


def _get_comment_property(comment_id, property_key):
    url = f"{JIRA_BASE_URL}/rest/api/3/comment/{comment_id}/properties/{property_key}"

    auth = (JIRA_EMAIL, JIRA_API_TOKEN)
    headers = {
        "Accept": "application/json"
    }

    response = requests.get(url, headers=headers, auth=auth)

    if response.status_code == 404:
        return None

    if response.status_code != 200:
        print(f"Failed to fetch comment property {property_key} for {comment_id}: {response.status_code} {response.text}")
        return None

    return response.json().get("value")


def _property_value_is_true(value):
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.lower() == "true"
    return False


def _is_platform_comment_customer_visible(comment):
    comment_id = comment.get("id")
    if not comment_id:
        return False

    visibility = _get_comment_property(comment_id, "sd.public.comment")
    if isinstance(visibility, dict):
        if isinstance(visibility.get("value"), dict):
            visibility = visibility["value"]
        if "internal" in visibility:
            return not _property_value_is_true(visibility.get("internal"))

    legacy_visibility = _get_comment_property(comment_id, "sd.allow.public.comment")
    if isinstance(legacy_visibility, dict):
        if "allow" in legacy_visibility:
            return _property_value_is_true(legacy_visibility.get("allow"))
        if "internal" in legacy_visibility:
            return not _property_value_is_true(legacy_visibility.get("internal"))

    return False


def get_latest_platform_customer_visible_comment(issue_key):
    url = f"{JIRA_BASE_URL}/rest/api/3/issue/{issue_key}/comment"

    auth = (JIRA_EMAIL, JIRA_API_TOKEN)
    headers = {
        "Accept": "application/json"
    }
    params = {
        "maxResults": 100,
        "orderBy": "-created"
    }

    response = requests.get(url, headers=headers, auth=auth, params=params)

    if response.status_code != 200:
        print(f"Failed to fetch Jira comments for {issue_key}: {response.status_code} {response.text}")
        return ""

    comments = sorted(
        response.json().get("comments", []),
        key=lambda comment: comment.get("created", ""),
        reverse=True
    )
    for comment in comments:
        if _is_platform_comment_customer_visible(comment):
            return _extract_comment_text(comment)

    return ""


def get_latest_customer_visible_comment(issue_key):
    """Return the latest JSM Cloud Reply to customer comment only."""
    url = f"{JIRA_BASE_URL}/rest/servicedeskapi/request/{issue_key}/comment"

    auth = (JIRA_EMAIL, JIRA_API_TOKEN)
    headers = {
        "Accept": "application/json"
    }
    params = {
        "public": "true",
        "internal": "false",
        "limit": 100
    }

    response = requests.get(url, headers=headers, auth=auth, params=params)

    if response.status_code != 200:
        print(f"Failed to fetch JSM public comments for {issue_key}: {response.status_code} {response.text}")
        return get_latest_platform_customer_visible_comment(issue_key)

    comments = response.json().get("values", [])
    public_comments = sorted([
        comment for comment in comments
        if comment.get("public") is not False
    ], key=lambda comment: comment.get("created", ""), reverse=True)

    if not public_comments:
        return ""

    return _extract_comment_text(public_comments[0])


def get_latest_comment(issue_key, include_internal=False):
    """Get the latest comment from a JSM ticket.

    Args:
        issue_key: The JSM ticket key (e.g., 'TICKET-123')
        include_internal: If False (default), only return customer-visible comments.
                         If True, include internal comments too.

    Returns:
        str: The latest comment text (Jira ADF format parsed), or "" if no comments
    """
    url = f"{JIRA_BASE_URL}/rest/api/3/issue/{issue_key}/comment?expand=properties"

    auth = (JIRA_EMAIL, JIRA_API_TOKEN)
    response = requests.get(url, auth=auth)

    if response.status_code != 200:
        return ""

    comments = response.json().get("comments", [])
    if not comments:
        return ""

    # Filter out internal comments if requested.
    if not include_internal:
        visible_comments = [
            c for c in comments
            if not any(prop.get("key") == "sd.public.comment" and prop.get("value", {}).get("internal") is True
                      for prop in c.get("properties", []))
        ]
        if not visible_comments:
            return ""
        comments = visible_comments

    latest = comments[-1]
    return _extract_comment_text(latest)


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
