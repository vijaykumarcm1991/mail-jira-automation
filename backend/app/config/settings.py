import os
from dotenv import load_dotenv

load_dotenv()

MONGO_URI = os.getenv("MONGO_URI")

EMAIL_ACCOUNT = os.getenv("EMAIL_ACCOUNT")
EMAIL_PASSWORD = os.getenv("EMAIL_PASSWORD")
IMAP_SERVER = os.getenv("IMAP_SERVER")

TIMEZONE = os.getenv("TIMEZONE", "Asia/Kolkata")