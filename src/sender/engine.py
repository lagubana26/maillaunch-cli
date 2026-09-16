"""
The send engine: pulls PENDING recipients for a campaign, renders the
template per-recipient, applies a random inter-send delay, sends via
the configured provider, retries transient failures with exponential
backoff, refreshes auth on 401, enforces the daily send limit, and
records every outcome to both SQLite state and the structured log.

This is intentionally provider-agnostic: callers inject a `send_fn`
so the same loop drives both Gmail and Microsoft Graph, and so tests
can inject a mock without touching real network code.
"""
from __future__ import annotations

import random
import time
from dataclasses import dataclass
from typing import Callable, Optional

from ..campaign.send_log import SendLogger
from ..campaign.state import StateStore
from ..campaign.template import render


class DailyLimitReached(Exception):
    pass


@dataclass
class EngineConfig:
    daily_limit: int
    min_delay_seconds: int
    max_delay_seconds: int
    retry_attempts: int
    retry_failed: bool = True
    sleep_fn: Callable[[float], None] = time.sleep
    random_fn: Callable[[float, float], float] = random.uniform


class SendEngine:
    def __init__(
        self,
        state: StateStore,
        logger: SendLogger,
        config: EngineConfig,
        send_fn: Callable[[str, str, str], None],
        refresh_fn: Optional[Callable[[], bool]] = None,
    ):
        """
        send_fn(to_email, subject, body) -> None; raises AuthExpiredError
            or SendError (see sender.gmail_sender) on failure.
        refresh_fn() -> bool; attempts to refresh the auth token,
            returns True on success. If None or it returns False on a
            401, the campaign aborts and prompts re-auth.
        """
        self.state = state
        self.logger = logger
        self.config = config
        self.send_fn = send_fn
        self.refresh_fn = refresh_fn

    def run(self, campaign_id: str, subject_template: str, body_template: str) -> dict:
        from .gmail_sender import AuthExpiredError, SendError  # local import avoids
        # a hard dependency on google libs for callers that only use Graph.

        sent_today = self.state.sent_count_today()
        recipients = self.state.pending_recipients(campaign_id)

        for row in recipients:
            if sent_today >= self.config.daily_limit:
                self.logger.log(
                    "LIMIT_REACHED", row["email"], campaign_id, name=row["name"] or ""
                )
                break  # remaining recipients stay PENDING for `resume`

            import json

            data = json.loads(row["data_json"])
            subject = render(subject_template, data).text
            body = render(body_template, data).text

            ok = self._send_with_retry(
                campaign_id, row["row_index"], row["email"], row["name"] or "",
                subject, body, AuthExpiredError, SendError,
            )

            if ok:
                sent_today += 1
                # Random delay between sends, but not after the very
                # last recipient.
                delay = self.config.random_fn(
                    self.config.min_delay_seconds, self.config.max_delay_seconds
                )
                self.config.sleep_fn(delay)

        self._maybe_complete(campaign_id)
        return self.state.campaign_summary(campaign_id)

    def _send_with_retry(
        self,
        campaign_id: str,
        row_index: int,
        email: str,
        name: str,
        subject: str,
        body: str,
        AuthExpiredError,
        SendError,
    ) -> bool:
        attempts_allowed = 1 + (self.config.retry_attempts if self.config.retry_failed else 0)

        for attempt in range(1, attempts_allowed + 1):
            try:
                self.send_fn(email, subject, body)
                self.state.mark_sent(campaign_id, row_index)
                self.logger.log("SENT", email, campaign_id, name=name)
                return True

            except AuthExpiredError:
                refreshed = self.refresh_fn() if self.refresh_fn else False
                if not refreshed:
                    self.state.mark_failed(
                        campaign_id, row_index, "auth expired; re-auth required"
                    )
                    self.logger.log(
                        "FAILED", email, campaign_id, name=name,
                        error="auth expired; run `maillaunch auth` to re-authenticate",
                    )
                    return False
                # Refreshed successfully -- retry this attempt without
                # counting it against the backoff/attempt budget.
                continue

            except SendError as e:
                is_last = attempt >= attempts_allowed
                self.state.increment_attempts(campaign_id, row_index)
                if is_last:
                    self.state.mark_failed(campaign_id, row_index, str(e))
                    self.logger.log("FAILED", email, campaign_id, name=name, error=str(e))
                    return False
                else:
                    backoff = (2 ** (attempt - 1)) * self.config.min_delay_seconds
                    self.logger.log(
                        "RETRY", email, campaign_id, name=name,
                        error=f"attempt {attempt}/{attempts_allowed - 1} in {backoff}s",
                    )
                    self.config.sleep_fn(backoff)

        return False

    def _maybe_complete(self, campaign_id: str) -> None:
        summary = self.state.campaign_summary(campaign_id)
        if summary["pending"] == 0:
            self.state.mark_campaign_complete(campaign_id)
