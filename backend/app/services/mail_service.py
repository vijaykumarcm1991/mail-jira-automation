import imaplib
import email
from email.header import decode_header
from datetime import datetime
import re
from jinja2 import Template
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
import uuid
from app.services.mailbox_service import env_mailbox, get_default_outbound_mailbox

IST = pytz.timezone(TIMEZONE)


def clean_text(text):
    return text.strip() if text else ""

def normalize_msg_id(mid):
    if not mid:
        return None
    return mid.strip().replace("<", "").replace(">", "")

def fetch_unseen_emails(mailbox=None):
    mailbox = mailbox or env_mailbox()
    if not mailbox:
        print("No mailbox configured")
        return

    mail = imaplib.IMAP4_SSL(mailbox["imap_server"])
    mail.login(mailbox["email"], mailbox["password"])
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
        attachments = []  # 🔥 NEW

        if msg.is_multipart():
            for part in msg.walk():
                content_type = part.get_content_type()
                content_disposition = str(part.get("Content-Disposition"))

                # ✅ BODY
                if content_type == "text/plain" and "attachment" not in content_disposition:
                    body = part.get_payload(decode=True).decode()

                # ✅ ATTACHMENTS
                if "attachment" in content_disposition:
                    filename = part.get_filename()
                    if filename:
                        file_data = part.get_payload(decode=True)
                        attachments.append((filename, file_data))
        else:
            body = msg.get_payload(decode=True).decode()

        data = {
            "internal_id": internal_id,
            "subject": clean_text(subject),
            "from": from_email,
            "cc": cc.split(",") if cc else [],
            "jira_id": None,
            "mailbox_id": str(mailbox.get("_id")) if mailbox.get("_id") else None,
            "mailbox_email": mailbox.get("email"),
            "status": "New",
            "description": clean_text(body),
            "message_id": message_id,
            "email_attachments": [name for name, _ in attachments],  # 🔥 NEW
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
        jira_id = create_jira_ticket(data, rule_actions, attachments)

        if jira_id:
            base_subject = re.sub(r'^(re:\s*)+', '', data.get("subject"), flags=re.IGNORECASE)
            subject = f"Re: {base_subject}"

            template = db["email_templates"].find_one({"type": "create"})
            context = {
                "jira_id": jira_id
            }

            body = Template(template["body"]).render(**context)

            sent_msg_id = send_email(
                to_list=[from_email],
                cc_list=data.get("cc", []),
                subject=subject,
                body=body,
                message_id=message_id,
                mailbox=mailbox
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

def send_email(to_list, subject, body, cc_list=None, attachments=None, message_id=None, mailbox=None, metadata=None):
    mailbox = mailbox or get_default_outbound_mailbox()
    from_email = mailbox.get("email") if mailbox else EMAIL_ACCOUNT
    smtp_host = mailbox.get("smtp_host") if mailbox else SMTP_HOST
    smtp_port = mailbox.get("smtp_port") if mailbox else SMTP_PORT
    smtp_user = mailbox.get("smtp_user") if mailbox else SMTP_USER
    smtp_password = mailbox.get("smtp_password") if mailbox else SMTP_PASS

    try:
        if not smtp_host or not smtp_port or not smtp_user or not smtp_password:
            raise ValueError("SMTP host, port, user, and password are required")

        recipients = list(to_list or []) + list(cc_list or [])
        if not recipients:
            raise ValueError("At least one email recipient is required")

        msg = MIMEMultipart()
        msg["Subject"] = subject
        msg["From"] = from_email
        msg["To"] = ", ".join(to_list or [])

        if message_id:
            msg["In-Reply-To"] = message_id
            msg["References"] = message_id

        if cc_list:
            msg["Cc"] = ", ".join(cc_list)

        msg.attach(MIMEText(body, "plain"))

        # ✅ Attach files
        if attachments:
            for filename, content in attachments:
                part = MIMEBase("application", "octet-stream")
                part.set_payload(content)
                encoders.encode_base64(part)
                part.add_header("Content-Disposition", f"attachment; filename={filename}")
                msg.attach(part)

        smtp_port = int(smtp_port)
        if smtp_port == 465:
            server = smtplib.SMTP_SSL(smtp_host, smtp_port, timeout=30)
        else:
            server = smtplib.SMTP(smtp_host, smtp_port, timeout=30)

        with server:
            if smtp_port != 465:
                server.ehlo()
                server.starttls()
                server.ehlo()
            server.login(smtp_user, smtp_password)
            # ✅ Generate message-id manually BEFORE sending
            generated_msg_id = f"<{uuid.uuid4()}@mail-jira.local>"
            msg["Message-ID"] = generated_msg_id
            server.sendmail(from_email, recipients, msg.as_string())
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
                "body": body,
                "mailbox_id": str(mailbox.get("_id")) if mailbox else None,
                "mailbox_email": mailbox.get("email") if mailbox else None,
                "metadata": metadata or {}
            },
            "retry_count": 0,
            "status": "pending",
            "error": str(e),
            "created_at": datetime.now(IST)
        })
