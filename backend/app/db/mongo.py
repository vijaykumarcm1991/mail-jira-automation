from pymongo import MongoClient
import os

MONGO_URI = os.getenv("MONGO_URI", "mongodb://mongo:27017")

client = MongoClient(MONGO_URI)
db = client["mail_jira_db"]

# Collections
emails_collection = db["emails"]

emails_collection.create_index("internal_id", unique=True)