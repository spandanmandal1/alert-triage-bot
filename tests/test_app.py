import json

import httpx
import pytest
import respx
from fastapi.testclient import TestClient
from pydantic import SecretStr

from triage.main import create_app

LOKI = "http://loki.test/loki/api/v1/query_range"
LLM = "http://llm.test/v1/chat/completions"
SLACK = "https://hooks.slack.test/services/T/B/X"


def chat(text: str) -> httpx.Response:
    return httpx.Response(200, json={"choices": [{"message": {"content": text}}]})


@pytest.fixture
def mocked(loki_response):
    with respx.mock(assert_all_called=False) as router:
        router.get(LOKI).mock(return_value=httpx.Response(200, json=loki_response))
        router.post(LLM).mock(return_value=chat("Likely cause: payment timeouts"))
        router.post(SLACK).mock(return_value=httpx.Response(200))
        yield router


def slack_bodies(router) -> list[dict]:
    return [json.loads(c.request.content) for c in router.calls if str(c.request.url) == SLACK]


def test_health_endpoints(settings):
    with TestClient(create_app(settings)) as client:
        assert client.get("/healthz").status_code == 200
        assert client.get("/readyz").status_code == 200


def test_firing_batch_posts_one_message_per_alert(settings, mocked, firing_payload):
    with TestClient(create_app(settings)) as client:
        resp = client.post("/webhook/alertmanager", json=firing_payload)
    assert resp.status_code == 202
    assert resp.json() == {"accepted": 2, "duplicates": 0}
    bodies = slack_bodies(mocked)
    assert len(bodies) == 2
    assert "payment timeouts" in json.dumps(bodies[0])


def test_redelivery_is_deduplicated(settings, mocked, firing_payload):
    with TestClient(create_app(settings)) as client:
        client.post("/webhook/alertmanager", json=firing_payload)
        resp = client.post("/webhook/alertmanager", json=firing_payload)
    assert resp.json() == {"accepted": 0, "duplicates": 2}
    assert len(slack_bodies(mocked)) == 2


def test_resolved_skips_loki_and_llm(settings, mocked, resolved_payload):
    with TestClient(create_app(settings)) as client:
        client.post("/webhook/alertmanager", json=resolved_payload)
    urls = [str(c.request.url) for c in mocked.calls]
    assert urls == [SLACK]
    assert "Resolved" in slack_bodies(mocked)[0]["text"]


def test_llm_down_still_posts_alert_with_logs(settings, mocked, firing_payload):
    mocked.post(LLM).mock(return_value=httpx.Response(500))
    with TestClient(create_app(settings)) as client:
        client.post("/webhook/alertmanager", json=firing_payload)
    body = json.dumps(slack_bodies(mocked)[0])
    assert "No AI summary" in body
    assert "payment call failed" in body


def test_loki_down_still_asks_llm(settings, mocked, firing_payload):
    mocked.get(LOKI).mock(side_effect=httpx.ConnectError("refused"))
    with TestClient(create_app(settings)) as client:
        client.post("/webhook/alertmanager", json=firing_payload)
    body = json.dumps(slack_bodies(mocked)[0])
    assert "payment timeouts" in body
    assert "No matching error logs" in body


def test_slack_down_does_not_crash(settings, mocked, firing_payload):
    mocked.post(SLACK).mock(return_value=httpx.Response(500))
    with TestClient(create_app(settings)) as client:
        assert client.post("/webhook/alertmanager", json=firing_payload).status_code == 202
        assert client.get("/healthz").status_code == 200


def test_rejects_bad_token_when_configured(settings, mocked, firing_payload):
    settings.webhook_token = SecretStr("s3cret")
    with TestClient(create_app(settings)) as client:
        assert client.post("/webhook/alertmanager", json=firing_payload).status_code == 401
        ok = client.post(
            "/webhook/alertmanager", json=firing_payload, headers={"Authorization": "Bearer s3cret"}
        )
        assert ok.status_code == 202


def test_rejects_invalid_payload(settings):
    with TestClient(create_app(settings)) as client:
        assert client.post("/webhook/alertmanager", json={"nope": 1}).status_code == 422


def test_metrics_count_alerts(settings, mocked, firing_payload):
    with TestClient(create_app(settings)) as client:
        client.post("/webhook/alertmanager", json=firing_payload)
        metrics = client.get("/metrics").text
    assert 'triage_alerts_received_total{status="firing"}' in metrics
    assert "triage_llm_request_seconds" in metrics
