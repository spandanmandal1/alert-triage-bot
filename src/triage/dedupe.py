import time
from collections.abc import Callable


class Deduper:
    """Remembers (fingerprint, status) pairs for a TTL so Alertmanager retries don't double-post.

    In-memory only: with more than one replica each pod dedupes on its own, which is acceptable
    because Alertmanager sends each notification to one receiver endpoint.
    """

    def __init__(self, ttl_seconds: float, clock: Callable[[], float] = time.monotonic) -> None:
        self._ttl = ttl_seconds
        self._clock = clock
        self._seen: dict[tuple[str, str], float] = {}

    def __len__(self) -> int:
        return len(self._seen)

    def is_duplicate(self, fingerprint: str, status: str) -> bool:
        now = self._clock()
        self._evict(now)
        key = (fingerprint, status)
        if key in self._seen:
            return True
        self._seen[key] = now
        # A state change makes the opposite state news again. Without this, an alert that
        # flaps (fire, resolve, fire, resolve) within the TTL had its second resolve and
        # every later firing dropped as duplicates.
        opposite = "firing" if status == "resolved" else "resolved"
        self._seen.pop((fingerprint, opposite), None)
        return False

    def _evict(self, now: float) -> None:
        expired = [k for k, t in self._seen.items() if now - t > self._ttl]
        for k in expired:
            del self._seen[k]
