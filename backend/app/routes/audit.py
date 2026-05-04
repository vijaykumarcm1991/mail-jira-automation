from fastapi import APIRouter, Request

from app.db.mongo import audit_logs_collection
from app.services.audit_service import serialize_audit_log
from app.services.auth_service import require_admin

router = APIRouter()


@router.get("/api/audit-logs")
def get_audit_logs(request: Request, limit: int = 100):
    require_admin(request)
    limit = min(max(limit, 1), 500)

    logs = audit_logs_collection.find({}).sort("timestamp", -1).limit(limit)
    return [serialize_audit_log(log) for log in logs]
