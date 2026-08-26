import requests
from html.parser import HTMLParser
from app.config.settings import (
    JIRA_BASE_URL,
    JIRA_EMAIL,
    JIRA_API_TOKEN,
    JIRA_CUSTOM_FIELDS,
    TIMEZONE,
    JIRA_PROJECT_KEY,      # ✅ ADD THIS
    JIRA_ISSUE_TYPE        # ✅ ADD THIS
)
from app.db.mongo import failed_jobs_collection, emails_collection
from datetime import datetime
import re
import hashlib
from app.config.settings import JIRA_ONPREM_URL, JIRA_ONPREM_USER, JIRA_ONPREM_PASS


def _message_id_label(message_id):
    """Build a fixed, label-safe token from an email Message-ID. Message-IDs are
    long and contain characters Jira labels disallow, so we hash them."""
    digest = hashlib.sha1(str(message_id).encode("utf-8")).hexdigest()[:20]
    return f"mailjira-mid-{digest}"


def find_existing_jira_by_message_id(message_id):
    """Return the key of an existing Jira ticket tagged with this email's
    Message-ID, or None. Used only on retries to avoid creating a duplicate when
    a prior attempt created the ticket (HTTP 201) but the response was lost."""
    if not message_id:
        return None

    jql = f'project = "{JIRA_PROJECT_KEY}" AND labels = "{_message_id_label(message_id)}"'
    url = f"{JIRA_BASE_URL}/rest/api/3/search/jql"
    auth = (JIRA_EMAIL, JIRA_API_TOKEN)
    headers = {"Accept": "application/json"}
    params = {"jql": jql, "maxResults": 1, "fields": "key"}

    try:
        response = requests.get(url, headers=headers, auth=auth, params=params)
    except Exception as e:
        print("Jira idempotency search error:", str(e))
        return None

    if response.status_code != 200:
        print("Jira idempotency search failed:", response.status_code, response.text)
        return None

    issues = response.json().get("issues", [])
    if issues:
        return issues[0].get("key")
    return None


def persist_jira_id(internal_id, jira_id):
    """Write a freshly created jira_id back onto the email document so the
    dashboard reflects it. Safe to call on every successful creation/retry."""
    if not internal_id or not jira_id:
        return
    emails_collection.update_one(
        {"internal_id": internal_id},
        {"$set": {"jira_id": jira_id, "status": "Open"}}
    )


