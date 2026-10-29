import logging
import re
import time

import httpx

from triage.config import Settings
from triage.metrics import DEPENDENCY_ERRORS, LLM_SECONDS
from triage.models import Alert

log = logging.getLogger(__name__)

SYSTEM_PROMPT = """You are an SRE helping an on-call engineer triage a Prometheus alert.
Use only the alert and log lines given. Do not invent services, metrics or numbers.
Reply in plain text, at most 80 words, in exactly this format:
Likely cause: <one sentence>
Check next:
- <first concrete check>
- <second concrete check>
If the logs do not show a cause, say "Likely cause: unclear from logs" and suggest what to look at."""

THINK_BLOCK = re.compile(r"<think>.*?</think>", re.DOTALL)


class LLMError(Exception):
    pass


def build_messages(alert: Alert, log_lines: list[str]) -> list[dict[str, str]]:
    labels = ", ".join(f"{k}={v}" for k, v in sorted(alert.labels.items()))
    logs = "\n".join(log_lines) if log_lines else "No logs found for this workload in the alert window."
    user = (
        f"Alert: {alert.name} (severity {alert.severity})\n"
        f"Labels: {labels}\n"
        f"Summary: {alert.summary}\n"
        f"Description: {alert.description}\n"
        f"Started: {alert.starts_at.isoformat()}\n\n"
        f"Recent error/warning logs, newest first:\n{logs}"
    )
    return [{"role": "system", "content": SYSTEM_PROMPT}, {"role": "user", "content": user}]


def clean_output(text: str) -> str:
    # reasoning models (qwen3, deepseek-r1) put their chain of thought in <think> tags
    return THINK_BLOCK.sub("", text).strip()


async def suggest_cause(
    client: httpx.AsyncClient, alert: Alert, log_lines: list[str], settings: Settings
) -> str:
    """Ask the LLM for a likely cause. Raises LLMError with a short, user-facing reason."""
    body = {
        "model": settings.llm_model,
        "messages": build_messages(alert, log_lines),
        "temperature": 0.2,
        "max_tokens": settings.llm_max_tokens,
        "stream": False,
    }
    headers = {"Authorization": f"Bearer {settings.llm_api_key.get_secret_value()}"}
    started = time.perf_counter()
    try:
        resp = await client.post(
            f"{settings.llm_base_url}/chat/completions",
            json=body,
            headers=headers,
            timeout=settings.llm_timeout,
        )
        resp.raise_for_status()
        content = resp.json()["choices"][0]["message"]["content"] or ""
    except httpx.TimeoutException as exc:
        DEPENDENCY_ERRORS.labels(target="llm").inc()
        raise LLMError(f"LLM timed out after {settings.llm_timeout:.0f}s") from exc
    except httpx.HTTPStatusError as exc:
        DEPENDENCY_ERRORS.labels(target="llm").inc()
        raise LLMError(f"LLM returned HTTP {exc.response.status_code}") from exc
    except (httpx.HTTPError, ValueError, KeyError, IndexError, TypeError) as exc:
        DEPENDENCY_ERRORS.labels(target="llm").inc()
        raise LLMError(f"LLM request failed: {exc.__class__.__name__}") from exc
    finally:
        LLM_SECONDS.observe(time.perf_counter() - started)

    text = clean_output(content)
    if not text:
        raise LLMError("LLM returned an empty answer")
    return text
