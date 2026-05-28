#!/usr/bin/env bash
# =============================================================================
#  Sentinel — end-to-end demo
#
#  Runs three scenarios in sequence:
#
#  Scenario 1 — CLI diagnose (no infra, just Ollama)
#    Pipes fake Lambda error logs into  sentinel diagnose
#    and shows the AI RCA in the terminal.
#
#  Scenario 2 — Full automated pipeline (Floci + dashboard)
#    Starts Floci, bootstraps resources, starts the FastAPI dashboard,
#    then fires a realistic incident via POST /api/incidents/receive and
#    polls until the AI RCA is written back to DynamoDB.
#
#  Scenario 3 — CloudWatch alarm receiver simulation
#    Calls the alarm receiver Lambda handler directly to show the full
#    CloudWatch → Sentinel → RCA path without needing real AWS.
#
#  Prerequisites (all present on this machine):
#    - Ollama running at localhost:11434 with llama3.2:1b
#    - Docker (for Floci)
#    - Python 3.10+
# =============================================================================
set -euo pipefail

# ── colours ───────────────────────────────────────────────────────────────────
RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'
CYAN='\033[0;36m'; BOLD='\033[1m'; DIM='\033[2m'; RESET='\033[0m'

ok()    { echo -e "  ${GREEN}✓${RESET}  $*"; }
info()  { echo -e "  ${CYAN}→${RESET}  $*"; }
warn()  { echo -e "  ${YELLOW}!${RESET}  $*"; }
die()   { echo -e "  ${RED}✗${RESET}  $*" >&2; exit 1; }
step()  { echo -e "\n${BOLD}${CYAN}═══ $* ═══${RESET}\n"; }
pause() { echo -e "\n${DIM}  Press Enter to continue...${RESET}"; read -r; }

PROJECT_ROOT="$(cd "$(dirname "$0")" && pwd)"
cd "$PROJECT_ROOT"

export PYTHONPATH="$PROJECT_ROOT"
export FLOCI_ENDPOINT="http://localhost:4566"
export AWS_DEFAULT_REGION="us-east-1"
export AWS_ACCESS_KEY_ID="test"
export AWS_SECRET_ACCESS_KEY="test"
export INCIDENTS_TABLE="sentinel-incidents"
export VALIDATION_RESULTS_TABLE="sentinel-validation-results"
export PR_REVIEWS_TABLE="sentinel-pr-reviews"
export KSERVE_ENDPOINT="http://localhost:8081"
export KSERVE_MODEL="llama3.2:1b"
export CLOUDWATCH_LOG_GROUP_PREFIX="/aws/lambda/"

PIDS=()
cleanup() {
  echo -e "\n${YELLOW}  Stopping demo services...${RESET}"
  for pid in "${PIDS[@]}"; do kill "$pid" 2>/dev/null || true; done
  docker stop sentinel-floci 2>/dev/null || true
}
trap cleanup EXIT INT TERM

# ── helper: wait for HTTP endpoint ───────────────────────────────────────────
wait_for() {
  local url="$1" label="$2" tries=0
  printf "  Waiting for %-30s" "$label"
  until curl -sf "$url" > /dev/null 2>&1; do
    sleep 1; tries=$((tries+1))
    [ $tries -lt 30 ] || { echo "  TIMEOUT"; return 1; }
    printf "."
  done
  echo -e "  ${GREEN}ready${RESET}"
}

# =============================================================================
#  BANNER
# =============================================================================
clear
echo -e "${BOLD}${CYAN}"
cat <<'EOF'
   ███████╗███████╗███╗   ██╗████████╗██╗███╗   ██╗███████╗██╗
   ██╔════╝██╔════╝████╗  ██║╚══██╔══╝██║████╗  ██║██╔════╝██║
   ███████╗█████╗  ██╔██╗ ██║   ██║   ██║██╔██╗ ██║█████╗  ██║
   ╚════██║██╔══╝  ██║╚██╗██║   ██║   ██║██║╚██╗██║██╔══╝  ██║
   ███████║███████╗██║ ╚████║   ██║   ██║██║ ╚████║███████╗███████╗
   ╚══════╝╚══════╝╚═╝  ╚═══╝   ╚═╝   ╚═╝╚═╝  ╚═══╝╚══════╝╚══════╝
EOF
echo -e "${RESET}${DIM}  AI-powered incident response — end-to-end demo${RESET}\n"

echo -e "  This demo runs three scenarios:\n"
echo -e "  ${BOLD}1.${RESET} sentinel diagnose  — CLI RCA from a Lambda log file"
echo -e "  ${BOLD}2.${RESET} Full pipeline       — Floci + dashboard + AI RCA via REST API"
echo -e "  ${BOLD}3.${RESET} CloudWatch alarm    — simulate an alarm firing → auto-RCA\n"

