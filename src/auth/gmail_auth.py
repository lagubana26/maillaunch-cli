"""
Gmail OAuth 2.0 (installed-app flow) authentication.

Tokens are stored via token_store (OS keychain, encrypted-file
fallback) -- never in plaintext, never in the config file.
"""
from __future__ import annotations

import getpass
from typing import Optional

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow

from . import token_store

SCOPES = ["https://www.googleapis.com/auth/gmail.send"]
ACCOUNT_KEY = "gmail:credentials"
CLIENT_SECRET_KEY = "gmail:client_secret"


def _client_config(client_id: str, client_secret: str) -> dict:
    return {
        "installed": {
            "client_id": client_id,
            "client_secret": client_secret,
            "auth_uri": "https://accounts.google.com/o/oauth2/auth",
            "token_uri": "https://oauth2.googleapis.com/token",
            "redirect_uris": ["http://localhost"],
        }
    }


def interactive_login(client_id: str, client_secret: Optional[str] = None) -> Credentials:
    """Run the OAuth consent flow in a local browser and persist tokens."""
    if not client_secret:
        client_secret = getpass.getpass(
            "Gmail OAuth client secret (input hidden, stored in OS "
            "keychain, not the config file): "
        )
    token_store.set_secret(CLIENT_SECRET_KEY, client_secret)

    flow = InstalledAppFlow.from_client_config(
        _client_config(client_id, client_secret), SCOPES
    )
    creds = flow.run_local_server(port=0)
    _save_credentials(creds)
    return creds


def _save_credentials(creds: Credentials) -> None:
    token_store.set_secret(
        ACCOUNT_KEY,
        {
            "token": creds.token,
            "refresh_token": creds.refresh_token,
            "token_uri": creds.token_uri,
            "client_id": creds.client_id,
            "client_secret": creds.client_secret,
            "scopes": creds.scopes,
        },
    )


def load_credentials() -> Optional[Credentials]:
    """Load stored credentials, refreshing the access token if expired.

    Returns None if no credentials are stored yet, or refresh fails
    (in which case the caller should prompt the user to re-run
    `maillaunch auth gmail`).
    """
    data = token_store.get_secret(ACCOUNT_KEY)
    if not data:
        return None

    creds = Credentials(
        token=data.get("token"),
        refresh_token=data.get("refresh_token"),
        token_uri=data.get("token_uri"),
        client_id=data.get("client_id"),
        client_secret=data.get("client_secret"),
        scopes=data.get("scopes"),
    )

    if not creds.valid:
        if creds.expired and creds.refresh_token:
            try:
                creds.refresh(Request())
                _save_credentials(creds)
            except Exception:
                return None
        else:
            return None

    return creds


def refresh_or_none(creds: Credentials) -> Optional[Credentials]:
    """Explicit refresh entry point used by the sender on a 401."""
    try:
        creds.refresh(Request())
        _save_credentials(creds)
        return creds
    except Exception:
        return None