class _HtmlToAdf(HTMLParser):
    """Convert an HTML string into an Atlassian Document Format (ADF) tree."""

    _MARKS = {
        'strong': 'strong', 'b': 'strong',
        'em': 'em',         'i': 'em',
        'code': 'code',
        'u': 'underline',
        's': 'strike', 'strike': 'strike', 'del': 'strike',
    }
    _HEADINGS = {'h1': 1, 'h2': 2, 'h3': 3, 'h4': 4, 'h5': 5, 'h6': 6}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self._doc = []
        self._para = None       # open block node being built
        self._marks = []        # stack of active inline marks
        self._lists = []        # stack of [list_node, current_list_item]
        self._in_pre = False

    # ── helpers ───────────────────────────────────────────────────────────────

    def _flush_para(self):
        if self._para is None:
            return
        if self._para.get("content"):
            self._push_block(self._para)
        self._para = None

    def _push_block(self, node):
        """Add a finished block node to the right container."""
        if self._lists:
            item = self._lists[-1][1]
            if item is not None:
                item["content"].append(node)
        else:
            self._doc.append(node)

    def _open_para(self, node_type="paragraph", **attrs):
        self._flush_para()
        self._para = {"type": node_type, "content": []}
        if attrs:
            self._para["attrs"] = attrs

    def _inline(self, node):
        if self._para is None:
            self._para = {"type": "paragraph", "content": []}
        self._para["content"].append(node)

    def _text_node(self, text):
        node = {"type": "text", "text": text}
        if self._marks:
            node["marks"] = [dict(m) for m in self._marks]
        return node

    # ── tag callbacks ─────────────────────────────────────────────────────────

    def handle_starttag(self, tag, attrs):
        attrs_d = dict(attrs)
        if tag == 'p':
            self._open_para()
        elif tag == 'div':
            self._flush_para()
        elif tag in self._HEADINGS:
            self._open_para("heading", level=self._HEADINGS[tag])
        elif tag == 'pre':
            self._flush_para()
            self._in_pre = True
            self._para = {"type": "codeBlock", "attrs": {}, "content": []}
        elif tag in ('ul', 'ol'):
            self._flush_para()
            ltype = "bulletList" if tag == 'ul' else "orderedList"
            self._lists.append([{"type": ltype, "content": []}, None])
        elif tag == 'li':
            self._flush_para()
            if self._lists:
                item = {"type": "listItem", "content": []}
                self._lists[-1][0]["content"].append(item)
                self._lists[-1][1] = item
        elif tag == 'br':
            self._inline({"type": "hardBreak"})
        elif tag == 'a':
            href = attrs_d.get('href', '').strip()
            if href and not href.startswith(('javascript:', '#')):
                self._marks.append({"type": "link", "attrs": {"href": href}})
        elif tag in self._MARKS:
            self._marks.append({"type": self._MARKS[tag]})

    def handle_endtag(self, tag):
        if tag in ('p', 'div', 'blockquote') or tag in self._HEADINGS:
            self._flush_para()
        elif tag == 'pre':
            self._in_pre = False
            self._flush_para()
        elif tag in ('ul', 'ol'):
            self._flush_para()
            if self._lists:
                list_node, _ = self._lists.pop()
                if list_node.get("content"):
                    self._push_block(list_node)
        elif tag == 'li':
            self._flush_para()
        elif tag == 'a':
            for i in range(len(self._marks) - 1, -1, -1):
                if self._marks[i].get("type") == "link":
                    self._marks.pop(i)
                    break
        elif tag in self._MARKS:
            mtype = self._MARKS[tag]
            for i in range(len(self._marks) - 1, -1, -1):
                if self._marks[i].get("type") == mtype:
                    self._marks.pop(i)
                    break

    def handle_data(self, data):
        if self._in_pre:
            if self._para is None:
                self._para = {"type": "codeBlock", "attrs": {}, "content": []}
            self._para["content"].append({"type": "text", "text": data})
            return
        # Collapse source whitespace the way a browser would
        text = re.sub(r'[\r\n\t]+', ' ', data)
        text = re.sub(r' {2,}', ' ', text)
        if text.strip() == '' and self._para is None:
            return
        if text:
            self._inline(self._text_node(text))

    def result(self):
        self._flush_para()
        while self._lists:
            list_node, _ = self._lists.pop()
            if list_node.get("content"):
                self._doc.append(list_node)
        return {
            "version": 1,
            "type": "doc",
            "content": self._doc or [{"type": "paragraph", "content": []}],
        }


def html_to_adf(html_text):
    """Convert an HTML string to an ADF document dict."""
    parser = _HtmlToAdf()
    try:
        parser.feed(html_text)
    except Exception:
        pass
    return parser.result()


