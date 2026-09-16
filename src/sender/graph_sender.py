"""
Microsoft Graph sending via POST /graph.microsoft.com/v1.0/me/sendMail.

No raw MIME needed -- Graph takes a JSON message payload directly.
"""
from __future__ import annotations

import requests

from .gmail_sender import AuthExpiredError, SendError  # reuse the same exception types

GRAPH_SEND_URL = "https://graph.microsoft.com/v1.0/me/sendMail"


def send(access_token: str, to_email: str, subject: str, body: str) -> None:
    """Send one email via Microsoft Graph.

    Raises AuthExpiredError on 401 (caller refreshes token and
    retries), or SendError for any other failure.
    """
    payload = {
        "message": {
            "subject": subject,
            "body": {"contentType": "Text", "content": body},
            "toRecipients": [{"emailAddress": {"address": to_email}}],
        },
        "saveToSentItems": "true",
    }
    headers = {
        "Authorization": f"Bearer {access_token}",
        "Content-Type": "application/json",
    }

    resp = requests.post(GRAPH_SEND_URL, json=payload, headers=headers, timeout=30)

    if resp.status_code == 202:  # Graph returns 202 Accepted with no body on success
        return
    if resp.status_code == 401:
        raise AuthExpiredError(resp.text)
    raise SendError(f"HTTP {resp.status_code} - {resp.text}", status_code=resp.status_code)
