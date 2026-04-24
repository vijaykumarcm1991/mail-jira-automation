from fastapi import APIRouter
from datetime import datetime
import pytz

from app.db.mongo import db
from app.config.settings import TIMEZONE

router = APIRouter()

collection = db["rules"]
IST = pytz.timezone(TIMEZONE)


@router.get("/api/rules")
def get_rules():
    return list(collection.find({}, {"_id": 0}))


@router.post("/api/rules")
def create_or_update_rule(rule: dict):
    rule["created_at"] = datetime.now(IST)

    collection.update_one(
        {"rule_name": rule["rule_name"]},   # unique key
        {"$set": rule},
        upsert=True
    )

    return {"message": "Rule saved"}


@router.delete("/api/rules/{rule_name}")
def delete_rule(rule_name: str):
    collection.delete_one({"rule_name": rule_name})
    return {"message": "Rule deleted"}