def create_jira_ticket(data, rule_actions, attachments=None, from_retry=False):
    url = f"{JIRA_BASE_URL}/rest/api/3/issue"

    auth = (JIRA_EMAIL, JIRA_API_TOKEN)

    headers = {
        "Accept": "application/json",
        "Content-Type": "application/json"
    }

    message_id = data.get("message_id")

    # ✅ Retry idempotency: ONLY on a retry, check whether a previous attempt
    # already created this ticket (matched by the email's stable Message-ID) but
    # lost the response. A genuinely new email always has a fresh Message-ID, so
    # this never matches for new mail — a new mail always creates a new ticket.
    if from_retry and message_id:
        existing_key = find_existing_jira_by_message_id(message_id)
        if existing_key:
            print(f"Jira ticket already exists for message_id {message_id}: {existing_key}")
            return existing_key

    # ✅ Step 1: Base fields
    fields = {
        "project": {"key": JIRA_PROJECT_KEY},
        "summary": data.get("subject"),

        "description": data.get("description_adf") or {
            "type": "doc",
            "version": 1,
            "content": [
                {
                    "type": "paragraph",
                    "content": [
                        {
                            "text": data.get("description", ""),
                            "type": "text"
                        }
                    ]
                }
            ]
        },

        "issuetype": {
            "name": JIRA_ISSUE_TYPE
        },

        # ✅ FIXED SOURCE (your requirement)
        "customfield_10095": {"value": "EMAIL"},

        # ✅ NEW FIELD (Infra_App)
        "customfield_10099": {"value": "App"}
    }

    # ✅ Tag the ticket with the email's Message-ID so a later retry can detect
    # this ticket and avoid creating a duplicate.
    if message_id:
        fields["labels"] = [_message_id_label(message_id)]

    # ✅ Step 2: Apply rule-based fields

    if rule_actions.get("application"):
        fields["customfield_10085"] = {
            "value": rule_actions["application"]
        }

    if rule_actions.get("geography"):
        fields["customfield_10097"] = {
            "value": rule_actions["geography"]
        }

    if rule_actions.get("country"):
        fields["customfield_10091"] = {
            "value": rule_actions["country"]
        }

    if rule_actions.get("unit"):
        fields["customfield_10086"] = {
            "value": rule_actions["unit"]
        }
    
    # ✅ PRIORITY SUPPORT
    if rule_actions.get("priority"):
        fields["priority"] = {
            "name": rule_actions["priority"]
        }
    
    # ✅ Step 3: Final payload
    payload = {
        "fields": fields
    }

    # ✅ DEBUG LINE (ADD THIS HERE)
    print("Final Jira Fields:", fields)

    # ✅ Step 4: API call
    response = requests.post(url, json=payload, headers=headers, auth=auth)

    if response.status_code == 201:
        issue_key = response.json().get("key")

        # 🔥 UPLOAD ATTACHMENTS
        if attachments:
            upload_attachments(issue_key, attachments)

        return issue_key
    else:
        print("Jira Error:", response.text)

        # ✅ When called from a retry the scheduler already owns the existing
        # failed-job record, so don't insert a duplicate here.
        if not from_retry:
            # ✅ STORE FAILED JOB
            failed_jobs_collection.insert_one({
                "type": "jira",
                "payload": {
                    "data": data,
                    "rule_actions": rule_actions
                },
                "retry_count": 0,
                "status": "pending",
                "error": response.text,
                "created_at": datetime.utcnow()
            })

        return None
    
def _extract_adf_text(node):
    if isinstance(node, dict):
        text = node.get("text", "")
        child_text = [_extract_adf_text(child) for child in node.get("content", [])]
        return "\n".join(part for part in [text, *child_text] if part)

    if isinstance(node, list):
        return "\n".join(part for child in node for part in [_extract_adf_text(child)] if part)

    return ""


def _extract_comment_text(comment):
    body = comment.get("body", "")
    if isinstance(body, str):
        return body.strip()
    return _extract_adf_text(body).strip()


def _get_comment_property(comment_id, property_key):
    url = f"{JIRA_BASE_URL}/rest/api/3/comment/{comment_id}/properties/{property_key}"

    auth = (JIRA_EMAIL, JIRA_API_TOKEN)
    headers = {
        "Accept": "application/json"
    }

    response = requests.get(url, headers=headers, auth=auth)

    if response.status_code == 404:
        return None

    if response.status_code != 200:
        print(f"Failed to fetch comment property {property_key} for {comment_id}: {response.status_code} {response.text}")
        return None

    return response.json().get("value")


def _property_value_is_true(value):
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.lower() == "true"
    return False


def _is_platform_comment_customer_visible(comment):
    comment_id = comment.get("id")
    if not comment_id:
        return False

    visibility = _get_comment_property(comment_id, "sd.public.comment")
    if isinstance(visibility, dict):
        if isinstance(visibility.get("value"), dict):
            visibility = visibility["value"]
        if "internal" in visibility:
            return not _property_value_is_true(visibility.get("internal"))

    legacy_visibility = _get_comment_property(comment_id, "sd.allow.public.comment")
    if isinstance(legacy_visibility, dict):
        if "allow" in legacy_visibility:
            return _property_value_is_true(legacy_visibility.get("allow"))
        if "internal" in legacy_visibility:
            return not _property_value_is_true(legacy_visibility.get("internal"))

    return False


