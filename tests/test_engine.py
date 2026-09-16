from pathlib import Path

import pytest

from src.campaign.csv_parser import Recipient
from src.campaign.send_log import SendLogger
from src.campaign.state import StateStore
from src.sender.engine import EngineConfig, SendEngine
from src.sender.gmail_sender import AuthExpiredError, SendError


def make_state(tmp_path) -> StateStore:
    return StateStore(db_path=Path(tmp_path) / "state.db")


def make_logger(tmp_path) -> SendLogger:
    return SendLogger(log_dir=Path(tmp_path) / "logs")


def make_recipients(n: int):
    return [
        Recipient(row_index=i, email=f"user{i}@example.com", data={"name": f"User {i}"})
        for i in range(n)
    ]


def no_sleep(_seconds):
    pass


def fixed_delay(_a, _b):
    return 0


def test_all_sends_succeed(tmp_path):
    state = make_state(tmp_path)
    logger = make_logger(tmp_path)
    recipients = make_recipients(3)
    cid = state.create_campaign("gmail", "r.csv", "Hi {{name}}", "Body", None, recipients)

    engine = SendEngine(
        state=state,
        logger=logger,
        config=EngineConfig(
            daily_limit=100, min_delay_seconds=0, max_delay_seconds=0,
            retry_attempts=2, sleep_fn=no_sleep, random_fn=fixed_delay,
        ),
        send_fn=lambda to, subj, body: None,  # always succeeds
    )

    summary = engine.run(cid, "Hi {{name}}", "Body")
    assert summary == {"sent": 3, "failed": 0, "skipped": 0, "pending": 0}


def test_daily_limit_stops_campaign_and_leaves_rest_pending(tmp_path):
    state = make_state(tmp_path)
    logger = make_logger(tmp_path)
    recipients = make_recipients(5)
    cid = state.create_campaign("gmail", "r.csv", "s", "b", None, recipients)

    engine = SendEngine(
        state=state,
        logger=logger,
        config=EngineConfig(
            daily_limit=2, min_delay_seconds=0, max_delay_seconds=0,
            retry_attempts=0, sleep_fn=no_sleep, random_fn=fixed_delay,
        ),
        send_fn=lambda to, subj, body: None,
    )

    summary = engine.run(cid, "s", "b")
    assert summary["sent"] == 2
    assert summary["pending"] == 3

    record = state.get_campaign(cid)
    assert record.status == "IN_PROGRESS"


def test_daily_limit_respects_prior_sends_from_other_campaigns(tmp_path):
    state = make_state(tmp_path)
    logger = make_logger(tmp_path)

    prior = state.create_campaign("gmail", "a.csv", "s", "b", None, make_recipients(3))
    state.mark_sent(prior, 0)
    state.mark_sent(prior, 1)
    state.mark_sent(prior, 2)  # 3 already sent today

    cid = state.create_campaign("gmail", "b.csv", "s", "b", None, make_recipients(5))
    engine = SendEngine(
        state=state,
        logger=logger,
        config=EngineConfig(
            daily_limit=5, min_delay_seconds=0, max_delay_seconds=0,
            retry_attempts=0, sleep_fn=no_sleep, random_fn=fixed_delay,
        ),
        send_fn=lambda to, subj, body: None,
    )

    summary = engine.run(cid, "s", "b")
    # Only 2 more sends allowed before hitting the cumulative limit of 5.
    assert summary["sent"] == 2
    assert summary["pending"] == 3


def test_retry_then_success(tmp_path):
    state = make_state(tmp_path)
    logger = make_logger(tmp_path)
    cid = state.create_campaign("gmail", "r.csv", "s", "b", None, make_recipients(1))

    calls = {"n": 0}

    def flaky_send(to, subj, body):
        calls["n"] += 1
        if calls["n"] < 2:
            raise SendError("HTTP 429 - Too Many Requests", status_code=429)

    engine = SendEngine(
        state=state,
        logger=logger,
        config=EngineConfig(
            daily_limit=100, min_delay_seconds=1, max_delay_seconds=1,
            retry_attempts=2, sleep_fn=no_sleep, random_fn=fixed_delay,
        ),
        send_fn=flaky_send,
    )

    summary = engine.run(cid, "s", "b")
    assert summary["sent"] == 1
    assert calls["n"] == 2


