#!/usr/bin/env bash
# Fire one demo incident and time it: failure starts -> triage message posted.
# Usage: scripts/measure-incident.sh <mode>   (payment_timeout | db_pool | bad_config)
set -euo pipefail
mode=${1:?mode}
cd "$(dirname "$0")/.."

wait_for_no_alerts() {
  for _ in $(seq 1 90); do
    [ "$(curl -fsS localhost:9093/api/v2/alerts?active=true | python3 -c 'import json,sys;print(len(json.load(sys.stdin)))')" = 0 ] && return
    sleep 2
  done
  echo "alerts still active after 3 min" >&2; exit 1
}

curl -fsS -X POST "localhost:8000/mode?m=ok" >/dev/null
wait_for_no_alerts

start=$(date -u +%s.%N)
since=$(date -u +%Y-%m-%dT%H:%M:%SZ)
curl -fsS -X POST "localhost:8000/mode?m=${mode}" >/dev/null

for _ in $(seq 1 120); do
  line=$(docker compose logs bot --since "$since" --no-log-prefix 2>/dev/null \
    | grep '"alert processed"' | grep '"status": "firing"' | head -1 || true)
  [ -n "$line" ] && break
  sleep 1
done
[ -z "$line" ] && { echo "no alert processed within 2 min" >&2; exit 1; }
end=$(date -u +%s.%N)

triage=$(docker compose logs bot --since "$since" --no-log-prefix 2>/dev/null \
  | grep -o '\*AI triage\*[^"]*\\n[^"]*' | head -1 | sed 's/.*_\\n//; s/\\n/ | /g' || true)
bot_s=$(python3 -c "import json,sys; print(json.loads(sys.argv[1])['seconds'])" "$line")
outcome=$(python3 -c "import json,sys; print(json.loads(sys.argv[1])['outcome'])" "$line")
total=$(python3 -c "print(round($end - $start, 1))")

printf '%s\ttotal=%ss\tbot=%ss\toutcome=%s\n%s\n' "$mode" "$total" "$bot_s" "$outcome" "${triage:-<no AI summary>}"
curl -fsS -X POST "localhost:8000/mode?m=ok" >/dev/null