def get_latest_platform_customer_visible_comment(issue_key):
    url = f"{JIRA_BASE_URL}/rest/api/3/issue/{issue_key}/comment"

    auth = (JIRA_EMAIL, JIRA_API_TOKEN)
    headers = {
        "Accept": "application/json"
    }
    params = {
        "maxResults": 100,
        "orderBy": "-created"
    }

    response = requests.get(url, headers=headers, auth=auth, params=params)

    if response.status_code != 200:
        print(f"Failed to fetch Jira comments for {issue_key}: {response.status_code} {response.text}")
        return ""

    comments = sorted(
        response.json().get("comments", []),
        key=lambda comment: comment.get("created", ""),
        reverse=True
    )
    for comment in comments:
        if _is_platform_comment_customer_visible(comment):
            return _extract_comment_text(comment)

    return ""


def get_latest_customer_visible_comment(issue_key):
    """Return the latest JSM Cloud Reply to customer comment only."""
    url = f"{JIRA_BASE_URL}/rest/servicedeskapi/request/{issue_key}/comment"

    auth = (JIRA_EMAIL, JIRA_API_TOKEN)
    headers = {
        "Accept": "application/json"
    }
    params = {
        "public": "true",
        "internal": "false",
        "limit": 100
    }

    response = requests.get(url, headers=headers, auth=auth, params=params)

    if response.status_code != 200:
        print(f"Failed to fetch JSM public comments for {issue_key}: {response.status_code} {response.text}")
        return get_latest_platform_customer_visible_comment(issue_key)

    comments = response.json().get("values", [])
    public_comments = sorted([
        comment for comment in comments
        if comment.get("public") is not False
    ], key=lambda comment: comment.get("created", ""), reverse=True)

    if not public_comments:
        return ""

    return _extract_comment_text(public_comments[0])


def get_latest_comment(issue_key, include_internal=False):
    """Get the latest comment from a JSM ticket.

    Args:
        issue_key: The JSM ticket key (e.g., 'TICKET-123')
        include_internal: If False (default), only return customer-visible comments.
                         If True, include internal comments too.

    Returns:
        str: The latest comment text (Jira ADF format parsed), or "" if no comments
    """
    url = f"{JIRA_BASE_URL}/rest/api/3/issue/{issue_key}/comment?expand=properties"

    auth = (JIRA_EMAIL, JIRA_API_TOKEN)
    response = requests.get(url, auth=auth)

    if response.status_code != 200:
        return ""

    comments = response.json().get("comments", [])
    if not comments:
        return ""

    # Filter out internal comments if requested.
    if not include_internal:
        visible_comments = [
            c for c in comments
            if not any(prop.get("key") == "sd.public.comment" and prop.get("value", {}).get("internal") is True
                      for prop in c.get("properties", []))
        ]
        if not visible_comments:
            return ""
        comments = visible_comments

    latest = comments[-1]
    return _extract_comment_text(latest)


def get_attachments(issue_key, skip_files=None):
    url = f"{JIRA_BASE_URL}/rest/api/3/issue/{issue_key}"

    auth = (JIRA_EMAIL, JIRA_API_TOKEN)
    response = requests.get(url, auth=auth)

    if response.status_code != 200:
        return []

    attachments = response.json()["fields"].get("attachment", [])

    files = []
    for att in attachments:
        filename = att["filename"]

        # 🔥 SKIP EMAIL FILES
        if skip_files and filename in skip_files:
            continue

        file_resp = requests.get(att["content"], auth=auth)
        if file_resp.status_code == 200:
            files.append((filename, file_resp.content))

    return files


