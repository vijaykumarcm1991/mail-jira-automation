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

def create_jira_ticket(data, rule_actions):
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
        "customfield_10095": {"value": "EMAIL"}
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
        return response.json().get("key")
    else:
        print("Jira Error:", response.text)
        return None