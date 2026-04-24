import time
import threading
from app.services.mail_service import fetch_unseen_emails
from app.services.jira_sync_service import sync_jira_fields
from app.services.jira_status_service import sync_jira_status


def start_mail_listener():
    while True:
        try:
            print("Checking for new emails...")
            fetch_unseen_emails()

            # ✅ Sync Jira fields every cycle (temporary)
            sync_jira_fields()
            # ✅ Sync Jira statuses every cycle (temporary)
            sync_jira_status()

        except Exception as e:
            print("Error:", e)

        time.sleep(60) # Check every minute

def start_background_thread():
    thread = threading.Thread(target=start_mail_listener, daemon=True)
    thread.start()
    