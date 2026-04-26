import os
from dotenv import load_dotenv

load_dotenv()

MONGO_URI = os.getenv("MONGO_URI")

EMAIL_ACCOUNT = os.getenv("EMAIL_ACCOUNT")
EMAIL_PASSWORD = os.getenv("EMAIL_PASSWORD")
IMAP_SERVER = os.getenv("IMAP_SERVER")

TIMEZONE = os.getenv("TIMEZONE", "Asia/Kolkata")

JIRA_BASE_URL = os.getenv("JIRA_BASE_URL")
JIRA_EMAIL = os.getenv("JIRA_EMAIL")
JIRA_API_TOKEN = os.getenv("JIRA_API_TOKEN")
JIRA_PROJECT_KEY = os.getenv("JIRA_PROJECT_KEY")
JIRA_ISSUE_TYPE = os.getenv("JIRA_ISSUE_TYPE", "Incident")

JIRA_CUSTOM_FIELDS = [
    {"key": "customfield_10085", "name": "Application"},
    {"key": "customfield_10097", "name": "Geography"},
    {"key": "customfield_10091", "name": "Country"},
    {"key": "customfield_10086", "name": "Unit"},
    {"key": "priority", "name": "Priority"}
]

SMTP_HOST = os.getenv("SMTP_HOST")
SMTP_PORT = int(os.getenv("SMTP_PORT", 465))
SMTP_USER = os.getenv("SMTP_USER")
SMTP_PASS = os.getenv("SMTP_PASS")

# ❗ Source excluded (fixed as EMAIL)