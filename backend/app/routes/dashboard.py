from fastapi import APIRouter, Request
from app.db.mongo import db, emails_collection, failed_jobs_collection
from app.utils.helpers import generate_internal_id
from app.models.email_model import create_email_doc
from app.services.auth_service import require_admin
from app.services.audit_service import log_audit
from app.config.settings import TIMEZONE
import pytz

router = APIRouter()
IST = pytz.timezone(TIMEZONE)

def serialize_timestamp(value):
    if not value:
        return None
    if hasattr(value, "isoformat"):
        if value.tzinfo is None:
            value = pytz.utc.localize(value)
        return value.astimezone(IST).isoformat()
    return str(value)


def add_timeline_event(timeline, label, timestamp=None, status="info", detail=None):
    timeline.append({
        "label": label,
        "timestamp": serialize_timestamp(timestamp),
        "status": status,
        "detail": detail
    })


def build_ticket_timeline(ticket):
    timeline = []
    created_at = ticket.get("created_at")
    updated_at = ticket.get("updated_at") or created_at
    rule_log = ticket.get("_timeline_rule_log") or {}
    failed_job = ticket.get("_timeline_failed_job") or {}
    status = str(ticket.get("status") or "").lower()
    l3_status = str(ticket.get("l3_status") or "").lower()

    add_timeline_event(timeline, "Email received", created_at, "info", ticket.get("from"))

    if ticket.get("jira_id"):
        add_timeline_event(timeline, "Jira ticket created", created_at, "success", ticket.get("jira_id"))
    else:
        add_timeline_event(timeline, "Jira ticket created", created_at, "error", "Jira ID missing")

    if ticket.get("system_message_id"):
        add_timeline_event(timeline, "Create email triggered", created_at, "success")

    if rule_log:
        label = "Rule matched" if rule_log.get("matched") else "Rule matched"
        event_status = "success" if rule_log.get("matched") else "warning"
        detail = rule_log.get("rule_name") or "No Rule Matched"
        add_timeline_event(timeline, label, rule_log.get("timestamp") or created_at, event_status, detail)

    if status == "comment added":
        add_timeline_event(timeline, "Comment added", updated_at, "success")

    if ticket.get("customer_comment_found"):
        add_timeline_event(timeline, "Customer-facing comment found", updated_at, "success")
    elif ticket.get("resolved_email_sent"):
        add_timeline_event(timeline, "Customer-facing comment found", updated_at, "success")
    elif status == "resolved" and not ticket.get("resolved_email_sent"):
        add_timeline_event(timeline, "Customer-facing comment not found", updated_at, "warning")

    if ticket.get("l3_jira_id"):
        add_timeline_event(timeline, "L3 escalation detected", updated_at, "info")
        add_timeline_event(timeline, "L3 ticket created", updated_at, "success", ticket.get("l3_jira_id"))

    if ticket.get("status"):
        add_timeline_event(timeline, "Jira status changed", updated_at, "info", ticket.get("status"))

    if status in ["resolved", "cancelled", "canceled"] or l3_status in ["resolved", "cancelled", "canceled"]:
        add_timeline_event(timeline, "Ticket resolved", updated_at, "success")

    if ticket.get("resolved_email_sent") or ticket.get("l3_resolved_email_sent"):
        add_timeline_event(timeline, "Resolved email triggered", updated_at, "success")
    elif status == "resolved":
        add_timeline_event(timeline, "Resolved email skipped", updated_at, "warning")

    for attachment in ticket.get("email_attachments") or []:
        add_timeline_event(timeline, "Attachment uploaded", created_at, "success", attachment)

    if failed_job:
        add_timeline_event(
            timeline,
            "Retry triggered",
            failed_job.get("created_at") or updated_at,
            "warning",
            f"Retry {failed_job.get('retry_count', 0)}"
        )

    if status == "failed" or ticket.get("error") or failed_job.get("error"):
        add_timeline_event(timeline, "Error occurred", updated_at, "error", ticket.get("error") or failed_job.get("error"))

    return timeline


@router.get("/api/tickets")
def get_tickets():
    tickets = list(
        emails_collection.find({}, {"_id": 0})
        .sort("created_at", -1)   # ✅ latest first
    )
    internal_ids = [ticket.get("internal_id") for ticket in tickets if ticket.get("internal_id")]
    jira_ids = [ticket.get("jira_id") for ticket in tickets if ticket.get("jira_id")]

    rule_logs = {}
    for log in db["rule_logs"].find(
        {"internal_id": {"$in": internal_ids}},
        {"_id": 0}
    ).sort("timestamp", -1):
        internal_id = log.get("internal_id")
        if internal_id and internal_id not in rule_logs:
            rule_logs[internal_id] = log

    failed_jobs = {}
    for job in failed_jobs_collection.find(
        {
            "status": {"$ne": "completed"},
            "$or": [
                {"payload.data.internal_id": {"$in": internal_ids}},
                {"payload.metadata.jira_id": {"$in": jira_ids}}
            ]
        },
        {"_id": 0}
    ).sort("created_at", -1):
        payload = job.get("payload") or {}
        data = payload.get("data") or {}
        key = data.get("internal_id") or payload.get("metadata", {}).get("jira_id")
        if key and key not in failed_jobs:
            failed_jobs[key] = job

    for ticket in tickets:
        ticket["_timeline_rule_log"] = rule_logs.get(ticket.get("internal_id"))
        ticket["_timeline_failed_job"] = (
            failed_jobs.get(ticket.get("internal_id"))
            or failed_jobs.get(ticket.get("jira_id"))
        )
        ticket["timeline"] = build_ticket_timeline(ticket)
        ticket.pop("_timeline_rule_log", None)
        ticket.pop("_timeline_failed_job", None)
    return tickets

@router.post("/api/tickets")
def create_ticket(request: Request):
    actor = require_admin(request)
    internal_id = generate_internal_id()

    data = {
        "internal_id": internal_id,
        "subject": "Test Email Issue",
        "from": "test@gmail.com",
        "jira_id": "SUP-123",
        "status": "Open"
    }

    doc = create_email_doc(data)
    emails_collection.insert_one(doc)

    log_audit(
        request,
        "create",
        "ticket",
        internal_id,
        {"jira_id": data["jira_id"], "source": "manual_api"},
        actor,
    )
    return {"message": "Ticket created", "internal_id": internal_id}
