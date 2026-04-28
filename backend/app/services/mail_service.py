import imaplib
import email
from email.header import decode_header
from datetime import datetime
import re
import pytz
from app.services.jira_service import create_jira_ticket
from app.db.mongo import emails_collection
from app.utils.helpers import generate_internal_id
from app.models.email_model import create_email_doc
from app.config.settings import (
    EMAIL_ACCOUNT,
    EMAIL_PASSWORD,
    IMAP_SERVER,
    TIMEZONE
)
from app.services.rule_engine import apply_rules
import smtplib
from email.mime.text import MIMEText
from app.config.settings import SMTP_HOST, SMTP_PORT, SMTP_USER, SMTP_PASS
from app.db.mongo import db
from email.mime.multipart import MIMEMultipart
from email.mime.base import MIMEBase
from email import encoders
from app.db.mongo import failed_jobs_collection
import re
import uuid

IST = pytz.timezone(TIMEZONE)


def clean_text(text):
    return text.strip() if text else ""

def normalize_msg_id(mid):
    if not mid:
        return None
    return mid.strip().replace("<", "").replace(">", "")

def fetch_unseen_emails():
    mail = imaplib.IMAP4_SSL(IMAP_SERVER)
    mail.login(EMAIL_ACCOUNT, EMAIL_PASSWORD)
    mail.select("inbox")

    status, messages = mail.search(None, '(UNSEEN)')
    email_ids = messages[0].split()

    for e_id in email_ids:
        _, msg_data = mail.fetch(e_id, "(RFC822)")
        # ✅ Mark as seen AFTER fetching
        mail.store(e_id, '+FLAGS', '\\Seen')
        raw_email = msg_data[0][1]

        msg = email.message_from_bytes(raw_email)

        internal_id = generate_internal_id()

        message_id = normalize_msg_id(msg.get("Message-ID"))
        in_reply_to = msg.get("In-Reply-To")
        references = msg.get("References")
        
        # ✅ STRONG fallback
        if not message_id:
            message_id = f"generated-{uuid.uuid4()}"

        # ✅ Prevent duplicates
        if emails_collection.find_one({"message_id": message_id}):
            continue

        subject, encoding = decode_header(msg["Subject"])[0]
        if isinstance(subject, bytes):
            subject = subject.decode(encoding or "utf-8")

        is_reply = subject.lower().startswith("re:")

        from_email = msg.get("From")
        cc = msg.get("Cc")

        body = ""

        if msg.is_multipart():
            for part in msg.walk():
                if part.get_content_type() == "text/plain":
                    body = part.get_payload(decode=True).decode()
                    break
        else:
            body = msg.get_payload(decode=True).decode()

        data = {
            "internal_id": internal_id,
            "subject": clean_text(subject),
            "from": from_email,
            "cc": cc.split(",") if cc else [],
            "jira_id": None,
            "status": "New",
            "description": clean_text(body),
            "message_id": message_id,
            "created_at": datetime.now(IST)
        }

        parent_message_id = None
        existing_ticket = None

        if in_reply_to:
            parent_message_id = normalize_msg_id(in_reply_to)
        elif references:
            parent_message_id = normalize_msg_id(references.split()[-1])

        print("Incoming Message-ID:", message_id)
        print("In-Reply-To:", parent_message_id)

        if parent_message_id:
            existing_ticket = emails_collection.find_one({
                "$or": [
                    {"message_id": parent_message_id},
                    {"system_message_id": parent_message_id}
                ]
            })
            print("Matched Ticket:", existing_ticket)

            if existing_ticket and existing_ticket.get("jira_id"):
                from app.services.jira_service import add_comment_to_jira

                add_comment_to_jira(
                    existing_ticket["jira_id"],
                    body
                )

                print(f"Added comment to {existing_ticket['jira_id']} via message-id")

                # ✅ STORE REPLY EMAIL
                doc = create_email_doc({
                    **data,
                    "jira_id": existing_ticket["jira_id"],
                    "status": "Comment Added"
                })
                try:
                    emails_collection.insert_one(doc)
                except Exception as e:
                    print("DB insert skipped (reply):", str(e))

                continue  # ❗ STOP NEW TICKET
        
        # ✅ BLOCK reply cases
        if parent_message_id and not existing_ticket:
            print("Reply detected but no matching ticket → ignoring")
            continue

        # ✅ Apply rules
        rule_actions = apply_rules(data)

        # ✅ Pass to Jira
        jira_id = create_jira_ticket(data, rule_actions)

        if jira_id:
            base_subject = re.sub(r'^(re:\s*)+', '', data.get("subject"), flags=re.IGNORECASE)
            subject = f"Re: {base_subject}"

            template = db["email_templates"].find_one({"type": "create"})
            body = template["body"].replace("{{jira_id}}", jira_id)

            sent_msg_id = send_email(
                to_list=[from_email],
                cc_list=data.get("cc", []),
                subject=subject,
                body=body,
                message_id=message_id
            )

            # ✅ DO NOT overwrite original message_id
            data["system_message_id"] = normalize_msg_id(sent_msg_id)

        data["jira_id"] = jira_id
        data["status"] = "Open" if jira_id else "Failed"

        doc = create_email_doc(data)
        try:
            emails_collection.insert_one(doc)
        except Exception as e:
            print("DB insert skipped (new ticket):", str(e))

        # ✅ 3. mark as seen
        mail.store(e_id, '+FLAGS', '\\Seen')

    mail.logout()

def send_email(to_list, subject, body, cc_list=None, attachments=None, message_id=None):

    msg = MIMEMultipart()
    msg["Subject"] = subject
    msg["From"] = EMAIL_ACCOUNT
    msg["To"] = ", ".join(to_list)

    if message_id:
        msg["In-Reply-To"] = message_id
        msg["References"] = message_id

    if cc_list:
        msg["Cc"] = ", ".join(cc_list)
        recipients = to_list + cc_list
    else:
        recipients = to_list

    msg.attach(MIMEText(body, "plain"))

    # ✅ Attach files
    if attachments:
        for filename, content in attachments:
            part = MIMEBase("application", "octet-stream")
            part.set_payload(content)
            encoders.encode_base64(part)
            part.add_header("Content-Disposition", f"attachment; filename={filename}")
            msg.attach(part)

    try:
        with smtplib.SMTP_SSL(SMTP_HOST, SMTP_PORT) as server:
            server.login(SMTP_USER, SMTP_PASS)
            # ✅ Generate message-id manually BEFORE sending
            generated_msg_id = f"<{uuid.uuid4()}@mail-jira.local>"
            msg["Message-ID"] = generated_msg_id
            server.sendmail(EMAIL_ACCOUNT, recipients, msg.as_string())
            return generated_msg_id

    except Exception as e:
        print("Email Error:", str(e))

        # ✅ STORE FAILED EMAIL
        failed_jobs_collection.insert_one({
            "type": "email",
            "payload": {
                "to_list": to_list,
                "cc_list": cc_list,
                "subject": subject,
                "body": body
            },
            "retry_count": 0,
            "status": "pending",
            "error": str(e),
            "created_at": datetime.now(IST)
        })