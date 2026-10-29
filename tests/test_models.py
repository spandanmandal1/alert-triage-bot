from datetime import UTC, datetime

from triage.models import WebhookPayload


def test_parses_batched_payload(firing_payload):
    payload = WebhookPayload.model_validate(firing_payload)
    assert len(payload.alerts) == 2
    alert = payload.alerts[0]
    assert alert.name == "HighErrorRate"
    assert alert.status == "firing"
    assert alert.starts_at == datetime(2026, 10, 29, 10, 0, tzinfo=UTC)
    assert alert.severity == "critical"
    assert alert.summary == "checkout 5xx rate above 5%"


def test_missing_optional_fields_use_defaults():
    payload = WebhookPayload.model_validate(
        {
            "status": "firing",
            "alerts": [
                {"status": "firing", "labels": {}, "startsAt": "2026-10-29T10:00:00Z", "fingerprint": "x"}
            ],
        }
    )
    alert = payload.alerts[0]
    assert alert.name == "unknown"
    assert alert.severity == "none"
    assert alert.summary == ""
