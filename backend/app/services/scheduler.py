import time
import threading
from app.services.mail_service import fetch_unseen_emails
from app.services.jira_sync_service import sync_jira_fields
from app.services.jira_status_service import sync_jira_status
from app.db.mongo import failed_jobs_collection
from app.services.jira_service import create_jira_ticket, persist_jira_id
from app.services.mailbox_service import get_enabled_mailboxes


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
    # ✅ Only Jira jobs are auto-retried. Failed email jobs are NOT auto-retried:
    # they stay "pending" so they remain visible on the dashboard and can be
    # retried manually via POST /api/retry-job/{job_id}.
    jobs = list(failed_jobs_collection.find({"type": "jira", "status": "pending", "retry_count": {"$lt": 3}}))

    for job in jobs:
        try:
            payload = job["payload"]
            # from_retry=True: create_jira_ticket must NOT insert a duplicate
            # failed-job record on failure — this existing record is reused.
            result = create_jira_ticket(payload["data"], payload["rule_actions"], from_retry=True)

            if result:
                failed_jobs_collection.update_one(
                    {"_id": job["_id"]},
                    {"$set": {"status": "completed"}}
                )
                # ✅ reflect the created ticket on the email document
                persist_jira_id(payload.get("data", {}).get("internal_id"), result)
                continue

            # create_jira_ticket returns None (no exception) on API failure,
            # so a falsy result is a failed attempt — raise so retry_count is
            # incremented below and the retry_count < 3 cap can take effect.
            raise RuntimeError(job.get("error") or "Jira creation failed on retry")

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
