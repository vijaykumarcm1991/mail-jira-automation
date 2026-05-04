from fastapi import APIRouter, Request
from app.db.mongo import emails_collection
from app.utils.helpers import generate_internal_id
from app.models.email_model import create_email_doc
from app.services.auth_service import require_admin

router = APIRouter()

@router.get("/api/tickets")
def get_tickets():
    tickets = list(
        emails_collection.find({}, {"_id": 0})
        .sort("created_at", -1)   # ✅ latest first
    )
    return tickets

@router.post("/api/tickets")
def create_ticket(request: Request):
    require_admin(request)
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

    return {"message": "Ticket created", "internal_id": internal_id}
