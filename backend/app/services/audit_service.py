from datetime import datetime

import pytz

from app.config.settings import TIMEZONE
from app.db.mongo import audit_logs_collection
from app.services.auth_service import get_user_from_request

IST = pytz.timezone(TIMEZONE)


def log_audit(request, action, resource, resource_id=None, details=None, actor=None):
    user = actor or get_user_from_request(request)
    audit_logs_collection.insert_one({
        "timestamp": datetime.now(IST),
        "actor": user.get("username") or "public",
        "role": user.get("role", "public"),
        "action": action,
        "resource": resource,
        "resource_id": resource_id,
        "details": details or {},
        "client_ip": request.client.host if request and request.client else None,
        "user_agent": request.headers.get("user-agent") if request else None,
    })


def serialize_audit_log(log):
    log["_id"] = str(log["_id"])
    if log.get("timestamp"):
        timestamp = log["timestamp"]
        if timestamp.tzinfo is None:
            timestamp = pytz.utc.localize(timestamp)
        log["timestamp"] = timestamp.astimezone(IST).isoformat()
    return log