def add_comment_to_jira(issue_key, comment, is_customer_visible=True, body_adf=None):
    """Add a comment to a JSM ticket with visibility control.

    Args:
        issue_key: The JSM ticket key (e.g., 'TICKET-123')
        comment: Fallback plain-text comment (used when body_adf is None)
        is_customer_visible: If True, comment is visible to customers and may trigger notifications.
                           If False, comment is internal (internal note) and won't trigger notifications.
        body_adf: Optional pre-built ADF document dict (used instead of plain-text comment when set)

    Returns:
        bool: True if comment was successfully added, False otherwise
    """
    url = f"{JIRA_BASE_URL}/rest/api/3/issue/{issue_key}/comment"

    auth = (JIRA_EMAIL, JIRA_API_TOKEN)

    headers = {
        "Accept": "application/json",
        "Content-Type": "application/json"
    }

    payload = {
        "body": body_adf or {
            "type": "doc",
            "version": 1,
            "content": [
                {
                    "type": "paragraph",
                    "content": [
                        {
                            "type": "text",
                            "text": comment
                        }
                    ]
                }
            ]
        }
    }

    # Add visibility control for JSM internal notes
    if not is_customer_visible:
        payload["properties"] = [
            {
                "key": "sd.public.comment",
                "value": {"internal": True}
            }
        ]

    response = requests.post(url, json=payload, headers=headers, auth=auth)

    if response.status_code == 201:
        return True
    else:
        print(f"Failed to add comment to {issue_key}: {response.text}")
        return False

def get_l3_ticket_from_jsm(issue_key):
    url = f"{JIRA_BASE_URL}/rest/api/3/issue/{issue_key}"

    auth = (JIRA_EMAIL, JIRA_API_TOKEN)
    response = requests.get(url, auth=auth)

    if response.status_code != 200:
        return None

    # ✅ ONLY USE REMOTE LINKS (CORRECT)
    url = f"{JIRA_BASE_URL}/rest/api/3/issue/{issue_key}/remotelink"
    response = requests.get(url, auth=auth)

    if response.status_code != 200:
        return None

    links = response.json()

    for link in links:
        url = link.get("object", {}).get("url", "")
        match = re.search(r'[A-Z]+-\d+', url)
        if match:
            return match.group(0)

    return None

def fetch_l3_status(issue_key):

    url = f"{JIRA_ONPREM_URL}/rest/api/2/issue/{issue_key}"

    response = requests.get(url, auth=(JIRA_ONPREM_USER, JIRA_ONPREM_PASS))

    if response.status_code != 200:
        return None

    return response.json()["fields"]["status"]["name"]

def get_l3_comment(issue_key):

    url = f"{JIRA_ONPREM_URL}/rest/api/2/issue/{issue_key}/comment"

    response = requests.get(url, auth=(JIRA_ONPREM_USER, JIRA_ONPREM_PASS))

    if response.status_code != 200:
        return ""

    comments = response.json().get("comments", [])
    if not comments:
        return ""

    return comments[-1].get("body", "")

def get_l3_attachments(issue_key):

    url = f"{JIRA_ONPREM_URL}/rest/api/2/issue/{issue_key}"

    response = requests.get(url, auth=(JIRA_ONPREM_USER, JIRA_ONPREM_PASS))

    if response.status_code != 200:
        return []

    attachments = response.json()["fields"].get("attachment", [])

    files = []
    for att in attachments:
        file_resp = requests.get(att["content"], auth=(JIRA_ONPREM_USER, JIRA_ONPREM_PASS))
        if file_resp.status_code == 200:
            files.append((att["filename"], file_resp.content))

    return files

def upload_attachments(issue_key, attachments):
    url = f"{JIRA_BASE_URL}/rest/api/3/issue/{issue_key}/attachments"

    auth = (JIRA_EMAIL, JIRA_API_TOKEN)

    headers = {
        "X-Atlassian-Token": "no-check"
    }

    files = []
    for filename, content in attachments:
        files.append((
            "file",
            (
                filename,
                content,
                "application/octet-stream",
                {"X-Source": "email"}  # 🔥 TAG
            )
        ))

    response = requests.post(url, headers=headers, files=files, auth=auth)

    if response.status_code not in [200, 201]:
        print(f"Attachment upload failed for {issue_key}:", response.text)
