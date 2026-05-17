#!/usr/bin/env bash
# Start the target environment — the "existing business running on AWS".
#
# What this starts:
#   1. Prometheus + Grafana + Loki + AlertManager (Docker Compose)
#   2. 3 Flask microservices: auth-service, payments-service, search-api
#
# Prerequisites: Docker, Python 3.10+, pip
#
# Usage: ./start_target.sh
set -euo pipefail

cd "$(dirname "$0")"
R='\033[0m'; G='\033[0;32m'; B='\033[0;36m'; Y='\033[1;33m'; BOLD='\033[1m'
ok()   { echo -e "  ${G}✓${R}  $*"; }
info() { echo -e "  ${B}→${R}  $*"; }
warn() { echo -e "  ${Y}!${R}  $*"; }
die()  { echo -e "  \033[0;31m✗${R}  $*" >&2; exit 1; }
step() { echo -e "\n${BOLD}${B}── $* ${R}"; }

clear
echo -e "${BOLD}${B}"
echo "  ┌─────────────────────────────────────────────────┐"
echo "  │   Target Environment — Business Running on AWS  │"
echo "  │                                                 │"
echo "  │   auth-service    → http://localhost:5001       │"
echo "  │   payments-service→ http://localhost:5002       │"
echo "  │   search-api      → http://localhost:5003       │"
echo "  │   Prometheus      → http://localhost:9090       │"
echo "  │   Grafana         → http://localhost:3000       │"
echo "  │   Loki            → http://localhost:3100       │"
echo "  │   AlertManager    → http://localhost:9093       │"
echo "  └─────────────────────────────────────────────────┘"
echo -e "${R}"
sleep 0.5

# ── Step 1: Install Python deps ───────────────────────────────────────────────
step "Installing Python dependencies"
pip install --quiet flask prometheus-client boto3 requests 2>/dev/null || true
ok "flask, prometheus-client, boto3, requests"

# ── Step 2: Docker Compose (observability stack) ──────────────────────────────
step "Starting observability stack (Docker Compose)"

if ! command -v docker &>/dev/null; then
  warn "Docker not found — skipping Prometheus/Grafana/Loki/AlertManager"
  warn "The services will still run; metrics won't be scraped."
else
  cd target
  docker compose up -d 2>/dev/null || docker-compose up -d 2>/dev/null
  cd ..
  ok "Prometheus  → http://localhost:9090"
  ok "Grafana     → http://localhost:3000  (admin / sentinel)"
  ok "Loki        → http://localhost:3100"
  ok "AlertManager→ http://localhost:9093"
fi

# ── Step 3: Start the microservices ──────────────────────────────────────────
step "Starting microservices"

# Kill any existing instances
for port in 5001 5002 5003; do
  fuser -k ${port}/tcp &>/dev/null || true
done
sleep 0.3

PYTHONPATH="$(pwd)/target/services" \
  python3 target/services/auth/app.py    &>/tmp/auth-service.log    &
ok "auth-service      → http://localhost:5001"

PYTHONPATH="$(pwd)/target/services" \
  python3 target/services/payments/app.py &>/tmp/payments-service.log &
ok "payments-service  → http://localhost:5002"

PYTHONPATH="$(pwd)/target/services" \
  python3 target/services/search/app.py  &>/tmp/search-api.log       &
ok "search-api        → http://localhost:5003"

# Wait for services to be ready
info "Waiting for services…"
for port in 5001 5002 5003; do
  for i in $(seq 1 15); do
    curl -sf "http://localhost:${port}/health" &>/dev/null && break
    sleep 1
  done
done

for port in 5001 5002 5003; do
  curl -sf "http://localhost:${port}/health" &>/dev/null \
    && ok "Port $port ready" \
    || warn "Port $port not responding — check /tmp/*.log"
done

# ── Done ──────────────────────────────────────────────────────────────────────
echo ""
echo -e "${BOLD}${G}"
echo "  ┌──────────────────────────────────────────────────────┐"
echo "  │   Target environment is running!                     │"
echo "  │                                                      │"
echo "  │   Next steps:                                        │"
echo "  │   1. ./setup_demo.sh        — start Sentinel         │"
echo "  │   2. ./connect_sentinel.sh  — bridge them together   │"
echo "  │   3. python3 chaos_monkey.py — inject failures       │"
echo "  └──────────────────────────────────────────────────────┘"
echo -e "${R}"

# Keep script alive so services stay up
wait
