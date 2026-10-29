from triage.dedupe import Deduper


class FakeClock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


def test_second_delivery_within_ttl_is_duplicate():
    clock = FakeClock()
    d = Deduper(ttl_seconds=60, clock=clock)
    assert d.is_duplicate("fp1", "firing") is False
    assert d.is_duplicate("fp1", "firing") is True


def test_delivery_after_ttl_is_new():
    clock = FakeClock()
    d = Deduper(ttl_seconds=60, clock=clock)
    d.is_duplicate("fp1", "firing")
    clock.now += 61
    assert d.is_duplicate("fp1", "firing") is False


def test_resolve_allows_refire():
    clock = FakeClock()
    d = Deduper(ttl_seconds=3600, clock=clock)
    d.is_duplicate("fp1", "firing")
    assert d.is_duplicate("fp1", "resolved") is False
    # the same alert firing again after resolving is a new incident, not a duplicate
    assert d.is_duplicate("fp1", "firing") is False


def test_expired_entries_are_evicted():
    clock = FakeClock()
    d = Deduper(ttl_seconds=10, clock=clock)
    for i in range(100):
        d.is_duplicate(f"fp{i}", "firing")
    clock.now += 11
    d.is_duplicate("new", "firing")
    assert len(d) == 1


def test_flapping_alert_is_not_suppressed():
    # fire -> resolve -> fire -> resolve -> fire, all inside the TTL: every transition is news
    clock = FakeClock()
    d = Deduper(ttl_seconds=3600, clock=clock)
    for status in ["firing", "resolved", "firing", "resolved", "firing"]:
        clock.now += 60
        assert d.is_duplicate("fp1", status) is False, status
    # but a retry of the current state is still a duplicate
    assert d.is_duplicate("fp1", "firing") is True
