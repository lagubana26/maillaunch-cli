# MailLaunch CLI

A command-line cold-email sender for Gmail and Microsoft 365, rebuilding
`maillaunch.html` as a proper developer tool: persistent OAuth tokens,
resumable campaigns, structured logging, and retry logic.

## 1. Setup — from zero to first send

```bash
git clone <this-repo>
cd maillaunch
python3 -m venv venv
source venv/bin/activate          # Windows: venv\Scripts\activate
pip install -r requirements.txt
```

### Configure a provider

Copy the example config (or edit it in place) and fill in your OAuth
`client_id`. **Never put a client secret or token in this file** — see
[Credential handling](#credential-handling) below.

```bash
cp maillaunch.config.yaml.example maillaunch.config.yaml   # if you keep a template
```

`maillaunch.config.yaml`:

```yaml
provider: gmail
daily_limit: 100
gmail:
  client_id: "your-client-id.apps.googleusercontent.com"
microsoft:
  client_id: "xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx"
send:
  min_delay_seconds: 60
  max_delay_seconds: 180
  retry_failed: true
  retry_attempts: 2
```

**Gmail:** create an OAuth client (type "Desktop app") in Google Cloud
Console, enable the Gmail API, and add the `gmail.send` scope.

**Microsoft:** register a public-client app in Azure AD (no redirect
URI needed for the device-code flow used here), and grant it the
`Mail.Send` delegated permission.

### Authenticate

```bash
python -m src.main auth gmail        # opens a browser for consent
python -m src.main auth microsoft    # prints a device code to enter at microsoft.com/devicelogin
```

Tokens are stored in your OS keychain (or an encrypted local file if
no keychain is available) — never in plaintext, never in the repo.

### Send a campaign

```bash
python -m src.main send \
  --provider gmail \
  --csv recipients.csv \
  --subject "Hi {{name}}, quick note about {{company}}" \
  --body-file template.txt \
  --min-delay 60 \
  --max-delay 180
```

`recipients.csv` needs a column containing "email" or "mail" in its
name (auto-detected, or pass `--email-column` explicitly). Any other
column can be referenced in the subject or body with `{{column_name}}`.

### Other commands

```bash
python -m src.main status           # today's send count + current campaign queue
python -m src.main resume           # resume the most recent interrupted campaign
python -m src.main resume <id>      # resume a specific campaign by ID
python -m src.main log              # print today's send log
python -m src.main log --date 2026-09-15
```

If you install the package (`pip install -e .`), all of the above work
as `maillaunch <command>` instead of `python -m src.main <command>`.

## 2. How to run tests

```bash
pip install -r requirements.txt   # pytest is included
pytest tests/ -v
```

34 tests cover CSV parsing (BOM, quoted commas, empty rows, missing
email column), template rendering ({{variable}} substitution, missing
keys, nested braces), email-column auto-detection, daily-limit
enforcement (including across multiple campaigns on the same day), and
retry/backoff logic (including auth-refresh-then-retry on 401), all
against mocked send functions — no network or real credentials needed
to run the suite.

## 3. Design decisions

**Language: Python 3.10+.** The original tool's core logic (CSV
parsing, MIME construction, Gmail/Graph calls) maps directly onto
mature, well-documented libraries (`google-api-python-client`, `msal`,
`requests`), and `click` gives a clean CLI structure with minimal
boilerplate. Node would have worked equally well; Python was chosen
mainly for `msal`'s first-class device-code flow support and
familiarity with the Gmail client library's error surface.

**Token storage.** `keyring` is the primary backend — OS Keychain on
macOS, Secret Service (gnome-keyring/kwallet) on Linux — so tokens
never touch disk in plaintext. On a headless box with no keychain
service running, `token_store.py` falls back to a `cryptography.Fernet`
-encrypted file under `~/.maillaunch/`, with the encryption key itself
stored in the keychain when possible, or a 0600 key file otherwise.
Client secrets follow the same path and are explicitly rejected if
found in the YAML config file (`config.py` raises rather than silently
loading them).

**State: SQLite over a flat JSON file.** A campaign can have thousands
of recipients; SQLite gives cheap indexed lookups for "give me all
PENDING rows" and atomic per-row status updates, which a hand-rolled
JSON file would need to reimplement (and get wrong under a crash
mid-write). The daily-limit count is derived from `SENT` rows'
timestamps rather than a separate counter, so it's automatically
correct across multiple campaigns run the same day and survives
process restarts.

