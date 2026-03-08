import json
import os
import time
import uuid
from datetime import datetime, timezone

import boto3
import psycopg2

AWS_REGION = os.getenv("AWS_REGION", "us-east-1")
AWS_ENDPOINT_URL = os.getenv("AWS_ENDPOINT_URL", "http://localhost:4566")
QUEUE_NAME = os.getenv("ORDER_QUEUE_NAME", "order-created-queue")
TOPIC_NAME = os.getenv("TOPIC_NAME", "orders-topic")
DATABASE_URL = os.getenv("DATABASE_URL", "postgresql://app:app@postgres:5432/orders")
HANDLER_NAME = "inventory-worker"


def sqs_client():
    return boto3.client(
        "sqs",
        region_name=AWS_REGION,
        endpoint_url=AWS_ENDPOINT_URL,
        aws_access_key_id=os.getenv("AWS_ACCESS_KEY_ID", "test"),
        aws_secret_access_key=os.getenv("AWS_SECRET_ACCESS_KEY", "test"),
    )


def sns_client():
    return boto3.client(
        "sns",
        region_name=AWS_REGION,
        endpoint_url=AWS_ENDPOINT_URL,
        aws_access_key_id=os.getenv("AWS_ACCESS_KEY_ID", "test"),
        aws_secret_access_key=os.getenv("AWS_SECRET_ACCESS_KEY", "test"),
    )


def db_conn():
    return psycopg2.connect(DATABASE_URL)


def resolve_queue_url(client):
    return client.get_queue_url(QueueName=QUEUE_NAME)["QueueUrl"]


def resolve_topic_arn(client):
    return client.create_topic(Name=TOPIC_NAME)["TopicArn"]


def parse_sns_wrapped_message(body: str):
    outer = json.loads(body)
    if "Message" in outer:
        return json.loads(outer["Message"])
    return outer


def publish_decision(sns, topic_arn, order_event, decision, reason):
    decision_event = {
        "schemaVersion": 1,
        "eventId": str(uuid.uuid4()),
        "eventType": decision,
        "occurredAt": datetime.now(timezone.utc).isoformat(),
        "orderId": order_event["orderId"],
        "correlationId": order_event["correlationId"],
        "payload": {
            "sku": order_event["payload"]["sku"],
            "quantity": order_event["payload"]["quantity"],
            "reason": reason,
        },
    }
    sns.publish(
        TopicArn=topic_arn,
        Message=json.dumps(decision_event),
        MessageAttributes={
            "eventType": {"DataType": "String", "StringValue": decision_event["eventType"]}
        },
    )


def handle_order_created(event):
    event_id = event["eventId"]
    sku = event["payload"]["sku"]
    qty = int(event["payload"]["quantity"])

    with db_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT 1 FROM processed_events WHERE event_id = %s AND handler_name = %s",
                (event_id, HANDLER_NAME),
            )
            if cur.fetchone():
                return "DUPLICATE", "already processed"

            cur.execute("SELECT available_qty FROM inventory WHERE sku = %s FOR UPDATE", (sku,))
            row = cur.fetchone()
            if row is None:
                decision, reason = "OrderRejected", "SKU_NOT_FOUND"
            elif row[0] >= qty:
                cur.execute(
                    "UPDATE inventory SET available_qty = available_qty - %s, updated_at = NOW() WHERE sku = %s",
                    (qty, sku),
                )
                decision, reason = "OrderAccepted", "RESERVED"
            else:
                decision, reason = "OrderRejected", "INSUFFICIENT_STOCK"

            cur.execute(
                "INSERT INTO processed_events (event_id, handler_name) VALUES (%s, %s)",
                (event_id, HANDLER_NAME),
            )

    return decision, reason


def run():
    sqs = sqs_client()
    sns = sns_client()

    queue_url = None
    for attempt in range(30):
        try:
            queue_url = resolve_queue_url(sqs)
            break
        except Exception as exc:
            print(f"[inventory-worker] queue not ready ({exc}), retry={attempt + 1}")
            time.sleep(2)

    if not queue_url:
        raise RuntimeError("could not resolve order queue url")

    topic_arn = resolve_topic_arn(sns)
    print(f"[inventory-worker] listening on {QUEUE_NAME} ({queue_url})")

    while True:
        resp = sqs.receive_message(
            QueueUrl=queue_url,
            MaxNumberOfMessages=5,
            WaitTimeSeconds=10,
            VisibilityTimeout=30,
        )

        for msg in resp.get("Messages", []):
            receipt_handle = msg["ReceiptHandle"]
            try:
                event = parse_sns_wrapped_message(msg["Body"])
                if event.get("eventType") != "OrderCreated":
                    sqs.delete_message(QueueUrl=queue_url, ReceiptHandle=receipt_handle)
                    continue

                decision, reason = handle_order_created(event)
                if decision != "DUPLICATE":
                    publish_decision(sns, topic_arn, event, decision, reason)
                print(
                    "[inventory-worker] handled "
                    f"orderId={event.get('orderId')} eventId={event.get('eventId')} decision={decision} reason={reason}"
                )
                sqs.delete_message(QueueUrl=queue_url, ReceiptHandle=receipt_handle)
            except Exception as exc:
                print(f"[inventory-worker] failed to process message: {exc}")


if __name__ == "__main__":
    run()
