from careerops_scheduler.dispatcher import effective_quota


def test_effective_quota_never_exceeds_25():
    assert effective_quota(planned=24, carry=20, remaining=100, max_per_run=25) == 25


def test_effective_quota_respects_daily_remaining():
    assert effective_quota(planned=20, carry=5, remaining=7, max_per_run=25) == 7


def test_carry_can_fill_a_later_batch_but_only_to_cap():
    assert effective_quota(planned=18, carry=4, remaining=80, max_per_run=25) == 22
