from fastapi import APIRouter, Request
from app.db.mongo import failed_jobs_collection
from bson import ObjectId
from app.services.auth_service import require_admin
from app.services.audit_service import log_audit
from app.services.mail_service import send_email
from app.services.mailbox_service import get_mailbox_for_email_doc

router = APIRouter()

# ✅ Get all failed jobs
@router.get("/api/failed-jobs")
def get_failed_jobs():
    jobs = list(
        failed_jobs_collection.find({}, {"_id": 1, "type": 1, "retry_count": 1, "status": 1, "error": 1})
        .sort("created_at", -1)
    )

    # convert ObjectId to string
    for j in jobs:
        j["_id"] = str(j["_id"])

    return jobs


# ✅ Manual retry API
@router.post("/api/retry-job/{job_id}")
def retry_job(job_id: str, request: Request):
    actor = require_admin(request)

    job = failed_jobs_collection.find_one({"_id": ObjectId(job_id)})

    if not job:
        return {"message": "Job not found"}

    # ✅ Email jobs are not auto-retried by the scheduler, so re-send them here directly.
    if job.get("type") == "email":
        payload = job.get("payload") or {}
        try:
            sent_msg_id = send_email(
                to_list=payload["to_list"],
                cc_list=payload.get("cc_list"),
                subject=payload["subject"],
                body=payload["body"],
                mailbox=get_mailbox_for_email_doc(payload),
                from_retry=True
            )

            if not sent_msg_id:
                raise RuntimeError("Email retry failed")

            failed_jobs_collection.update_one(
                {"_id": ObjectId(job_id)},
                {"$set": {"status": "completed"}}
            )
            result_message = "Email re-sent"
        except Exception as e:
            failed_jobs_collection.update_one(
                {"_id": ObjectId(job_id)},
                {
                    "$inc": {"retry_count": 1},
                    "$set": {"status": "pending", "error": str(e)}
                }
            )
            result_message = f"Email retry failed: {e}"

        log_audit(
            request,
            "retry",
            "failed_job",
            job_id,
            {"job_type": "email", "previous_status": job.get("status"), "result": result_message},
            actor,
        )
        return {"message": result_message}

    # ✅ Jira (and other) jobs: re-queue and let the scheduler retry on its next cycle.
    failed_jobs_collection.update_one(
        {"_id": ObjectId(job_id)},
        {"$set": {"status": "pending"}}
    )

    log_audit(
        request,
        "retry",
        "failed_job",
        job_id,
        {"job_type": job.get("type"), "previous_status": job.get("status")},
        actor,
    )
    return {"message": "Retry triggered"}