def test_retry_exhausted_marks_failed(tmp_path):
    state = make_state(tmp_path)
    logger = make_logger(tmp_path)
    cid = state.create_campaign("gmail", "r.csv", "s", "b", None, make_recipients(1))

    def always_fail(to, subj, body):
        raise SendError("HTTP 500 - Internal Server Error", status_code=500)

    engine = SendEngine(
        state=state,
        logger=logger,
        config=EngineConfig(
            daily_limit=100, min_delay_seconds=1, max_delay_seconds=1,
            retry_attempts=2, sleep_fn=no_sleep, random_fn=fixed_delay,
        ),
        send_fn=always_fail,
    )

    summary = engine.run(cid, "s", "b")
    assert summary["failed"] == 1
    assert summary["sent"] == 0

    record_summary = state.campaign_summary(cid)
    assert record_summary["failed"] == 1


def test_retry_disabled_fails_on_first_error(tmp_path):
    state = make_state(tmp_path)
    logger = make_logger(tmp_path)
    cid = state.create_campaign("gmail", "r.csv", "s", "b", None, make_recipients(1))

    calls = {"n": 0}

    def always_fail(to, subj, body):
        calls["n"] += 1
        raise SendError("HTTP 500", status_code=500)

    engine = SendEngine(
        state=state,
        logger=logger,
        config=EngineConfig(
            daily_limit=100, min_delay_seconds=0, max_delay_seconds=0,
            retry_attempts=2, retry_failed=False, sleep_fn=no_sleep, random_fn=fixed_delay,
        ),
        send_fn=always_fail,
    )

    engine.run(cid, "s", "b")
    assert calls["n"] == 1  # no retries attempted


def test_auth_expired_triggers_refresh_and_retries(tmp_path):
    state = make_state(tmp_path)
    logger = make_logger(tmp_path)
    cid = state.create_campaign("gmail", "r.csv", "s", "b", None, make_recipients(1))

    calls = {"n": 0}

    def send_fn(to, subj, body):
        calls["n"] += 1
        if calls["n"] == 1:
            raise AuthExpiredError("token expired")

    refresh_calls = {"n": 0}

    def refresh_fn():
        refresh_calls["n"] += 1
        return True

    engine = SendEngine(
        state=state,
        logger=logger,
        config=EngineConfig(
            daily_limit=100, min_delay_seconds=0, max_delay_seconds=0,
            retry_attempts=1, sleep_fn=no_sleep, random_fn=fixed_delay,
        ),
        send_fn=send_fn,
        refresh_fn=refresh_fn,
    )

    summary = engine.run(cid, "s", "b")
    assert summary["sent"] == 1
    assert refresh_calls["n"] == 1


def test_auth_expired_with_failed_refresh_marks_failed_and_aborts(tmp_path):
    state = make_state(tmp_path)
    logger = make_logger(tmp_path)
    cid = state.create_campaign("gmail", "r.csv", "s", "b", None, make_recipients(1))

    def send_fn(to, subj, body):
        raise AuthExpiredError("token expired")

    engine = SendEngine(
        state=state,
        logger=logger,
        config=EngineConfig(
            daily_limit=100, min_delay_seconds=0, max_delay_seconds=0,
            retry_attempts=1, sleep_fn=no_sleep, random_fn=fixed_delay,
        ),
        send_fn=send_fn,
        refresh_fn=lambda: False,  # refresh fails -> abort, prompt re-auth
    )

    summary = engine.run(cid, "s", "b")
    assert summary["failed"] == 1
