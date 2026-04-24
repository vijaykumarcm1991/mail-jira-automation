from fastapi import APIRouter
from app.db.mongo import db

router = APIRouter()

collection = db["jira_field_options"]


@router.get("/api/jira-options")
def get_jira_options():
    data = list(collection.find({}, {"_id": 0}))
    return data