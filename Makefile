MODE ?= payment_timeout

.PHONY: test lint up down logs demo-incident recover status

test:
	uv run pytest -q --cov

lint:
	uv run ruff check . && uv run ruff format --check .

up:
	docker compose up -d --build

down:
	docker compose down

logs:
	docker compose logs -f bot

# Put checkout into a failure mode. The alert fires ~40-60s later (rate window + for: 30s + group_wait).
demo-incident:
	@date -u +"incident started %H:%M:%SZ (mode=$(MODE))"
	curl -fsS -X POST "http://localhost:8000/mode?m=$(MODE)" && echo

recover:
	curl -fsS -X POST "http://localhost:8000/mode?m=ok" && echo

status:
	@curl -fsS localhost:9093/api/v2/alerts | python3 -c "import json,sys; [print(a['labels']['alertname'], a['status']['state'], a['labels'].get('service')) for a in json.load(sys.stdin)] or print('no active alerts')"
