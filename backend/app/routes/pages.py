from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from app.config.settings import JIRA_BASE_URL, JIRA_ONPREM_URL
from app.services.auth_service import get_user_from_request

router = APIRouter()
templates = Jinja2Templates(directory="/frontend/templates")


def page_context(request: Request, **extra):
    user = get_user_from_request(request)
    context = {
        "request": request,
        "current_user": user,
        "is_admin": user.get("role") == "admin",
        "is_authenticated": user.get("authenticated", False),
    }
    context.update(extra)
    return context


@router.get("/login", response_class=HTMLResponse)
def login_page(request: Request):
    return templates.TemplateResponse(request, "login.html", page_context(request))


@router.get("/dashboard", response_class=HTMLResponse)
def dashboard_page(request: Request):
    return templates.TemplateResponse(
        request,
        "dashboard.html",
        page_context(
            request,
            jira_base_url=JIRA_BASE_URL,
            l3_jira_base_url=JIRA_ONPREM_URL,
        ),
    )


@router.get("/mappings", response_class=HTMLResponse)
def mappings_page(request: Request):
    return templates.TemplateResponse(request, "mappings.html", page_context(request))


@router.get("/templates", response_class=HTMLResponse)
def templates_page(request: Request):
    return templates.TemplateResponse(request, "templates.html", page_context(request))


@router.get("/admin-users", response_class=HTMLResponse)
def admin_users_page(request: Request):
    user = get_user_from_request(request)
    if user.get("role") != "admin":
        return RedirectResponse("/login")

    return templates.TemplateResponse(request, "admin_users.html", page_context(request))


@router.get("/audit", response_class=HTMLResponse)
def audit_page(request: Request):
    user = get_user_from_request(request)
    if user.get("role") != "admin":
        return RedirectResponse("/login")

    return templates.TemplateResponse(request, "audit.html", page_context(request))