**Resume semantics.** Every recipient row has an explicit status
(`PENDING`/`SENT`/`FAILED`/`SKIPPED`). `resume` just re-queries for
`PENDING` rows in the same campaign — there's no separate "resume log"
to keep in sync with the main state, which was a source of bugs I
wanted to avoid.

**Retry/backoff.** Exponential backoff (`min_delay * 2^(attempt-1)`)
on non-auth errors, up to `retry_attempts` extra tries. A `401` is
handled distinctly: it triggers a token refresh (Gmail: refresh_token
grant; Microsoft: MSAL's silent-token cache) and retries once without
consuming a retry-budget slot, since it's not a transient send failure
— aborting and telling the user to re-auth only happens if the refresh
itself fails.

**Provider abstraction.** `sender/engine.py` is provider-agnostic: it
takes an injected `send_fn(to, subject, body)` closure, so the same
retry/backoff/delay/logging loop drives both Gmail and Graph, and unit
tests can inject a mock without touching real network code or OAuth.

**Trade-offs / things kept intentionally simple:**
- No queueing system (Celery, etc.) — a single-process loop with
  SQLite state is sufficient for a CLI tool sending well under
  `daily_limit` emails, and it's dramatically easier to reason about
  and resume correctly.
- No abstract "Provider" base class/plugin system for two providers —
  a `if provider == "gmail" / "microsoft"` dispatch in `main.py` is
  more readable at this scale than a formal interface would be.
- HTML email bodies aren't supported (plain text only), matching what
  the assignment's reference material describes.

## 4. What I'd improve with more time

- **HTML + plaintext multipart bodies**, with a `--body-html` option.
- **Per-recipient send-time windows** (e.g. avoid sending outside
  9am–5pm recipient-local-time) rather than just an inter-send delay.
- **A dry-run mode** (`--dry-run`) that renders and prints every
  message without sending, for template sanity-checking before a real
  campaign.
- **Bounce/complaint handling** — currently a hard failure (4xx/5xx) is
  just retried and eventually marked FAILED; distinguishing a
  permanent bounce from a transient rate limit would avoid wasted
  retries.
- **Integration tests behind a `--live` flag** that hit real sandboxed
  Gmail/Graph test accounts, complementing the current fully-mocked
  unit suite.
- **Windows support** — the keychain fallback path and file
  permissions (`os.chmod`) are POSIX-oriented; Windows Credential
  Manager works via `keyring` but hasn't been tested end-to-end.

## Credential handling

| What | Where it lives |
|---|---|
| OAuth `client_id` | `maillaunch.config.yaml` (not sensitive) |
| OAuth `client_secret` (Gmail) | `MAILLAUNCH_GMAIL_CLIENT_SECRET` env var, or prompted once and stored in the OS keychain |
| Access/refresh tokens | OS keychain, or `~/.maillaunch/credentials.enc` (Fernet-encrypted, 0600) if no keychain is available |
| MSAL token cache (Microsoft) | Same as above, serialized via `msal.SerializableTokenCache` |

`config.py` actively refuses to load a config file that contains a
`client_secret` field, to catch accidental commits early.

## Project structure

```
maillaunch/
├── README.md
├── maillaunch.config.yaml   ← example config, no real credentials
├── requirements.txt
├── pyproject.toml
├── src/
│   ├── main.py               ← CLI entry point (click)
│   ├── config.py
│   ├── auth/
│   │   ├── token_store.py    ← keychain / encrypted-file credential storage
│   │   ├── gmail_auth.py     ← Gmail OAuth (installed-app flow)
│   │   └── microsoft_auth.py ← Microsoft OAuth (MSAL device-code flow)
│   ├── sender/
│   │   ├── gmail_sender.py   ← RFC 2822 MIME + Gmail API send
│   │   ├── graph_sender.py   ← Microsoft Graph sendMail
│   │   └── engine.py         ← delay / retry / daily-limit / logging orchestration
│   └── campaign/
│       ├── csv_parser.py     ← CSV parsing + email column detection
│       ├── template.py       ← {{variable}} rendering
│       ├── state.py          ← SQLite campaign/recipient persistence
│       └── send_log.py       ← structured send log writer
└── tests/
    ├── test_csv_parser.py
    ├── test_template.py
    ├── test_state.py
    └── test_engine.py
```
