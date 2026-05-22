#!/usr/bin/env bash
# Start the Sentinel dashboard.
# Requires: Floci running at localhost:4566
#           python scripts/bootstrap_floci.py  (first time only)
set -e

cd "$(dirname "$0")"

export FLOCI_ENDPOINT="http://localhost:4566"
export AWS_DEFAULT_REGION="us-east-1"
export INCIDENTS_TABLE="sentinel-incidents"
export VALIDATION_RESULTS_TABLE="sentinel-validation-results"
export PR_REVIEWS_TABLE="sentinel-pr-reviews"
export KSERVE_ENDPOINT="${KSERVE_ENDPOINT:-http://localhost:8081}"
export KSERVE_MODEL="${KSERVE_MODEL:-phi3:mini}"

echo ""
echo "  ┌─────────────────────────────────────────┐"
echo "  │   Sentinel Dashboard                    │"
echo "  │   http://localhost:8501                 │"
echo "  └─────────────────────────────────────────┘"
echo ""

uvicorn services.dashboard.api:app --host 0.0.0.0 --port 8501 --reload
