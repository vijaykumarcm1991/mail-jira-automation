from fastapi import APIRouter

router = APIRouter()

@router.get("/api/tickets")
def get_tickets():
    return [
        {
            "internal_id": "INT-001",
            "subject": "Login issue",
            "jira_id": "SUP-101",
            "status": "Open"
        }
    ]