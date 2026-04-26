from fastapi import APIRouter
from app.db.mongo import db

router = APIRouter()
collection = db["email_templates"]

@router.get("/api/templates")
def get_templates():
    return list(collection.find({}, {"_id": 0}))

@router.post("/api/templates")
def save_template(data: dict):
    collection.update_one(
        {"type": data["type"]},
        {"$set": data},
        upsert=True
    )
    return {"message": "Saved"}