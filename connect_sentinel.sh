#!/usr/bin/env bash
# Bridge the target environment into Sentinel.
#
# What this does:
#   1. Verifies target services are running
#   2. Verifies Sentinel dashboard is running
#   3. Starts the sentinel_pipeline.py (Kinesis poller → validator + log-analyzer)
#   4. Starts a local AlertManager webhook receiver on port 8502
#      (AlertManager in Docker forwards to this, which puts events on Kinesis)
#   5. Pushes the Sentinel Grafana dashboard into the existing Grafana
#
# Usage: ./connect_sentinel.sh
set -euo pipefail

cd "$(dirname "$0")"
R='\033[0m'; G='\033[0;32m'; B='\033[0;36m'; Y='\033[1;33m'; BOLD='\033[1m'; RED='\033[0;31m'
ok()   { echo -e "  ${G}✓${R}  $*"; }
info() { echo -e "  ${B}→${R}  $*"; }
warn() { echo -e "  ${Y}!${R}  $*"; }
die()  { echo -e "  ${RED}✗${R}  $*" >&2; exit 1; }
step() { echo -e "\n${BOLD}${B}── $* ${R}"; }

clear
echo -e "${BOLD}${B}"
echo "  ┌──────────────────────────────────────────────────────┐"
echo "  │   Connecting Sentinel ↔ Target Environment           │"
echo "  └──────────────────────────────────────────────────────┘"
echo -e "${R}"
sleep 0.3

# ── Check prerequisites ───────────────────────────────────────────────────────
step "Checking prerequisites"

for port_name in "5001:auth-service" "5002:payments-service" "5003:search-api"; do
  port="${port_name%%:*}"; name="${port_name##*:}"
  curl -sf "http://localhost:${port}/health" &>/dev/null \
    && ok "$name (:$port)" \
    || die "$name not running. Run: ./start_target.sh"
done

curl -sf "http://localhost:8501/health" &>/dev/null \
  && ok "Sentinel dashboard (:8501)" \
  || die "Sentinel not running. Run: ./setup_demo.sh"

curl -sf "http://localhost:4566/_localstack/health" &>/dev/null \
  && ok "Floci (:4566)" \
  || die "Floci not running. Start it with Docker."

# ── Start AlertManager webhook receiver ──────────────────────────────────────
step "Starting AlertManager → Kinesis bridge (port 8502)"

fuser -k 8502/tcp &>/dev/null || true
sleep 0.3

python3 - &>/tmp/sentinel-alertbridge.log & cat <<'PYEOF' > /tmp/sentinel_alertbridge.py
#!/usr/bin/env python3
"""
Minimal Flask server that receives AlertManager webhooks and
puts them onto Kinesis sentinel-alerts — exactly what the loki-bridge Lambda does in production.
"""
import json, uuid, boto3
from datetime import datetime, timezone
from flask import Flask, request, jsonify

FLOCI = dict(endpoint_url="http://localhost:4566", region_name="us-east-1",
             aws_access_key_id="test", aws_secret_access_key="test")

app = Flask(__name__)

@app.post("/alertmanager/webhook")
def webhook():
    data = request.get_json(silent=True) or {}
    kc = boto3.client("kinesis", **FLOCI)
    forwarded = 0
    for alert in data.get("alerts", []):
        if alert.get("status") != "firing":
            continue
        labels = alert.get("labels", {})
        svc = labels.get("service") or labels.get("job") or "unknown"
        sev_label = labels.get("severity", "warning")
        severity = "P1" if sev_label == "critical" else "P2" if sev_label == "warning" else "P3"
        payload = {
            "alert_id":        str(uuid.uuid4()),
            "service_name":    svc,
            "severity":        severity,
            "error_rate":      0.6 if severity == "P1" else 0.25 if severity == "P2" else 0.1,
            "health_endpoint": f"http://localhost:500{['auth-service','payments-service','search-api'].index(svc)+1}/health" if svc in ['auth-service','payments-service','search-api'] else "",
            "source":          "alertmanager",
            "fired_at":        datetime.now(timezone.utc).isoformat(),
            "labels":          labels,
            "annotations":     alert.get("annotations", {}),
        }
        kc.put_record(StreamName="sentinel-alerts", Data=json.dumps(payload), PartitionKey=svc)
        forwarded += 1
    return jsonify({"forwarded": forwarded})

