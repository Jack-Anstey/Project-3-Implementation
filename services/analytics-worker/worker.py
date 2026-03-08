import json
import os
import time

import boto3
import psycopg2

AWS_REGION = os.getenv("AWS_REGION", "us-east-1")
AWS_ENDPOINT_URL = os.getenv("AWS_ENDPOINT_URL", "http://localhost:4566")
QUEUE_NAME = os.getenv("ANALYTICS_QUEUE_NAME", "analytics-queue")
DATABASE_URL = os.getenv("DATABASE_URL", "postgresql://app:app@postgres:5432/orders")
HANDLER_NAME = "analytics-worker"


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


def ensure_table(conn):
    with conn.cursor() as cur:
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS analytics_event_counts (
              event_type TEXT PRIMARY KEY,
              count BIGINT NOT NULL DEFAULT 0,
              updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
            )
            """
        )


def process_event(event):
    event_id = event.get("eventId")
    event_type = event.get("eventType")
    if not event_id or not event_type:
        return "IGNORED"

    with db_conn() as conn:
        ensure_table(conn)
        with conn.cursor() as cur:
            cur.execute(
                "SELECT 1 FROM processed_events WHERE event_id = %s AND handler_name = %s",
                (event_id, HANDLER_NAME),
            )
            if cur.fetchone():
                return "DUPLICATE"

            cur.execute(
                """
                INSERT INTO analytics_event_counts (event_type, count)
                VALUES (%s, 1)
                ON CONFLICT (event_type)
                DO UPDATE SET count = analytics_event_counts.count + 1,
                              updated_at = NOW()
                """,
                (event_type,),
            )
            cur.execute(
                "INSERT INTO processed_events (event_id, handler_name) VALUES (%s, %s)",
                (event_id, HANDLER_NAME),
            )

    return "COUNTED"


def run():
    sqs = sqs_client()

    queue_url = None
    for attempt in range(30):
        try:
            queue_url = resolve_queue_url(sqs)
            break
        except Exception as exc:
            print(f"[analytics-worker] queue not ready ({exc}), retry={attempt + 1}")
            time.sleep(2)

    if not queue_url:
        raise RuntimeError("could not resolve analytics queue url")

    print(f"[analytics-worker] listening on {QUEUE_NAME} ({queue_url})")

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
                result = process_event(event)
                if result != "IGNORED":
                    print(
                        "[analytics-worker] processed "
                        f"eventType={event.get('eventType')} eventId={event.get('eventId')} result={result}"
                    )
                sqs.delete_message(QueueUrl=queue_url, ReceiptHandle=receipt_handle)
            except Exception as exc:
                print(f"[analytics-worker] failed to process message: {exc}")


if __name__ == "__main__":
    run()