pause

# =============================================================================
#  SCENARIO 1 — CLI diagnose (fastest demo, no infra)
# =============================================================================
step "SCENARIO 1: sentinel diagnose"

# Start the KServe bridge (Ollama proxy) if not running
if ! curl -sf http://localhost:8081/v2/health/live > /dev/null 2>&1; then
  info "Starting KServe bridge (Ollama proxy on :8081)..."
  OLLAMA_MODEL=llama3.2:1b PYTHONPATH="$PROJECT_ROOT" \
    uvicorn server:app \
      --app-dir "$PROJECT_ROOT/services/kserve-local" \
      --host 0.0.0.0 --port 8081 \
      --log-level error &
  PIDS+=($!)
  wait_for "http://localhost:8081/v2/health/live" "KServe bridge (:8081)"
else
  ok "KServe bridge already running"
fi

# Write a realistic Lambda error log
LOG_FILE="/tmp/sentinel_demo_lambda.log"
cat > "$LOG_FILE" <<'LOGEOF'
2026-05-28T10:00:00.123Z START RequestId: a1b2c3d4 Version: $LATEST
2026-05-28T10:00:00.200Z INFO  payment-processor: Processing charge for order_id=ord-9812
2026-05-28T10:00:00.350Z INFO  payment-processor: Connecting to Stripe API endpoint
2026-05-28T10:00:30.400Z ERROR payment-processor: requests.exceptions.Timeout: HTTPSConnectionPool(host='api.stripe.com', port=443): Read timed out. (read timeout=30)
2026-05-28T10:00:30.401Z ERROR payment-processor: Failed to charge card for order ord-9812: timeout after 30s
2026-05-28T10:00:30.402Z ERROR payment-processor: Traceback (most recent call last):
  File "/var/task/gateway.py", line 47, in charge
    resp = requests.post(GATEWAY_URL + "/charges", json=payload, timeout=TIMEOUT)
  File "/opt/python/requests/adapters.py", line 665, in send
    raise ReadTimeout(e, request=request)
requests.exceptions.ReadTimeout: HTTPSConnectionPool(host='api.stripe.com', port=443): Read timed out.
2026-05-28T10:00:30.410Z ERROR payment-processor: DLQ enqueue failed: connection pool exhausted (pool_size=5, overflow=0)
2026-05-28T10:00:30.420Z ERROR payment-processor: Retry 1/3 failed for order ord-9812
2026-05-28T10:00:35.000Z ERROR payment-processor: Retry 2/3 failed: same timeout
2026-05-28T10:00:40.000Z FATAL payment-processor: All retries exhausted for order ord-9812. Charge NOT processed.
2026-05-28T10:00:40.010Z ERROR payment-processor: CloudWatch metric emit failed: connection refused localhost:8125
2026-05-28T10:00:40.020Z END   RequestId: a1b2c3d4
2026-05-28T10:00:40.030Z REPORT RequestId: a1b2c3d4 Duration: 40000ms Billed Duration: 40000ms Memory Size: 512MB Max Memory Used: 489MB Init Duration: 312ms
2026-05-28T10:00:41.000Z START RequestId: b2c3d4e5 Version: $LATEST
2026-05-28T10:00:41.100Z ERROR payment-processor: Timeout connecting to Stripe: 30s exceeded
2026-05-28T10:00:41.200Z FATAL payment-processor: Circuit breaker OPEN — all charge requests rejected
LOGEOF

ok "Created demo Lambda log → $LOG_FILE"
echo ""
echo -e "  ${BOLD}Running:${RESET} sentinel diagnose payment-processor \\"
echo -e "           --from file --log-file $LOG_FILE \\"
echo -e "           --severity P1 --no-code\n"

sleep 1

python3 -c "
from sentinel.cli.main import main
main(['diagnose', 'payment-processor',
      '--from', 'file', '--log-file', '$LOG_FILE',
      '--severity', 'P1', '--no-code'])
"

pause

# Now demonstrate with code context (repo scan)
echo -e "  ${BOLD}With code context${RESET} — scans the repo for relevant source:\n"
echo -e "  ${BOLD}Running:${RESET} sentinel diagnose payments-service \\"
echo -e "           --from file --log-file $LOG_FILE --severity P1\n"

sleep 1

python3 -c "
from sentinel.cli.main import main
main(['diagnose', 'payments-service',
      '--from', 'file', '--log-file', '$LOG_FILE',
      '--severity', 'P1',
      '--repo-path', '$PROJECT_ROOT'])
