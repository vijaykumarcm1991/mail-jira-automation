from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from app.routes import dashboard
from app.routes import pages
from starlette.middleware.sessions import SessionMiddleware
from app.routes import auth
from app.services.scheduler import start_background_thread
from app.routes import jira_options

app = FastAPI()

@app.on_event("startup")
def startup_event():
    start_background_thread()

app.include_router(dashboard.router)
app.include_router(pages.router)
app.include_router(auth.router)
app.include_router(jira_options.router)

app.add_middleware(SessionMiddleware, secret_key="1893557437c3772417d7ccc83de6b11dc033d13ff7543e1e1c965a59bf614270")

# Mount static files
app.mount("/static", StaticFiles(directory="/frontend/static"), name="static")

templates = Jinja2Templates(directory="/frontend/templates")

@app.get("/")
def home():
    return {"message": "Mail to Jira Automation Running"}