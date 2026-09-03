from app.twilio import dedup


def test_first_sighting_is_not_a_duplicate():
    assert dedup.seen_before("SM_new_1") is False


def test_repeated_sighting_is_a_duplicate():
    dedup.seen_before("SM_repeat")
    assert dedup.seen_before("SM_repeat") is True


def test_different_sids_are_independent():
    dedup.seen_before("SM_a")
    assert dedup.seen_before("SM_b") is False


def test_expired_entries_are_swept(monkeypatch):
    import time

    real_now = time.time()
    dedup.seen_before("SM_old")

    monkeypatch.setattr(time, "time", lambda: real_now + dedup._TTL_SECONDS + 1)
    # Past its TTL, "SM_old" must be treated as unseen again, not as a duplicate.
    assert dedup.seen_before("SM_old") is False
