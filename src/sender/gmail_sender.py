"""
Gmail sending via POST /gmail/v1/users/me/messages/send.

Constructs a raw RFC 2822 MIME message with RFC 2047-encoded (UTF-8)
subject, base64url-encodes it, and sends via the googleapiclient
Gmail resource -- mirroring the logic in the original maillaunch.html.
"""
from __future__ import annotations

import base64
from email.header import Header
from email.mime.text import MIMEText
from email.utils import formataddr

from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError


class AuthExpiredError(Exception):
    """Raised on HTTP 401 -- caller should refresh and retry once."""


class SendError(Exception):
    def __init__(self, message: str, status_code: int | None = None):
        super().__init__(message)
        self.status_code = status_code


def _build_message(to_email: str, subject: str, body: str, from_email: str | None) -> dict:
    mime_msg = MIMEText(body, "plain", "utf-8")
    mime_msg["to"] = to_email
    mime_msg["subject"] = str(Header(subject, "utf-8"))  # RFC 2047 UTF-8 subject
    if from_email:
        mime_msg["from"] = formataddr(("", from_email))

    raw = base64.urlsafe_b64encode(mime_msg.as_bytes()).decode("utf-8")
    return {"raw": raw}


def send(creds: Credentials, to_email: str, subject: str, body: str) -> str:
    """Send one email. Returns the Gmail message ID on success.

    Raises AuthExpiredError on 401 (caller refreshes token and
    retries), or SendError for any other failure (caller applies
    retry/backoff per the campaign's retry policy).
    """
    service = build("gmail", "v1", credentials=creds, cache_discovery=False)
    message = _build_message(to_email, subject, body, from_email=None)

    try:
        result = service.users().messages().send(userId="me", body=message).execute()
        return result.get("id", "")
    except HttpError as e:
        status = getattr(e.resp, "status", None)
        if status == 401:
            raise AuthExpiredError(str(e)) from e
        raise SendError(f"HTTP {status} - {e}", status_code=status) from e
