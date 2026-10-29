#!/usr/bin/env bash
# Fire one demo incident and time it: failure starts -> triage message posted.
# Usage: scripts/measure-incident.sh <mode>   (payment_timeout | db_pool | bad_config)
set -euo pipefail
mode=${1:?mode}
cd "$(dirname "$0")/.."
# always put checkout back to healthy, even if this run fails
trap 'curl -fsS -X POST "localhost:8000/mode?m=ok" >/dev/null || true' EXIT

# Wait until the previous incident is fully closed: no active alert AND the bot has posted "resolved".
# Alertmanager only sends "resolved" on its next group_interval (1m). If the same alert fires again before
# that, Alertmanager sees "still firing, already notified" and sends nothing, so the run would time out.
wait_until_resolved() {
  local since=$1
  for _ in $(seq 1 120); do
    active=$(curl -fsS 'localhost:9093/api/v2/alerts?active=true' | python3 -c 'import json,sys;print(len(json.load(sys.stdin)))')
    last=$(docker compose logs bot --since "$since" --no-log-prefix 2>/dev/null \
      | grep '"alert processed"' | tail -1 | grep -o '"status": "[a-z]*"' || true)
    # done when nothing is active and the bot's latest message was not a firing one
    [ "$active" = 0 ] && [ "$last" != '"status": "firing"' ] && return
    sleep 2
  done
  echo "previous incident not resolved after 4 min" >&2; exit 1
}

recovered_at=$(date -u -d "-10 minutes" +%Y-%m-%dT%H:%M:%SZ)
curl -fsS -X POST "localhost:8000/mode?m=ok" >/dev/null
wait_until_resolved "$recovered_at"

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
