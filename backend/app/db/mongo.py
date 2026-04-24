from pymongo import MongoClient
from app.config.settings import MONGO_URI

client = MongoClient(MONGO_URI)
db = client["mail_jira_db"]