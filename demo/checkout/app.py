"""Fake 'checkout' service for demos. Generates its own traffic and can be switched into failure modes.

GET  /            one checkout request (fails according to the current mode)
GET  /metrics     Prometheus metrics
POST /mode?m=...  set failure mode: ok | payment_timeout | db_pool | bad_config
"""

import contextlib
import json
import os
import random
import sys
import threading
import time
import urllib.request
from datetime import UTC, datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

PORT = int(os.environ.get("PORT", "8000"))
RPS = float(os.environ.get("SELF_TRAFFIC_RPS", "5"))

MODES = {
    "ok": None,
    "payment_timeout": [
        (
            0.6,
            "error",
            "payment call failed",
            {"err": "context deadline exceeded", "upstream": "payment:8080", "timeout_ms": 2000},
        ),
        (0.3, "warn", "retrying payment", {"attempt": 3, "upstream": "payment:8080"}),
    ],
    "db_pool": [
        (
            0.7,
            "error",
            "could not acquire db connection",
            {"err": "pool exhausted", "pool_size": 10, "in_use": 10, "wait_ms": 5000},
        ),
        (0.2, "warn", "slow query", {"table": "orders", "duration_ms": 4200}),
    ],
    "bad_config": [
        (
            0.9,
            "error",
            "failed to load tax rates",
            {"err": "open /etc/checkout/tax.yaml: no such file or directory"},
        ),
    ],
}

state = {"mode": os.environ.get("MODE", "ok")}
counts: dict[tuple[str, str], int] = {}
lock = threading.Lock()


def log(level: str, msg: str, **fields) -> None:
    entry = {"ts": datetime.now(UTC).isoformat(), "level": level, "service": "checkout", "msg": msg, **fields}
    print(json.dumps(entry), file=sys.stdout, flush=True)


def handle_checkout() -> int:
    plan = MODES.get(state["mode"])
    if plan:
        for prob, level, msg, fields in plan:
            if random.random() < prob:
                log(level, msg, **fields)
                if level == "error":
                    return 500
    log("info", "order placed", order_id=random.randint(10000, 99999))
    return 200


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args) -> None:  # silence default access log
        pass

    def _reply(self, code: int, body: str, ctype: str = "text/plain") -> None:
        data = body.encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self) -> None:
        path = urlparse(self.path).path
        if path == "/metrics":
            with lock:
                lines = ["# TYPE http_requests_total counter"]
                lines += [f'http_requests_total{{path="{p}",code="{c}"}} {n}' for (p, c), n in counts.items()]
            self._reply(200, "\n".join(lines) + "\n", "text/plain; version=0.0.4")
            return
        if path == "/":
            code = handle_checkout()
            with lock:
                counts[("/", str(code))] = counts.get(("/", str(code)), 0) + 1
            self._reply(code, "ok" if code == 200 else "error")
            return
        self._reply(404, "not found")

    def do_POST(self) -> None:
        url = urlparse(self.path)
        if url.path == "/mode":
            mode = parse_qs(url.query).get("m", ["ok"])[0]
            if mode not in MODES:
                self._reply(400, f"unknown mode, use one of {list(MODES)}")
                return
            state["mode"] = mode
            log("info", "failure mode changed", mode=mode)
            self._reply(200, mode)
            return
        self._reply(404, "not found")


def self_traffic() -> None:
    while True:
        with contextlib.suppress(Exception):
            urllib.request.urlopen(f"http://127.0.0.1:{PORT}/", timeout=2).read()
        time.sleep(1 / RPS)


if __name__ == "__main__":
    threading.Thread(target=self_traffic, daemon=True).start()
    log("info", "checkout started", port=PORT, mode=state["mode"])
    ThreadingHTTPServer(("0.0.0.0", PORT), Handler).serve_forever()