"

pause

# =============================================================================
#  SCENARIO 2 — Full pipeline with Floci + dashboard
# =============================================================================
step "SCENARIO 2: Full automated pipeline"

# Start Floci
info "Starting Floci (local AWS emulator)..."
docker start sentinel-floci 2>/dev/null || \
  docker run -d --name sentinel-floci \
    -p 4566:4566 \
    -e SERVICES=dynamodb,kinesis,sqs \
    -e DEBUG=1 \
    --health-cmd="curl -f http://localhost:4566/_localstack/health" \
    localstack/localstack:3 2>/dev/null || \
  die "Could not start Floci. Is Docker running?"

wait_for "http://localhost:4566/_localstack/health" "Floci (:4566)"

# Bootstrap resources
info "Bootstrapping DynamoDB tables and Kinesis streams..."
python3 scripts/bootstrap_floci.py 2>&1 | grep -E "✓|–|✗" | head -15

# Start the dashboard
if ! curl -sf http://localhost:8501/health > /dev/null 2>&1; then
  info "Starting Sentinel dashboard on :8501..."
  PYTHONPATH="$PROJECT_ROOT" \
    uvicorn services.dashboard.api:app \
      --host 0.0.0.0 --port 8501 \
      --log-level error &
  PIDS+=($!)
  wait_for "http://localhost:8501/health" "Dashboard (:8501)"
else
  ok "Dashboard already running"
fi

# Fire a real incident via the REST API
echo ""
info "Firing P1 incident: auth-service DB connection pool exhausted..."
echo ""

INCIDENT=$(curl -sf -X POST http://localhost:8501/api/incidents/receive \
  -H "Content-Type: application/json" \
  -d '{
    "service":   "auth-service",
    "severity":  "P1",
    "title":     "DB connection pool exhausted — auth failures spiking",
    "source":    "demo",
    "labels": {
      "alarm_name": "p1-auth-service-error-rate",
      "error_rate": "18%",
      "region":     "us-east-1"
    }
  }' 2>&1)

INCIDENT_ID=$(echo "$INCIDENT" | python3 -c "import sys,json; print(json.load(sys.stdin).get('incident_id','?'))" 2>/dev/null || echo "?")
ok "Incident created: $INCIDENT_ID"
echo -e "  ${DIM}ai_pending=true — RCA running in background...${RESET}\n"

# Poll until RCA is ready (max 60s)
echo -e "  Polling for AI RCA (llama3.2:1b via Ollama)..."
DONE=false
for i in $(seq 1 20); do
  sleep 3
  STATUS=$(curl -sf "http://localhost:8501/api/incidents/$INCIDENT_ID" 2>/dev/null || echo '{}')
  PENDING=$(echo "$STATUS" | python3 -c "import sys,json; print(json.load(sys.stdin).get('ai_pending',True))" 2>/dev/null || echo "true")
  if [ "$PENDING" = "False" ] || [ "$PENDING" = "false" ]; then
    DONE=true
    break
  fi
  printf "  [%2ds] still running...\n" $((i*3))
done

echo ""
if [ "$DONE" = "true" ]; then
  ok "RCA complete. Full incident from API:\n"
  curl -sf "http://localhost:8501/api/incidents/$INCIDENT_ID" | \
    python3 -c "
import sys, json
d = json.load(sys.stdin)
print()
print(f'  Service:       {d.get(\"service_name\", d.get(\"service\", \"-\"))}')
print(f'  Severity:      {d.get(\"severity\", \"-\")}')
print(f'  Status:        {d.get(\"status\", \"-\")}')
print(f'  AI source:     {d.get(\"rca_source\", \"-\")}')
print()
print(f'  Root cause:')
print(f'    {d.get(\"root_cause\", \"-\")}')
print()
rb = d.get('runbook', {})
if rb:
    print('  Runbook:')
    for k in ['step1','step2','step3','step4']:
        v = rb.get(k)
        if v:
            n = k.replace('step','')
            print(f'    {n}. {v}')
print()
"
else
  warn "RCA still pending after 60s (small model may be slow). Check: curl http://localhost:8501/api/incidents/$INCIDENT_ID"
fi

# Also fire a second incident to show the list view
curl -sf -X POST http://localhost:8501/api/incidents/receive \
  -H "Content-Type: application/json" \
  -d '{"service":"payments-service","severity":"P2","title":"Stripe API timeout — payment failures","source":"demo"}' > /dev/null

curl -sf -X POST http://localhost:8501/api/incidents/receive \
  -H "Content-Type: application/json" \
  -d '{"service":"search-api","severity":"P3","title":"Elasticsearch bulk index lag","source":"demo"}' > /dev/null

