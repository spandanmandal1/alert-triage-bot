import json
from pathlib import Path

import pytest

from triage.config import Settings
from triage.models import WebhookPayload

FIXTURES = Path(__file__).parent / "fixtures"


def load_fixture(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text())


@pytest.fixture
def settings() -> Settings:
    return Settings(
        loki_url="http://loki.test",
        llm_base_url="http://llm.test/v1",
        llm_model="qwen2.5:0.5b",
        slack_webhook_url="https://hooks.slack.test/services/T/B/X",
        dry_run=False,
        grafana_url="http://grafana.test",
    )


@pytest.fixture
def firing_payload() -> dict:
    return load_fixture("firing_batch.json")


@pytest.fixture
def resolved_payload() -> dict:
    return load_fixture("resolved.json")


@pytest.fixture
def loki_response() -> dict:
    return load_fixture("loki_response.json")


@pytest.fixture
def checkout_alert(firing_payload):
    return WebhookPayload.model_validate(firing_payload).alerts[0]
