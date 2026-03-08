import json
import os
import uuid
from datetime import datetime, timezone

import boto3
import psycopg2
from botocore.config import Config
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

app = FastAPI(title="order-api", version="0.2.0")

AWS_REGION = os.getenv("AWS_REGION", "us-east-1")
AWS_ENDPOINT_URL = os.getenv("AWS_ENDPOINT_URL", "http://localhost:4566")
TOPIC_NAME = os.getenv("TOPIC_NAME", "orders-topic")
DATABASE_URL = os.getenv("DATABASE_URL", "postgresql://app:app@postgres:5432/orders")


class OrderRequest(BaseModel):
    customer_id: str = Field(min_length=1)
    sku: str = Field(min_length=1)
    quantity: int = Field(gt=0)


def get_sns_client():
    return boto3.client(
        "sns",
        region_name=AWS_REGION,
        endpoint_url=AWS_ENDPOINT_URL,
        aws_access_key_id=os.getenv("AWS_ACCESS_KEY_ID", "test"),
        aws_secret_access_key=os.getenv("AWS_SECRET_ACCESS_KEY", "test"),
        config=Config(retries={"max_attempts": 3, "mode": "standard"}),
    )


def ensure_topic_arn(client) -> str:
    response = client.create_topic(Name=TOPIC_NAME)
    return response["TopicArn"]


def db_conn():
    return psycopg2.connect(DATABASE_URL)


@app.get("/health")
def health():
    return {"status": "ok", "service": "order-api"}


@app.post("/orders")
def create_order(order: OrderRequest):
    order_id = str(uuid.uuid4())
    event_id = str(uuid.uuid4())

    event = {
        "schemaVersion": 1,
        "eventId": event_id,
        "eventType": "OrderCreated",
        "occurredAt": datetime.now(timezone.utc).isoformat(),
        "orderId": order_id,
        "correlationId": order_id,
        "payload": {
            "customerId": order.customer_id,
            "sku": order.sku,
            "quantity": order.quantity,
        },
    }

    with db_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO orders (order_id, customer_id, sku, quantity, status, last_event_id)
                VALUES (%s, %s, %s, %s, %s, %s)
                """,
                (order_id, order.customer_id, order.sku, order.quantity, "PENDING", event_id),
            )

    sns = get_sns_client()
    topic_arn = ensure_topic_arn(sns)
    result = sns.publish(
        TopicArn=topic_arn,
        Message=json.dumps(event),
        MessageAttributes={
            "eventType": {"DataType": "String", "StringValue": event["eventType"]}
        },
    )

    return {
        "orderId": order_id,
        "eventId": event_id,
        "status": "PENDING",
        "snsMessageId": result.get("MessageId"),
    }


@app.get("/orders/{order_id}")
def get_order(order_id: str):
    with db_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT order_id::text, customer_id, sku, quantity, status, last_event_id,
                       created_at, updated_at
                FROM orders
                WHERE order_id = %s
                """,
                (order_id,),
            )
            row = cur.fetchone()

    if not row:
        raise HTTPException(status_code=404, detail="order not found")

    return {
        "orderId": row[0],
        "customerId": row[1],
        "sku": row[2],
        "quantity": row[3],
        "status": row[4],
        "lastEventId": row[5],
        "createdAt": row[6].isoformat(),
        "updatedAt": row[7].isoformat(),
    }
