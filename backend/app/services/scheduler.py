import time
import threading
from app.services.mail_service import fetch_unseen_emails
from app.services.jira_sync_service import sync_jira_fields
from app.services.jira_status_service import sync_jira_status
from app.db.mongo import failed_jobs_collection
from app.services.jira_service import create_jira_ticket
from app.services.mail_service import send_email
from app.services.mailbox_service import get_enabled_mailboxes, get_mailbox_by_id


def start_mail_listener():
    while True:
        try:
            print("Checking for new emails...")
            for mailbox in get_enabled_mailboxes():
                print(f"Checking mailbox: {mailbox.get('email')}")
                fetch_unseen_emails(mailbox)

            # ✅ Sync Jira fields every cycle (temporary)
            sync_jira_fields()
            # ✅ Sync Jira statuses every cycle (temporary)
            sync_jira_status()
            # ✅ Retry failed jobs every cycle (temporary)
            retry_failed_jobs()

        except Exception as e:
            print("Error:", e)

        time.sleep(15) # Check every 15 seconds

def start_background_thread():
    thread = threading.Thread(target=start_mail_listener, daemon=True)
    thread.start()

def retry_failed_jobs():
    jobs = list(failed_jobs_collection.find({"status": "pending", "retry_count": {"$lt": 3}}))

    for job in jobs:
        try:
            if job["type"] == "jira":
                payload = job["payload"]
                result = create_jira_ticket(payload["data"], payload["rule_actions"])

                if result:
                    failed_jobs_collection.update_one(
                        {"_id": job["_id"]},
                        {"$set": {"status": "completed"}}
                    )
                    continue

            elif job["type"] == "email":
                payload = job["payload"]

                send_email(
                    to_list=payload["to_list"],
                    cc_list=payload.get("cc_list"),
                    subject=payload["subject"],
                    body=payload["body"],
                    mailbox=get_mailbox_by_id(payload.get("mailbox_id"))
                )

                failed_jobs_collection.update_one(
                    {"_id": job["_id"]},
                    {"$set": {"status": "completed"}}
                )
                continue

        except Exception as e:
            failed_jobs_collection.update_one(
                {"_id": job["_id"]},
                {
                    "$inc": {"retry_count": 1},
                    "$set": {"error": str(e)}
                }
            )

            # ✅ ALERT AFTER 3 FAILURES
            if job.get("retry_count", 0) >= 2:
                print("ALERT: Job failed multiple times:", job["_id"])
