#!/usr/bin/env bash
# Verifies the request-outcome-accounting invariant documented in
# docs/decisions/request-outcome-accounting.md against a live Prometheus
# instance, after a run's drain has completed (i.e. gateway_async_active_requests
# has settled back to 0 — this script does not itself wait for drain).
#
# Usage: ./scripts/verify-outcome-invariant.sh [prometheus_url]
#   prometheus_url defaults to http://localhost:9091 (docker-compose.yml's
#   host mapping for the prometheus service).
set -euo pipefail

PROM_URL="${1:-http://localhost:9091}"

query() {
    local expr="$1"
    curl -sf --get "${PROM_URL}/api/v1/query" --data-urlencode "query=${expr}" \
        | python3 -c 'import json,sys; d=json.load(sys.stdin); r=d["data"]["result"]; print(r[0]["value"][1] if r else "0")'
}

received=$(query 'gateway_request_received_total')
outcome_sum=$(query 'sum(gateway_request_outcome_total)')
rejected=$(query 'gateway_request_outcome_total{outcome="rejected"}')
async_active=$(query 'gateway_async_active_requests')

echo "gateway_request_received_total       = ${received}"
echo "sum(gateway_request_outcome_total)   = ${outcome_sum}"
echo "gateway_request_outcome_total{rejected} = ${rejected}"
echo "gateway_async_active_requests (should be 0, drained) = ${async_active}"

status=0

# python3 for float-safe comparison (Counters are floats in Prometheus).
if ! python3 -c "import sys; sys.exit(0 if abs(float('${received}') - float('${outcome_sum}')) < 0.5 else 1)"; then
    echo "FAIL: received (${received}) != sum(outcome) (${outcome_sum}) — some request vanished without a terminal outcome"
    status=1
fi

accepted=$(python3 -c "print(float('${received}') - float('${rejected}'))")
non_rejected_outcome_sum=$(python3 -c "print(float('${outcome_sum}') - float('${rejected}'))")
echo "accepted (received - rejected)                 = ${accepted}"
echo "sum(outcome excluding rejected)                 = ${non_rejected_outcome_sum}"

if ! python3 -c "import sys; sys.exit(0 if abs(float('${accepted}') - float('${non_rejected_outcome_sum}')) < 0.5 else 1)"; then
    echo "FAIL: accepted (${accepted}) != sum(outcome excluding rejected) (${non_rejected_outcome_sum})"
    status=1
fi

if ! python3 -c "import sys; sys.exit(0 if abs(float('${async_active}')) < 0.5 else 1)"; then
    echo "WARN: gateway_async_active_requests = ${async_active}, not 0 — run may not have fully drained yet, re-run this script after waiting"
fi

if [ "${status}" -eq 0 ]; then
    echo "PASS: invariant holds"
else
    echo "INVARIANT VIOLATED — this run's results are not trustworthy, re-run"
fi

exit "${status}"
