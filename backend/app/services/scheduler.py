import time
import threading
from app.services.mail_service import fetch_unseen_emails


def start_mail_listener():
    while True:
        try:
            print("Checking for new emails...")
            fetch_unseen_emails()
        except Exception as e:
            print("Error:", e)

        time.sleep(30)


def start_background_thread():
    thread = threading.Thread(target=start_mail_listener, daemon=True)
    thread.start()