echo ""
ok "3 incidents created. Dashboard: ${BOLD}http://localhost:8501${RESET}"
info "API stats:"
curl -sf http://localhost:8501/api/stats 2>/dev/null | \
  python3 -c "
import sys, json
s = json.load(sys.stdin)
print(f'    total={s.get(\"total\",0)}  open={s.get(\"open\",0)}  '
      f'p1={s.get(\"by_severity\",{}).get(\"P1\",0)}  '
      f'p2={s.get(\"by_severity\",{}).get(\"P2\",0)}  '
      f'p3={s.get(\"by_severity\",{}).get(\"P3\",0)}')
" 2>/dev/null || echo "    (stats endpoint returned non-JSON)"

pause

# =============================================================================
#  SCENARIO 3 — CloudWatch alarm receiver simulation
# =============================================================================
step "SCENARIO 3: CloudWatch alarm → auto-RCA"

info "Simulating SNS delivery of a CloudWatch alarm state change...\n"

ALARM_JSON='{
  "AlarmName": "p1-auth-service-error-rate",
  "AlarmDescription": "Auth service error rate exceeded 15% for 5 minutes",
  "NewStateValue": "ALARM",
  "OldStateValue": "OK",
  "NewStateReason": "Threshold Crossed: 1 datapoint [18.2 (28/05/26 10:00:00)] was greater than the threshold (15.0).",
  "Region": "us-east-1",
  "Trigger": {
    "Namespace": "AWS/Lambda",
    "MetricName": "Errors",
    "Dimensions": [{"name": "FunctionName", "value": "auth-service"}],
    "Period": 300,
    "Threshold": 15.0
  }
}'

echo -e "  Alarm payload:\n"
echo "$ALARM_JSON" | python3 -m json.tool | sed 's/^/  /'
echo ""

python3 - <<PYEOF
import sys, json, os

sys.path.insert(0, '$PROJECT_ROOT/services/cloudwatch-alarm-receiver')
os.environ.update({
    'SENTINEL_DASHBOARD_URL': 'http://localhost:8501',
    'INCIDENTS_TABLE':        'sentinel-incidents',
    'EVENTS_STREAM':          'sentinel-events',
    'AWS_REGION':             'us-east-1',
    'FLOCI_ENDPOINT':         'http://localhost:4566',
})

from handler import _parse_alarm, lambda_handler

alarm = json.loads('$ALARM_JSON')
service, severity, title = _parse_alarm(alarm)
print(f"  Parsed alarm:  service={service}  severity={severity}")
print(f"  Title:         {title}")
print()

# Simulate the SNS event envelope
event = {
    "Records": [{
        "Sns": {
            "Message": '$ALARM_JSON'
        }
    }]
}

print("  Calling lambda_handler(event, {})...")
result = lambda_handler(event, {})
print(f"  Result: processed={result.get('processed')}  errors={result.get('errors', [])}")
PYEOF

echo ""
ok "Alarm received. Dashboard will show the new incident at http://localhost:8501"

# =============================================================================
#  SUMMARY
# =============================================================================
echo ""
echo -e "${BOLD}${GREEN}"
echo "  ════════════════════════════════════════════"
echo "   Demo complete"
echo "  ════════════════════════════════════════════"
echo -e "${RESET}"
echo -e "  ${BOLD}What you saw:${RESET}"
echo ""
echo -e "  1. ${GREEN}sentinel diagnose${RESET} — AI RCA from a Lambda log file (no infra)"
echo -e "     Try: cat /tmp/sentinel_demo_lambda.log | sentinel diagnose payment-processor --from -"
echo ""
echo -e "  2. ${GREEN}POST /api/incidents/receive${RESET} — full RCA pipeline via REST"
echo -e "     Dashboard: ${BOLD}http://localhost:8501${RESET}"
echo ""
echo -e "  3. ${GREEN}CloudWatch alarm receiver${RESET} — alarm → SNS → Lambda → RCA"
echo -e "     In production: set AlarmActions to the SNS topic ARN from terraform"
echo ""
echo -e "  ${DIM}Services still running (Ctrl+C or re-run demo.sh to stop):${RESET}"
echo -e "  ${DIM}  KServe bridge  http://localhost:8081${RESET}"
echo -e "  ${DIM}  Dashboard      http://localhost:8501${RESET}"
echo -e "  ${DIM}  Floci          http://localhost:4566${RESET}"
echo ""
echo -e "  ${DIM}Press Ctrl+C to stop all services.${RESET}"

# Keep services running so the user can explore
wait
