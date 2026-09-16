"""
Microsoft 365 / Graph authentication using MSAL's device-code flow.

Device code flow is used (rather than a local-redirect browser flow)
because it works headlessly over SSH and needs no redirect URI
registration beyond the default public-client setup -- a good fit for
a CLI tool.
"""
from __future__ import annotations

import time
from typing import Optional

import msal

from . import token_store

AUTHORITY = "https://login.microsoftonline.com/common"
SCOPES = ["Mail.Send"]
ACCOUNT_KEY = "microsoft:msal_cache"
CLIENT_ID_KEY = "microsoft:client_id"


def _load_cache() -> msal.SerializableTokenCache:
    cache = msal.SerializableTokenCache()
    raw = token_store.get_secret(ACCOUNT_KEY)
    if raw:
        cache.deserialize(raw)
    return cache


def _save_cache(cache: msal.SerializableTokenCache) -> None:
    if cache.has_state_changed:
        token_store.set_secret(ACCOUNT_KEY, cache.serialize())


def _app(client_id: str, cache: msal.SerializableTokenCache) -> msal.PublicClientApplication:
    return msal.PublicClientApplication(
        client_id=client_id, authority=AUTHORITY, token_cache=cache
    )


def interactive_login(client_id: str) -> dict:
    """Run the device-code flow, printing a code for the user to enter
    at https://microsoft.com/devicelogin, and persist the token cache."""
    token_store.set_secret(CLIENT_ID_KEY, client_id)
    cache = _load_cache()
    app = _app(client_id, cache)

    flow = app.initiate_device_flow(scopes=SCOPES)
    if "user_code" not in flow:
        raise RuntimeError(f"Failed to start device flow: {flow}")

    print(flow["message"])  # Instructs user to visit URL and enter code
    result = app.acquire_token_by_device_flow(flow)  # blocks until user completes it

    if "access_token" not in result:
        raise RuntimeError(
            f"Microsoft auth failed: {result.get('error_description', result)}"
        )

    _save_cache(cache)
    return result


def load_access_token() -> Optional[str]:
    """Return a valid access token, silently refreshing via the MSAL
    cache if needed. Returns None if the user needs to re-auth."""
    client_id = token_store.get_secret(CLIENT_ID_KEY)
    if not client_id:
        return None

    cache = _load_cache()
    app = _app(client_id, cache)
    accounts = app.get_accounts()
    if not accounts:
        return None

    result = app.acquire_token_silent(SCOPES, account=accounts[0])
    _save_cache(cache)

    if not result or "access_token" not in result:
        return None
    return result["access_token"]
