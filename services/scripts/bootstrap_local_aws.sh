#!/usr/bin/env bash
set -euo pipefail

AWS_REGION="${AWS_REGION:-us-east-1}"
LOCALSTACK_HOST_PORT="${LOCALSTACK_HOST_PORT:-4566}"
TOPIC_NAME="${TOPIC_NAME:-orders-topic}"
ORDER_QUEUE_NAME="${ORDER_QUEUE_NAME:-order-created-queue}"
DECISION_QUEUE_NAME="${DECISION_QUEUE_NAME:-order-decision-queue}"
NOTIFICATION_QUEUE_NAME="${NOTIFICATION_QUEUE_NAME:-notification-queue}"
ANALYTICS_QUEUE_NAME="${ANALYTICS_QUEUE_NAME:-analytics-queue}"
ORDER_DLQ_NAME="${ORDER_DLQ_NAME:-order-created-dlq}"

if ! command -v docker >/dev/null 2>&1; then
  echo "docker is required" >&2
  exit 1
fi

if ! docker compose ps localstack >/dev/null 2>&1; then
  echo "localstack service not found. Run 'make up' first." >&2
  exit 1
fi

echo "Waiting for LocalStack on http://localhost:${LOCALSTACK_HOST_PORT} ..."
for _ in $(seq 1 60); do
  if curl -fsS "http://localhost:${LOCALSTACK_HOST_PORT}/_localstack/health" >/dev/null 2>&1; then
    break
  fi
  sleep 2
done

awsls() {
  docker compose exec -T localstack awslocal --region "$AWS_REGION" "$@"
}

json_get() {
  local key="$1"
  awk -v k="$key" -F '"' '{for (i = 1; i <= NF; i++) if ($i == k) {print $(i+2); exit}}'
}

echo "Creating topic and queues ..."
TOPIC_ARN="$(awsls sns create-topic --name "$TOPIC_NAME" | json_get TopicArn)"
ORDER_DLQ_URL="$(awsls sqs create-queue --queue-name "$ORDER_DLQ_NAME" | json_get QueueUrl)"
ORDER_DLQ_ARN="$(awsls sqs get-queue-attributes --queue-url "$ORDER_DLQ_URL" --attribute-names QueueArn | json_get QueueArn)"

ORDER_Q_URL="$(awsls sqs create-queue --queue-name "$ORDER_QUEUE_NAME" | json_get QueueUrl)"
DECISION_Q_URL="$(awsls sqs create-queue --queue-name "$DECISION_QUEUE_NAME" | json_get QueueUrl)"
NOTIFICATION_Q_URL="$(awsls sqs create-queue --queue-name "$NOTIFICATION_QUEUE_NAME" | json_get QueueUrl)"
ANALYTICS_Q_URL="$(awsls sqs create-queue --queue-name "$ANALYTICS_QUEUE_NAME" | json_get QueueUrl)"

ORDER_Q_ARN="$(awsls sqs get-queue-attributes --queue-url "$ORDER_Q_URL" --attribute-names QueueArn | json_get QueueArn)"
DECISION_Q_ARN="$(awsls sqs get-queue-attributes --queue-url "$DECISION_Q_URL" --attribute-names QueueArn | json_get QueueArn)"
NOTIFICATION_Q_ARN="$(awsls sqs get-queue-attributes --queue-url "$NOTIFICATION_Q_URL" --attribute-names QueueArn | json_get QueueArn)"
ANALYTICS_Q_ARN="$(awsls sqs get-queue-attributes --queue-url "$ANALYTICS_Q_URL" --attribute-names QueueArn | json_get QueueArn)"

echo "Subscribing queues to topic ..."
awsls sns subscribe --topic-arn "$TOPIC_ARN" --protocol sqs --notification-endpoint "$ORDER_Q_ARN" >/dev/null
awsls sns subscribe --topic-arn "$TOPIC_ARN" --protocol sqs --notification-endpoint "$DECISION_Q_ARN" >/dev/null
awsls sns subscribe --topic-arn "$TOPIC_ARN" --protocol sqs --notification-endpoint "$NOTIFICATION_Q_ARN" >/dev/null
awsls sns subscribe --topic-arn "$TOPIC_ARN" --protocol sqs --notification-endpoint "$ANALYTICS_Q_ARN" >/dev/null

cat <<OUT

Bootstrap complete.
Topic:
  $TOPIC_ARN
Queues:
  order_created:  $ORDER_Q_URL
  order_decision: $DECISION_Q_URL
  notification:   $NOTIFICATION_Q_URL
  analytics:      $ANALYTICS_Q_URL
  order_dlq:      $ORDER_DLQ_URL

OUT
