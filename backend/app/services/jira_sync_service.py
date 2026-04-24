import requests
from datetime import datetime
import pytz
from app.config.settings import JIRA_ISSUE_TYPE

from app.config.settings import (
    JIRA_BASE_URL,
    JIRA_EMAIL,
    JIRA_API_TOKEN,
    JIRA_CUSTOM_FIELDS,
    TIMEZONE,
    JIRA_PROJECT_KEY,      # ✅ ADD THIS
    JIRA_ISSUE_TYPE        # ✅ ADD THIS
)

from app.db.mongo import db

collection = db["jira_field_options"]

IST = pytz.timezone(TIMEZONE)

def fetch_create_meta():
    url = f"{JIRA_BASE_URL}/rest/api/3/issue/createmeta"

    auth = (JIRA_EMAIL, JIRA_API_TOKEN)

    params = {
        "projectKeys": JIRA_PROJECT_KEY,
        "issuetypeNames": JIRA_ISSUE_TYPE,
        "expand": "projects.issuetypes.fields"
    }

    headers = {
        "Accept": "application/json"
    }

    response = requests.get(url, headers=headers, auth=auth, params=params)

    if response.status_code != 200:
        print("Failed to fetch create meta:", response.text)
        return {}

    return response.json()

def extract_field_options(meta_response):
    result = {}

    projects = meta_response.get("projects", [])

    for project in projects:
        for issuetype in project.get("issuetypes", []):

            fields = issuetype.get("fields", {})

            for field_key, field_data in fields.items():

                if field_key not in [f["key"] for f in JIRA_CUSTOM_FIELDS]:
                    continue

                allowed = field_data.get("allowedValues", [])

                options = []

                for opt in allowed:
                    if "value" in opt:
                        options.append({
                            "id": opt.get("id"),
                            "value": opt.get("value")
                        })

                result[field_key] = options

    return result

def sync_jira_fields():
    print("Syncing Jira dropdown fields...")

    meta = fetch_create_meta()
    extracted = extract_field_options(meta)

    for field in JIRA_CUSTOM_FIELDS:

        options = extracted.get(field["key"], [])

        collection.update_one(
            {"field_key": field["key"]},
            {
                "$set": {
                    "field_name": field["name"],
                    "options": options,
                    "last_synced": datetime.now(IST)
                }
            },
            upsert=True
        )

    print("Jira field sync completed.")