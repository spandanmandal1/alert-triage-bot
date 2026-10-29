from prometheus_client import Counter, Histogram

ALERTS_RECEIVED = Counter("triage_alerts_received_total", "Alerts received from Alertmanager", ["status"])
ALERTS_DUPLICATE = Counter("triage_alerts_duplicate_total", "Alerts dropped as duplicates")
ALERTS_PROCESSED = Counter("triage_alerts_processed_total", "Alerts fully processed", ["status", "outcome"])
DEPENDENCY_ERRORS = Counter("triage_dependency_errors_total", "Failed calls to Loki/LLM/Slack", ["target"])
LLM_SECONDS = Histogram(
    "triage_llm_request_seconds",
    "LLM request latency",
    buckets=(1, 2.5, 5, 10, 20, 30, 45, 60, 90, 120),
)
PROCESSING_SECONDS = Histogram(
    "triage_processing_seconds",
    "Time from webhook receipt to Slack post",
    buckets=(1, 2.5, 5, 10, 20, 30, 45, 60, 90, 120, 180),
)
