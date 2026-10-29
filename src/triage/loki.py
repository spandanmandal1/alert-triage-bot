import logging
from datetime import timedelta

import httpx

from triage.config import Settings
from triage.metrics import DEPENDENCY_ERRORS
from triage.models import Alert

log = logging.getLogger(__name__)

# Most specific first. pod is last because pods get replaced and the old name stops matching.
WORKLOAD_LABELS = ("service", "app", "job", "container", "pod")
LINE_FILTER = '|~ "(?i)(error|warn|exception|fatal|panic|timeout|refused)"'
MAX_LINE_LEN = 300


def _quote(value: str) -> str:
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'


def build_logql(labels: dict[str, str]) -> str | None:
    """Build a LogQL query for the alert's workload, or None if the alert has no workload label."""
    workload = next((k for k in WORKLOAD_LABELS if labels.get(k)), None)
    if workload is None:
        return None
    matchers = []
    if labels.get("namespace"):
        matchers.append(f"namespace={_quote(labels['namespace'])}")
    matchers.append(f"{workload}={_quote(labels[workload])}")
    return "{" + ", ".join(matchers) + "} " + LINE_FILTER


def extract_lines(data: dict, max_lines: int, max_chars: int) -> list[str]:
    """Flatten a query_range response to unique lines, newest first, within the size budget."""
    streams = (data.get("data") or {}).get("result") or []
    entries: list[tuple[int, str]] = []
    for stream in streams:
        for ts, line in stream.get("values") or []:
            entries.append((int(ts), line.strip()[:MAX_LINE_LEN]))
    entries.sort(key=lambda e: e[0], reverse=True)

    counts: dict[str, int] = {}
    for _, line in entries:
        counts[line] = counts.get(line, 0) + 1  # dict keeps first-seen (newest) order

    out: list[str] = []
    used = 0
    for line, n in counts.items():
        text = f"{line} (x{n})" if n > 1 else line
        if len(out) >= max_lines or used + len(text) > max_chars:
            break
        out.append(text)
        used += len(text)
    return out


async def fetch_logs(client: httpx.AsyncClient, alert: Alert, settings: Settings) -> list[str]:
    """Return recent error lines for the alert's workload. Empty list on any failure."""
    query = build_logql(alert.labels)
    if query is None:
        log.info("no workload label, skipping loki", extra={"alert": alert.name})
        return []
    start = alert.starts_at - timedelta(minutes=settings.log_lookback_minutes)
    end = alert.starts_at + timedelta(minutes=1)
    params = {
        "query": query,
        "start": str(int(start.timestamp()) * 10**9),
        "end": str(int(end.timestamp()) * 10**9),
        "limit": "500",
        "direction": "backward",
    }
    try:
        resp = await client.get(
            f"{settings.loki_url}/loki/api/v1/query_range", params=params, timeout=settings.loki_timeout
        )
        resp.raise_for_status()
        return extract_lines(resp.json(), settings.max_log_lines, settings.max_log_chars)
    except (httpx.HTTPError, ValueError) as exc:
        DEPENDENCY_ERRORS.labels(target="loki").inc()
        log.warning("loki query failed", extra={"alert": alert.name, "error": repr(exc)})
        return []
