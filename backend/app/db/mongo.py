from pymongo import MongoClient
import os

MONGO_URI = os.getenv("MONGO_URI", "mongodb://mongo:27017")

client = MongoClient(MONGO_URI)
db = client["mail_jira_db"]

# Collections
emails_collection = db["emails"]
failed_jobs_collection = db["failed_jobs"]
users_collection = db["users"]
audit_logs_collection = db["audit_logs"]
mailboxes_collection = db["mailboxes"]

existing_indexes = emails_collection.index_information()
emails_collection.create_index("internal_id", unique=True)
if "message_id_1" not in existing_indexes:
    emails_collection.create_index(
        "message_id",
        unique=True,
        sparse=True
    )

users_collection.create_index("username", unique=True)
audit_logs_collection.create_index("timestamp")
audit_logs_collection.create_index("actor")
audit_logs_collection.create_index("action")
mailboxes_collection.create_index("email", unique=True)
mailboxes_collection.create_index("enabled")
