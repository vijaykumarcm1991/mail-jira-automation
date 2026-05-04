from fastapi import APIRouter, Request
from app.db.mongo import db
from app.services.auth_service import require_admin

router = APIRouter()
collection = db["email_templates"]

@router.get("/api/templates")
def get_templates():
    return list(collection.find({}, {"_id": 0}))

@router.post("/api/templates")
def save_template(data: dict, request: Request):
    require_admin(request)
    collection.update_one(
        {"type": data["type"]},
        {"$set": data},
        upsert=True
    )
    return {"message": "Saved"}