@app.get("/health")
def health():
    return jsonify({"status": "ok"})

if __name__ == "__main__":
    print("[alert-bridge] Listening on :8502", flush=True)
    app.run(host="0.0.0.0", port=8502)
PYEOF
python3 /tmp/sentinel_alertbridge.py &>/tmp/sentinel-alertbridge.log &
BRIDGE_PID=$!

sleep 2
curl -sf http://localhost:8502/health &>/dev/null \
  && ok "Alert bridge running (port 8502)" \
  || warn "Alert bridge didn't start — check /tmp/sentinel-alertbridge.log"

# ── Start the pipeline runner ─────────────────────────────────────────────────
step "Starting Sentinel pipeline runner"

# Kill any existing pipeline runner
pkill -f sentinel_pipeline.py &>/dev/null || true
sleep 0.3

KSERVE_ENDPOINT="http://localhost:8081" KSERVE_MODEL="llama3.2:1b" \
  python3 sentinel_pipeline.py &>/tmp/sentinel-pipeline.log &
PIPELINE_PID=$!
sleep 2
ok "Pipeline runner started (PID $PIPELINE_PID)"
ok "Logs: tail -f /tmp/sentinel-pipeline.log"

# ── Push Sentinel dashboard into Grafana ──────────────────────────────────────
step "Importing Sentinel dashboard into Grafana"

if curl -sf "http://localhost:3000/api/health" &>/dev/null; then
  python3 - <<'PYEOF'
import json, requests

GRAFANA = "http://localhost:3000"
AUTH    = ("admin", "sentinel")

# Ensure folder exists
r = requests.post(f"{GRAFANA}/api/folders", auth=AUTH,
                  json={"title": "Sentinel", "uid": "sentinel-folder"})

# Push the Sentinel incidents dashboard
dash_path = "observability/grafana/dashboards/sentinel-incidents.json"
try:
    dash = json.loads(open(dash_path).read())
    dash.pop("id", None)
    dash.pop("version", None)
    r = requests.post(f"{GRAFANA}/api/dashboards/db", auth=AUTH, json={
        "dashboard": dash,
        "folderUid": "sentinel-folder",
        "overwrite": True,
    })
    if r.ok:
        uid = r.json().get("uid", "")
        print(f"  \033[92m✓\033[0m  Sentinel dashboard → http://localhost:3000/d/{uid}")
    else:
        print(f"  \033[33m!\033[0m  Dashboard push failed: {r.text[:100]}")
except Exception as e:
    print(f"  \033[33m!\033[0m  Could not push dashboard: {e}")
PYEOF
else
  warn "Grafana not running — skipping dashboard push"
fi

# ── Done ──────────────────────────────────────────────────────────────────────
echo ""
echo -e "${BOLD}${G}"
echo "  ┌─────────────────────────────────────────────────────────────┐"
echo "  │   Sentinel is connected to the target environment!          │"
echo "  │                                                             │"
echo "  │   Now inject chaos:                                         │"
echo "  │     python3 chaos_monkey.py                  # auto mode   │"
echo "  │     python3 chaos_monkey.py --service auth-service --severity P1  │"
echo "  │                                                             │"
echo "  │   Watch Sentinel respond:                                   │"
echo "  │     Dashboard:  http://localhost:8501                       │"
echo "  │     Pipeline:   tail -f /tmp/sentinel-pipeline.log          │"
echo "  │     Grafana:    http://localhost:3000  (admin/sentinel)     │"
echo "  │                                                             │"
echo "  │   Restore services:                                         │"
echo "  │     python3 chaos_monkey.py --heal                          │"
echo "  └─────────────────────────────────────────────────────────────┘"
echo -e "${R}"

# Tail pipeline log so user can see live activity
echo -e "${B}  Pipeline activity (Ctrl+C to stop tailing)${R}\n"
tail -f /tmp/sentinel-pipeline.log
