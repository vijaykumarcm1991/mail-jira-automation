from fastapi import APIRouter, Request, Form
from fastapi.responses import RedirectResponse

router = APIRouter()

# Hardcoded users (Phase 1 only)
USERS = {
    "admin": {"password": "admin123", "role": "admin"},
    "user": {"password": "user123", "role": "public"}
}


@router.post("/login")
def login(request: Request, username: str = Form(...), password: str = Form(...)):
    user = USERS.get(username)

    if user and user["password"] == password:
        request.session["user"] = username
        request.session["role"] = user["role"]
        return RedirectResponse("/dashboard", status_code=302)

    return RedirectResponse("/login", status_code=302)


@router.get("/logout")
def logout(request: Request):
    request.session.clear()
    return RedirectResponse("/login", status_code=302)