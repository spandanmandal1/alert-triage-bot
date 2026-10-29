import json
import logging

import httpx

from triage.config import Settings
from triage.metrics import DEPENDENCY_ERRORS
from triage.models import Alert

log = logging.getLogger(__name__)

SECTION_LIMIT = 3000  # Slack rejects section text longer than this
LOG_LINES_SHOWN = 10
SEVERITY_EMOJI = {
    "critical": ":red_circle:",
    "warning": ":large_orange_circle:",
    "info": ":large_blue_circle:",
}


class SlackError(Exception):
    pass


def _section(text: str) -> dict:
    if len(text) > SECTION_LIMIT:
        text = text[: SECTION_LIMIT - 1] + "…"
    return {"type": "section", "text": {"type": "mrkdwn", "text": text}}


def _code(lines: list[str]) -> str:
    # 6 chars for the backtick fences, 1 for a possible ellipsis
    body = "\n".join(lines).replace("```", "'''")[: SECTION_LIMIT - 7]
    return f"```{body}```"


def format_firing(
    alert: Alert, log_lines: list[str], cause: str | None, ai_error: str | None, settings: Settings
) -> dict:
    emoji = SEVERITY_EMOJI.get(alert.severity, ":white_circle:")
    title = f"{emoji} [FIRING] {alert.name} ({alert.severity})"
    labels = ", ".join(f"`{k}={v}`" for k, v in sorted(alert.labels.items()) if k != "alertname")

    blocks: list[dict] = [_section(f"*{title}*\n{alert.summary}\n{labels}")]
    if cause:
        blocks.append(
            _section(f"*AI triage* _(self-hosted {settings.llm_model}, verify before acting)_\n{cause}")
        )
    else:
        blocks.append(_section(f"*No AI summary* ({ai_error or 'unknown reason'})"))

    if log_lines:
        shown = log_lines[:LOG_LINES_SHOWN]
        blocks.append(_section(f"*Recent error logs* ({len(shown)} of {len(log_lines)})"))
        blocks.append(_section(_code(shown)))
    else:
        blocks.append(_section("_No matching error logs in Loki for this alert window._"))

    links = []
    if alert.generator_url:
        links.append(f"<{alert.generator_url}|Prometheus>")
    if settings.grafana_url:
        links.append(f"<{settings.grafana_url}|Grafana>")
    if links:
        blocks.append({"type": "context", "elements": [{"type": "mrkdwn", "text": " · ".join(links)}]})

    return {"text": f"[FIRING] {alert.name}: {alert.summary}", "blocks": blocks}


def format_resolved(alert: Alert) -> dict:
    text = f":large_green_circle: Resolved: {alert.name}"
    if alert.summary:
        text += f" ({alert.summary})"
    return {"text": text}


async def send(client: httpx.AsyncClient, message: dict, settings: Settings) -> None:
    if settings.dry_run or settings.slack_webhook_url is None:
        print(json.dumps(message, indent=2, ensure_ascii=False), flush=True)
        return
    try:
        resp = await client.post(
            settings.slack_webhook_url.get_secret_value(), json=message, timeout=settings.slack_timeout
        )
        resp.raise_for_status()
    except httpx.HTTPStatusError as exc:
        DEPENDENCY_ERRORS.labels(target="slack").inc()
        # never put the exception text in the error: it contains the webhook URL, which is a secret
        raise SlackError(f"Slack returned HTTP {exc.response.status_code}") from exc
    except httpx.HTTPError as exc:
        DEPENDENCY_ERRORS.labels(target="slack").inc()
        raise SlackError(f"Slack request failed: {exc.__class__.__name__}") from exc
