from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from app.routes import dashboard
from app.routes import pages
from app.routes import auth
from app.services.scheduler import start_background_thread
from app.routes import jira_options
from app.routes import rules
from app.routes import rule_logs
from app.routes import templates
from app.routes import failed_jobs
from app.routes import audit
from app.services.auth_service import ensure_default_admin

app = FastAPI()

@app.on_event("startup")
def startup_event():
    ensure_default_admin()
    start_background_thread()

app.include_router(dashboard.router)
app.include_router(pages.router)
app.include_router(auth.router)
app.include_router(jira_options.router)
app.include_router(rules.router)
app.include_router(rule_logs.router)
app.include_router(templates.router)
app.include_router(failed_jobs.router)
app.include_router(audit.router)

# Mount static files
app.mount("/static", StaticFiles(directory="/frontend/static"), name="static")

templates = Jinja2Templates(directory="/frontend/templates")

@app.get("/")
def home():
    return {"message": "Mail to Jira Automation Running"}
