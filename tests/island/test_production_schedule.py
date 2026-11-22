"""When the production task has to run again after claiming rewards."""
from datetime import datetime, timedelta

from module.island.production import (
    CLAIM_RETRY_MINUTE,
    IDLE_MINUTE,
    get_next_run_time,
)


def test_next_run_follows_the_earliest_slot_that_is_still_running():
    now = datetime(2026, 11, 7, 13, 0, 0)
    early = now + timedelta(minutes=40)
    late = now + timedelta(hours=2)

    assert get_next_run_time([late, early], now) == (early, None)


def test_pending_reward_retries_shortly_instead_of_waking_on_a_past_time():
    now = datetime(2026, 11, 7, 13, 0, 0)
    started_elsewhere = now - timedelta(hours=14)

    # The record cannot be used as a target, it is already gone; the reward is
    # still pending, so come back for it soon.
    assert get_next_run_time([started_elsewhere], now) == (None, CLAIM_RETRY_MINUTE)


def test_no_slot_running_waits_for_the_idle_interval():
    assert get_next_run_time([], datetime(2026, 11, 7, 13, 0, 0)) == (None, IDLE_MINUTE)
