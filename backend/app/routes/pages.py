from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from fastapi.responses import RedirectResponse
from app.config.settings import JIRA_BASE_URL

router = APIRouter()
templates = Jinja2Templates(directory="/frontend/templates")


@router.get("/login", response_class=HTMLResponse)
def login_page(request: Request):
    return templates.TemplateResponse(request, "login.html", {})


@router.get("/dashboard", response_class=HTMLResponse)
def dashboard_page(request: Request):
    if "user" not in request.session:
        return RedirectResponse("/login")

    return templates.TemplateResponse(
        request,
        "dashboard.html",
        {
            "jira_base_url": JIRA_BASE_URL   # ✅ PASS TO UI
        }
    )

@router.get("/mappings", response_class=HTMLResponse)
def mappings_page(request: Request):
    if "user" not in request.session:
        return RedirectResponse("/login")

    return templates.TemplateResponse(request, "mappings.html", {})

@router.get("/mappings")
def mappings_page(request: Request):
    if "user" not in request.session:
        return RedirectResponse("/login")

    return templates.TemplateResponse(request, "mappings.html", {})