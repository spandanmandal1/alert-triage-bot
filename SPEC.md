# Spec: alert-triage-bot

## Objective
When Prometheus fires an alert, an on-call engineer gets a Slack message that already has
the relevant error logs from Loki and a short "likely cause" written by a self-hosted LLM.
This cuts the first few minutes of every incident, which are usually spent opening Grafana and searching logs.

Portfolio goal: proves Python service work, Alertmanager/Loki integration, a self-hosted LLM,
Helm, and (via project 3) GitLab CI. Hosted on GitLab, mirrored to GitHub.

### Acceptance criteria
1. `POST /webhook/alertmanager` accepts the standard Alertmanager v4 webhook payload and returns
   `202` in under 100 ms. Processing happens in the background, because a CPU LLM takes 10–60 s and
   Alertmanager retries on timeout.
2. Every alert in the batched `alerts[]` array is handled separately.
3. Alerts are de-duplicated by `fingerprint` + `status` for a configurable window (default 1 h).
4. `status: firing`: query Loki `query_range` using the alert's labels (`namespace`, `pod`, `service`/`app`,
   `job`) over `startsAt − 10m … startsAt + 1m`. Keep error/warn lines and cap the result at N lines / N chars
   so it fits a small model's context.
5. Send the alert and logs to an **OpenAI-compatible** `/v1/chat/completions` endpoint. Locally that's Ollama;
   later it's the llama.cpp server from llm-platform-k8s, swapped in with no code change.
   The model gives a likely cause + 2–3 next checks.
6. Post to a Slack incoming webhook: alert name, severity, labels, likely cause, top log lines,
   and links to Grafana/Prometheus.
7. `status: resolved`: post a short "resolved" message. No Loki or LLM call.
8. Degraded paths: if Loki fails or returns nothing, the LLM still runs on the alert alone. If the LLM fails or
   times out, Slack still gets the alert + raw logs + "no AI summary (reason)". Every external call has a timeout.
9. `DRY_RUN=true` prints the Slack message to stdout instead of posting, so no webhook is needed to develop.
10. `/healthz`, `/readyz`, and `/metrics` (Prometheus: alerts received, processed, LLM latency, failures).

### Out of scope (v1)
Slack buttons/threads, other chat tools, storing incidents in a database, auth beyond a shared-secret header.

## Tech Stack
- Python 3.12 (pinned in Dockerfile and `.python-version`), managed with `uv`
- FastAPI + uvicorn, httpx (async), pydantic-settings, prometheus-client
- Tests: pytest, pytest-asyncio, respx (HTTP mocks)
- Lint/format: ruff
- Local stack (docker compose): Prometheus, Alertmanager, Loki, Promtail/Alloy, Grafana,
  Ollama (`qwen2.5:0.5b`), a small **demo app** that can be switched to throw errors, and the bot
- Deploy: Helm chart (validated with `helm lint` + `helm template | kubeconform`; one k3d run at the end)

**Hardware constraint:** the dev machine has 5 GB RAM (~2 GB free). That's why the main dev loop is
docker compose with a 0.5B model, not k3d. Every container gets a memory limit.

## Commands
```
uv sync                                  # install deps
uv run pytest -q                         # tests
uv run ruff check . && uv run ruff format --check .
uv run uvicorn triage.main:app --reload  # run bot alone (DRY_RUN=true)
docker compose up -d                     # full local stack
make demo-incident                       # flip demo app to error mode -> alert fires -> Slack/stdout
helm lint charts/alert-triage-bot && helm template charts/alert-triage-bot | kubeconform -strict
```

## Project Structure
```
src/triage/
  main.py        FastAPI app, routes, background dispatch
  config.py      pydantic-settings (env vars)
  models.py      Alertmanager payload models
  loki.py        LogQL builder + query client
  llm.py         OpenAI-compatible client + prompt
  slack.py       message formatting + sender (dry-run aware)
  dedupe.py      fingerprint cache with TTL
  metrics.py     Prometheus counters/histograms
tests/           unit tests + fixtures/ (real Alertmanager payload samples)
demo/            demo app + compose config (prometheus rules, alertmanager, loki)
charts/alert-triage-bot/
docs/            architecture diagram, incident timing results
Dockerfile, compose.yaml, Makefile, README.md
```

## Code Style
```python
async def fetch_logs(client: httpx.AsyncClient, alert: Alert, settings: Settings) -> list[str]:
    """Return recent error lines for the alert's workload, newest first. Empty list on failure."""
    query = build_logql(alert.labels)
    if query is None:
        return []
    try:
        resp = await client.get("/loki/api/v1/query_range", params={...}, timeout=settings.loki_timeout)
        resp.raise_for_status()
    except httpx.HTTPError as exc:
        log.warning("loki query failed", extra={"alert": alert.labels.get("alertname"), "error": str(exc)})
        return []
    return extract_lines(resp.json(), limit=settings.max_log_lines)
```
Type hints everywhere, small pure functions (easy to test), structured JSON logs, no global HTTP clients.

## Testing Strategy
- Unit: LogQL builder, log truncation, prompt builder, Slack formatter, dedupe. These are pure functions.
- Integration (respx): full webhook → Loki → LLM → Slack path, plus each failure path from criterion 8.
- Fixtures: real Alertmanager payloads (firing, resolved, batched).
- End-to-end (manual, recorded in `docs/`): compose stack + `make demo-incident`, timing how long it takes
  from alert firing to Slack message.
- Target: ≥ 85% line coverage on `src/triage`.

## Boundaries
- **Always:** tests green before commit; memory limits on containers; secrets only via env/K8s Secret.
- **Ask first:** creating the GitLab repo, pushing anywhere, adding a dependency not listed above,
  changing the model choice.
- **Never:** commit webhook URLs or tokens; put made-up numbers in README/resume (only measured ones).

## Success Criteria
- `uv run pytest` green, coverage ≥ 85%, ruff clean.
- `docker compose up` + `make demo-incident` produces a Slack (or dry-run) message with logs + likely cause
  within ~90 s on this laptop.
- Killing Loki or Ollama still produces a message (degraded).
- Helm chart passes lint + kubeconform. One successful k3d install, with a screenshot.
- README: architecture diagram, how to run, sample Slack message screenshot, measured timings.

## Resume bullet (fill only with measured values)
The playbook draft says "cut time to first diagnosis from [X] to [Y] min". We only use that if we measure a
manual baseline (time to find the cause in Grafana by hand) against the bot on the same demo incidents.
Otherwise, use: "Built an alert-triage service that adds Loki log context and a self-hosted-LLM
suggested cause to Alertmanager alerts in Slack, delivering a summary in [measured] s."

## Open Questions
1. Git email for commits: which address is on your GitHub/GitLab account? (The current local one,
   `no_reply@noreply.com`, means commits won't show on your profile.)
2. Slack: do you have a workspace where you can create an incoming webhook? (Not blocking; DRY_RUN works until then.)
3. GitLab account: not created yet. Needed only at push time (end of week).
