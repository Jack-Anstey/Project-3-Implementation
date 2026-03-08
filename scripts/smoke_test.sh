#!/usr/bin/env bash
set -euo pipefail

BASE_URL="${BASE_URL:-http://localhost:8000}"

db_count() {
  local event_type="$1"
  docker compose exec -T postgres psql -U app -d orders -At -c \
    "select coalesce((select count from analytics_event_counts where event_type='${event_type}'), 0);" \
    | tr -d '[:space:]'
}

json_field() {
  local json="$1"
  local key="$2"
  echo "$json" | sed -nE "s/.*\"${key}\":\"?([^\",}]+)\"?.*/\1/p"
}

BEFORE_CREATED="$(db_count "OrderCreated")"
BEFORE_ACCEPTED="$(db_count "OrderAccepted")"
BEFORE_REJECTED="$(db_count "OrderRejected")"

wait_for_status() {
  local order_id="$1"
  local expected="$2"

  for _ in $(seq 1 25); do
    local res
    res="$(curl -sS "${BASE_URL}/orders/${order_id}")"
    local status
    status="$(json_field "$res" status)"
    if [[ "$status" == "$expected" ]]; then
      return 0
    fi
    sleep 1
  done

  echo "Timed out waiting for order ${order_id} status=${expected}" >&2
  return 1
}

echo "Submitting accepted-path order..."
A="$(curl -sS -X POST "${BASE_URL}/orders" -H 'content-type: application/json' -d '{"customer_id":"smoke-a","sku":"sku-123","quantity":1}')"
A_ID="$(json_field "$A" orderId)"

echo "Submitting rejected-path order..."
B="$(curl -sS -X POST "${BASE_URL}/orders" -H 'content-type: application/json' -d '{"customer_id":"smoke-b","sku":"sku-low","quantity":1}')"
B_ID="$(json_field "$B" orderId)"

wait_for_status "$A_ID" "ACCEPTED"
wait_for_status "$B_ID" "REJECTED"

echo "Verifying notification records..."
N_COUNT=$(docker compose exec -T postgres psql -U app -d orders -At -c "select count(*) from notification_log where order_id in ('$A_ID','$B_ID');")
if [[ "$N_COUNT" -lt 2 ]]; then
  echo "Expected at least 2 notifications, got $N_COUNT" >&2
  exit 1
fi

echo "Verifying analytics counters..."
ACCEPTED_COUNT="$(db_count "OrderAccepted")"
REJECTED_COUNT="$(db_count "OrderRejected")"
CREATED_COUNT="$(db_count "OrderCreated")"

if [[ -z "$ACCEPTED_COUNT" || -z "$REJECTED_COUNT" || -z "$CREATED_COUNT" ]]; then
  echo "Missing analytics counters" >&2
  exit 1
fi

DELTA_CREATED=$((CREATED_COUNT - BEFORE_CREATED))
DELTA_ACCEPTED=$((ACCEPTED_COUNT - BEFORE_ACCEPTED))
DELTA_REJECTED=$((REJECTED_COUNT - BEFORE_REJECTED))

if [[ "$DELTA_CREATED" -ne 2 || "$DELTA_ACCEPTED" -ne 1 || "$DELTA_REJECTED" -ne 1 ]]; then
  echo "Unexpected analytics deltas: OrderCreated=${DELTA_CREATED} OrderAccepted=${DELTA_ACCEPTED} OrderRejected=${DELTA_REJECTED}" >&2
  exit 1
fi

echo "Smoke test passed"
echo "Accepted order: $A_ID"
echo "Rejected order: $B_ID"
echo "Counters: OrderCreated=$CREATED_COUNT OrderAccepted=$ACCEPTED_COUNT OrderRejected=$REJECTED_COUNT"
echo "Deltas:   OrderCreated=$DELTA_CREATED OrderAccepted=$DELTA_ACCEPTED OrderRejected=$DELTA_REJECTED"
