import httpx
import pytest
import respx

from triage.llm import LLMError, build_messages, clean_output, suggest_cause


def test_prompt_includes_alert_and_logs(checkout_alert):
    messages = build_messages(checkout_alert, ["level=error msg=boom"])
    assert messages[0]["role"] == "system"
    user = messages[1]["content"]
    assert "HighErrorRate" in user
    assert "checkout is returning 12% 5xx" in user
    assert "level=error msg=boom" in user


def test_prompt_says_when_no_logs(checkout_alert):
    user = build_messages(checkout_alert, [])[1]["content"]
    assert "No logs" in user


def test_clean_output_strips_think_blocks_and_whitespace():
    assert clean_output("<think>hmm</think>\n\nLikely cause: x\n") == "Likely cause: x"


def chat_response(text: str) -> dict:
    return {"choices": [{"message": {"role": "assistant", "content": text}}]}


@respx.mock
async def test_suggest_cause_calls_openai_compatible_api(settings, checkout_alert):
    route = respx.post("http://llm.test/v1/chat/completions").mock(
        return_value=httpx.Response(200, json=chat_response("Likely cause: payment timeouts"))
    )
    async with httpx.AsyncClient() as client:
        text = await suggest_cause(client, checkout_alert, ["l1"], settings)
    assert text == "Likely cause: payment timeouts"
    body = route.calls.last.request.read()
    assert b'"model":"qwen2.5:0.5b"' in body.replace(b" ", b"")


@respx.mock
async def test_suggest_cause_raises_on_http_error(settings, checkout_alert):
    respx.post("http://llm.test/v1/chat/completions").mock(return_value=httpx.Response(503))
    async with httpx.AsyncClient() as client:
        with pytest.raises(LLMError, match="503"):
            await suggest_cause(client, checkout_alert, [], settings)


@respx.mock
async def test_suggest_cause_raises_on_timeout(settings, checkout_alert):
    respx.post("http://llm.test/v1/chat/completions").mock(side_effect=httpx.ReadTimeout("slow"))
    async with httpx.AsyncClient() as client:
        with pytest.raises(LLMError, match="timed out"):
            await suggest_cause(client, checkout_alert, [], settings)


@respx.mock
async def test_suggest_cause_raises_on_empty_answer(settings, checkout_alert):
    respx.post("http://llm.test/v1/chat/completions").mock(
        return_value=httpx.Response(200, json=chat_response("<think>...</think>"))
    )
    async with httpx.AsyncClient() as client:
        with pytest.raises(LLMError, match="empty"):
            await suggest_cause(client, checkout_alert, [], settings)
