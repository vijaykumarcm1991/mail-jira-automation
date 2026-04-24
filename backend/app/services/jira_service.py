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

def create_jira_ticket(data):
    url = f"{JIRA_BASE_URL}/rest/api/3/issue"

    auth = (JIRA_EMAIL, JIRA_API_TOKEN)

    headers = {
        "Accept": "application/json",
        "Content-Type": "application/json"
    }

    payload = {
        "fields": {
            "project": {
                "key": JIRA_PROJECT_KEY
            },
            "summary": data.get("subject"),
            "description": {
                "type": "doc",
                "version": 1,
                "content": [
                    {
                        "type": "paragraph",
                        "content": [
                            {
                                "text": data.get("description"),
                                "type": "text"
                            }
                        ]
                    }
                ]
            },
            "issuetype": {
                "name": JIRA_ISSUE_TYPE
            }
        }
    }

    response = requests.post(url, json=payload, headers=headers, auth=auth)

    if response.status_code == 201:
        return response.json().get("key")
    else:
        print("Jira Error:", response.text)
        return None