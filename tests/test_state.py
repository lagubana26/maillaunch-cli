from pathlib import Path

from src.campaign.csv_parser import Recipient
from src.campaign.state import StateStore


def make_state(tmp_path) -> StateStore:
    return StateStore(db_path=Path(tmp_path) / "state.db")


def make_recipients(n: int):
    return [
        Recipient(row_index=i, email=f"user{i}@example.com", data={"name": f"User {i}"})
        for i in range(n)
    ]


def test_create_campaign_and_pending(tmp_path):
    state = make_state(tmp_path)
    recipients = make_recipients(3)
    cid = state.create_campaign("gmail", "r.csv", "Hi {{name}}", "Body", None, recipients)

    pending = state.pending_recipients(cid)
    assert len(pending) == 3
    assert {p["email"] for p in pending} == {r.email for r in recipients}


def test_mark_sent_removes_from_pending_and_counts_today(tmp_path):
    state = make_state(tmp_path)
    recipients = make_recipients(2)
    cid = state.create_campaign("gmail", "r.csv", "Hi", "Body", None, recipients)

    state.mark_sent(cid, 0)

    pending = state.pending_recipients(cid)
    assert len(pending) == 1
    assert pending[0]["row_index"] == 1
    assert state.sent_count_today() == 1


def test_daily_limit_enforced_across_campaigns(tmp_path):
    """The 100/day limit must hold cumulatively across multiple
    campaigns run the same day, not reset per-campaign."""
    state = make_state(tmp_path)

    cid1 = state.create_campaign("gmail", "a.csv", "s", "b", None, make_recipients(2))
    state.mark_sent(cid1, 0)
    state.mark_sent(cid1, 1)

    cid2 = state.create_campaign("gmail", "b.csv", "s", "b", None, make_recipients(2))
    state.mark_sent(cid2, 0)

    assert state.sent_count_today() == 3


def test_mark_failed_and_skipped(tmp_path):
    state = make_state(tmp_path)
    cid = state.create_campaign("gmail", "r.csv", "s", "b", None, make_recipients(2))

    state.mark_failed(cid, 0, "HTTP 500")
    state.mark_skipped(cid, 1, "invalid address")

    summary = state.campaign_summary(cid)
    assert summary == {"sent": 0, "failed": 1, "skipped": 1, "pending": 0}


def test_resume_finds_latest_incomplete_campaign(tmp_path):
    state = make_state(tmp_path)
    cid1 = state.create_campaign("gmail", "a.csv", "s", "b", None, make_recipients(1))
    state.mark_sent(cid1, 0)
    state.mark_campaign_complete(cid1)

    cid2 = state.create_campaign("gmail", "b.csv", "s", "b", None, make_recipients(1))

    latest = state.latest_incomplete_campaign()
    assert latest.id == cid2


def test_completed_campaign_not_returned_as_incomplete(tmp_path):
    state = make_state(tmp_path)
    cid = state.create_campaign("gmail", "a.csv", "s", "b", None, make_recipients(1))
    state.mark_sent(cid, 0)
    state.mark_campaign_complete(cid)

    assert state.latest_incomplete_campaign() is None
