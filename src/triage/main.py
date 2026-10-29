import asyncio
import hmac
import logging
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import httpx
from fastapi import BackgroundTasks, FastAPI, Header, HTTPException, Response, status
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest

from triage import llm, loki, slack
from triage.config import Settings
from triage.dedupe import Deduper
from triage.logging_setup import setup_logging
from triage.metrics import ALERTS_DUPLICATE, ALERTS_PROCESSED, ALERTS_RECEIVED, PROCESSING_SECONDS
from triage.models import Alert, WebhookPayload

log = logging.getLogger("triage")


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or Settings()
    deduper = Deduper(settings.dedupe_ttl_seconds)
    # a CPU-only LLM handles one request at a time well; queue the rest instead of thrashing
    llm_slots = asyncio.Semaphore(settings.llm_concurrency)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        async with httpx.AsyncClient() as client:
            app.state.client = client
            yield

    app = FastAPI(title="alert-triage-bot", lifespan=lifespan)

    async def process(alert: Alert, received: float) -> None:
        client: httpx.AsyncClient = app.state.client
        outcome = "ok"
        try:
            if alert.status == "resolved":
                message = slack.format_resolved(alert)
            else:
                lines = await loki.fetch_logs(client, alert, settings)
                cause, ai_error = None, None
                async with llm_slots:
                    try:
                        cause = await llm.suggest_cause(client, alert, lines, settings)
                    except llm.LLMError as exc:
                        ai_error, outcome = str(exc), "degraded"
                if not lines and outcome == "ok":
                    outcome = "no_logs"
                message = slack.format_firing(alert, lines, cause, ai_error, settings)
            await slack.send(client, message, settings)
        except slack.SlackError as exc:
            outcome = "slack_failed"
            log.error("slack post failed", extra={"alert": alert.name, "error": str(exc)})
        except Exception:
            outcome = "error"
            log.exception("unexpected error processing alert", extra={"alert": alert.name})
        finally:
            elapsed = time.perf_counter() - received
            ALERTS_PROCESSED.labels(status=alert.status, outcome=outcome).inc()
            PROCESSING_SECONDS.observe(elapsed)
            log.info(
                "alert processed",
                extra={
                    "alert": alert.name,
                    "fingerprint": alert.fingerprint,
                    "status": alert.status,
                    "outcome": outcome,
                    "seconds": round(elapsed, 2),
                },
            )

    @app.post("/webhook/alertmanager", status_code=status.HTTP_202_ACCEPTED)
    async def alertmanager_webhook(
        payload: WebhookPayload,
        background: BackgroundTasks,
        authorization: str | None = Header(default=None),
    ) -> dict[str, int]:
        if settings.webhook_token is not None:
            expected = f"Bearer {settings.webhook_token.get_secret_value()}"
            if not hmac.compare_digest(authorization or "", expected):
                raise HTTPException(status_code=401, detail="invalid token")

        received = time.perf_counter()
        accepted = duplicates = 0
        for alert in payload.alerts:
            ALERTS_RECEIVED.labels(status=alert.status).inc()
            if deduper.is_duplicate(alert.fingerprint, alert.status):
                ALERTS_DUPLICATE.inc()
                duplicates += 1
                continue
            accepted += 1
            # runs after the response is sent: Alertmanager gets its 202 immediately
            background.add_task(process, alert, received)
        return {"accepted": accepted, "duplicates": duplicates}

    @app.get("/healthz")
    async def healthz() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/readyz")
    async def readyz() -> dict[str, str]:
        if getattr(app.state, "client", None) is None:
            raise HTTPException(status_code=503, detail="starting")
        return {"status": "ready"}

    @app.get("/metrics")
    async def metrics() -> Response:
        return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)

    return app


def run() -> None:
    import uvicorn

    settings = Settings()
    setup_logging(settings.log_level)
    uvicorn.run(create_app(settings), host="0.0.0.0", port=8080, log_config=None, access_log=False)
