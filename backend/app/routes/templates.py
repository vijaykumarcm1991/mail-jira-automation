from fastapi import APIRouter, Request
from app.db.mongo import db
from app.services.auth_service import require_admin
from app.services.audit_service import log_audit

router = APIRouter()
collection = db["email_templates"]

@router.get("/api/templates")
def get_templates():
    return list(collection.find({}, {"_id": 0}))

@router.post("/api/templates")
def save_template(data: dict, request: Request):
    actor = require_admin(request)
    existing = collection.find_one({"type": data["type"]})
    collection.update_one(
        {"type": data["type"]},
        {"$set": data},
        upsert=True
    )
    log_audit(
        request,
        "update" if existing else "create",
        "email_template",
        data["type"],
        {"body_length": len(data.get("body", ""))},
        actor,
    )
    return {"message": "Saved"}
