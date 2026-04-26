from fastapi import APIRouter
from app.db.mongo import db

router = APIRouter()
collection = db["rule_logs"]

@router.get("/api/rule-logs")
def get_logs():
    return list(collection.find({}, {"_id": 0}).sort("timestamp", -1))