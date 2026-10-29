import json

import httpx
import pytest
import respx

from triage.models import WebhookPayload
from triage.slack import SlackError, format_firing, format_resolved, send


def all_text(message: dict) -> str:
    return json.dumps(message)


def test_firing_message_has_cause_logs_and_links(settings, checkout_alert):
    msg = format_firing(checkout_alert, ["level=error boom"], "Likely cause: payment down", None, settings)
    text = all_text(msg)
    assert "HighErrorRate" in msg["text"]
    assert "Likely cause: payment down" in text
    assert "level=error boom" in text
    assert "http://prometheus:9090/graph" in text
    assert "http://grafana.test" in text


def test_firing_message_explains_missing_ai_summary(settings, checkout_alert):
    msg = format_firing(checkout_alert, [], None, "LLM timed out after 60s", settings)
    text = all_text(msg)
    assert "No AI summary" in text
    assert "LLM timed out" in text
    assert "No matching error logs" in text


def test_firing_message_stays_under_slack_block_limit(settings, checkout_alert):
    lines = ["x" * 290] * 50
    msg = format_firing(checkout_alert, lines, "y" * 5000, None, settings)
    for block in msg["blocks"]:
        if block["type"] == "section":
            assert len(block["text"]["text"]) <= 3000


def test_resolved_message(resolved_payload):
    alert = WebhookPayload.model_validate(resolved_payload).alerts[0]
    msg = format_resolved(alert)
    assert "Resolved" in msg["text"]
    assert "HighErrorRate" in msg["text"]


async def test_dry_run_prints_instead_of_posting(settings, capsys):
    settings.dry_run = True
    async with httpx.AsyncClient() as client:
        await send(client, {"text": "hello"}, settings)
    assert '"text": "hello"' in capsys.readouterr().out


@respx.mock
async def test_send_posts_to_webhook(settings):
    route = respx.post("https://hooks.slack.test/services/T/B/X").mock(return_value=httpx.Response(200))
    async with httpx.AsyncClient() as client:
        await send(client, {"text": "hello"}, settings)
    assert route.called


@respx.mock
async def test_send_raises_on_failure(settings):
    respx.post("https://hooks.slack.test/services/T/B/X").mock(return_value=httpx.Response(404))
    async with httpx.AsyncClient() as client:
        with pytest.raises(SlackError):
            await send(client, {"text": "hello"}, settings)
