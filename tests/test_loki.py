from datetime import UTC, datetime

import httpx
import respx

from triage.loki import build_logql, extract_lines, fetch_logs


def test_logql_uses_namespace_and_first_workload_label():
    q = build_logql({"namespace": "shop", "service": "checkout", "pod": "checkout-abc", "app": "x"})
    assert q is not None
    assert q.startswith('{namespace="shop", service="checkout"}')
    assert "|~" in q and "error" in q


def test_logql_falls_back_through_workload_labels():
    assert build_logql({"job": "node"}).startswith('{job="node"}')
    assert build_logql({"namespace": "shop", "container": "api"}).startswith(
        '{namespace="shop", container="api"}'
    )


def test_logql_none_without_workload_label():
    # namespace alone would pull every log in the namespace: too noisy for a small model
    assert build_logql({"namespace": "shop", "alertname": "X"}) is None
    assert build_logql({}) is None


def test_logql_escapes_label_values():
    q = build_logql({"service": 'we"ird\\name'})
    assert '{service="we\\"ird\\\\name"}' in q


def test_extract_lines_newest_first_and_collapses_repeats(loki_response):
    lines = extract_lines(loki_response, max_lines=10, max_chars=10_000)
    assert lines[0].endswith("(x2)")
    assert "payment call failed" in lines[0]
    assert "retrying payment" in lines[1]
    assert len(lines) == 2


def test_extract_lines_respects_limits(loki_response):
    assert len(extract_lines(loki_response, max_lines=1, max_chars=10_000)) == 1
    lines = extract_lines(loki_response, max_lines=10, max_chars=60)
    assert sum(len(line) for line in lines) <= 60


def test_extract_lines_handles_garbage():
    assert extract_lines({}, max_lines=5, max_chars=100) == []
    assert extract_lines({"data": {"result": None}}, max_lines=5, max_chars=100) == []


@respx.mock
async def test_fetch_logs_queries_window_around_alert(settings, checkout_alert, loki_response):
    route = respx.get("http://loki.test/loki/api/v1/query_range").mock(
        return_value=httpx.Response(200, json=loki_response)
    )
    async with httpx.AsyncClient() as client:
        lines = await fetch_logs(client, checkout_alert, settings)
    assert len(lines) == 2
    params = route.calls.last.request.url.params
    fired = int(datetime(2026, 10, 29, 10, 0, tzinfo=UTC).timestamp())
    start_ns = (fired - 600) * 10**9  # 10 minutes before the alert started
    end_ns = (fired + 60) * 10**9  # 1 minute after
    assert params["start"] == str(start_ns)
    assert params["end"] == str(end_ns)
    assert params["direction"] == "backward"


@respx.mock
async def test_fetch_logs_returns_empty_on_error(settings, checkout_alert):
    respx.get("http://loki.test/loki/api/v1/query_range").mock(return_value=httpx.Response(500))
    async with httpx.AsyncClient() as client:
        assert await fetch_logs(client, checkout_alert, settings) == []


@respx.mock
async def test_fetch_logs_returns_empty_on_timeout(settings, checkout_alert):
    respx.get("http://loki.test/loki/api/v1/query_range").mock(side_effect=httpx.ReadTimeout("slow"))
    async with httpx.AsyncClient() as client:
        assert await fetch_logs(client, checkout_alert, settings) == []
