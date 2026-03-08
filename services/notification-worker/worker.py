import json
import os
import time

import boto3
import psycopg2

AWS_REGION = os.getenv("AWS_REGION", "us-east-1")
AWS_ENDPOINT_URL = os.getenv("AWS_ENDPOINT_URL", "http://localhost:4566")
QUEUE_NAME = os.getenv("NOTIFICATION_QUEUE_NAME", "notification-queue")
DATABASE_URL = os.getenv("DATABASE_URL", "postgresql://app:app@postgres:5432/orders")
HANDLER_NAME = "notification-worker"


def sqs_client():
    return boto3.client(
        "sqs",
        region_name=AWS_REGION,
        endpoint_url=AWS_ENDPOINT_URL,
        aws_access_key_id=os.getenv("AWS_ACCESS_KEY_ID", "test"),
        aws_secret_access_key=os.getenv("AWS_SECRET_ACCESS_KEY", "test"),
    )


def db_conn():
    return psycopg2.connect(DATABASE_URL)


def resolve_queue_url(client):
    return client.get_queue_url(QueueName=QUEUE_NAME)["QueueUrl"]


def parse_sns_wrapped_message(body: str):
    outer = json.loads(body)
    if "Message" in outer:
        return json.loads(outer["Message"])
    return outer


def handle_notification(event):
    if event["eventType"] not in ("OrderAccepted", "OrderRejected"):
        return "IGNORED"

    with db_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT 1 FROM processed_events WHERE event_id = %s AND handler_name = %s",
                (event["eventId"], HANDLER_NAME),
            )
            if cur.fetchone():
                return "DUPLICATE"

            message = f"Mock notify: order {event['orderId']} is {event['eventType']}"
            cur.execute(
                """
                INSERT INTO notification_log (order_id, event_type, message)
                VALUES (%s, %s, %s)
                """,
                (event["orderId"], event["eventType"], message),
            )
            cur.execute(
                "INSERT INTO processed_events (event_id, handler_name) VALUES (%s, %s)",
                (event["eventId"], HANDLER_NAME),
            )

    return "SENT"


def run():
    sqs = sqs_client()

    queue_url = None
    for attempt in range(30):
        try:
            queue_url = resolve_queue_url(sqs)
            break
        except Exception as exc:
            print(f"[notification-worker] queue not ready ({exc}), retry={attempt + 1}")
            time.sleep(2)

    if not queue_url:
        raise RuntimeError("could not resolve notification queue url")

    print(f"[notification-worker] listening on {QUEUE_NAME} ({queue_url})")

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
                result = handle_notification(event)
                if result != "IGNORED":
                    print(
                        "[notification-worker] processed "
                        f"orderId={event.get('orderId')} eventType={event.get('eventType')} result={result}"
                    )
                sqs.delete_message(QueueUrl=queue_url, ReceiptHandle=receipt_handle)
            except Exception as exc:
                print(f"[notification-worker] failed to process message: {exc}")


if __name__ == "__main__":
    run()
