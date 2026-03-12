# Third Party imports
import json
import os
import random
import time
import uuid

from locust import HttpUser, LoadTestShape, constant_throughput, events, task

# Local Imports
from src.scripts.inputs import Order


# ── RPS-targeted load shape ──────────────────────────────────────────────────
STAGES = [
    {"target_rps": 50, "duration": 60, "ramp": 10},
    {"target_rps": 100, "duration": 60, "ramp": 10},
    {"target_rps": 150, "duration": 60, "ramp": 10},
    {"target_rps": 200, "duration": 60, "ramp": 10},
]


class RPSLoadShape(LoadTestShape):
    """Discrete-tier load shape that targets specific RPS levels.

    Each user fires ~1 req/s via constant_throughput(1), so user count ≈ target RPS.
    """

    def tick(self):
        run_time = self.get_run_time()
        elapsed = 0
        for stage in STAGES:
            stage_end = elapsed + stage["ramp"] + stage["duration"]
            if run_time < stage_end:
                return stage["target_rps"], stage["target_rps"] // 2 or 1
            elapsed = stage_end
        return None


# ── Raw request collector ────────────────────────────────────────────────────
_raw_requests: list[dict] = []


@events.init.add_listener
def on_init(environment, **kwargs):
    """Create the output directory derived from --csv path."""
    csv_prefix = environment.parsed_options.csv_prefix if hasattr(environment.parsed_options, "csv_prefix") else None
    if csv_prefix:
        out_dir = os.path.dirname(csv_prefix)
        if out_dir:
            os.makedirs(out_dir, exist_ok=True)


@events.request.add_listener
def on_request(request_type, name, response_time, response_length, response, exception, context, **kwargs):
    """Capture every request for per-tier analysis."""
    status = 0
    if response is not None:
        status = response.status_code
    _raw_requests.append({
        "timestamp": time.time(),
        "method": request_type,
        "name": name,
        "response_time": response_time,
        "status_code": status,
        "response_length": response_length or 0,
    })


@events.test_stop.add_listener
def on_test_stop(environment, **kwargs):
    """Dump raw requests to JSON alongside the CSVs."""
    csv_prefix = environment.parsed_options.csv_prefix if hasattr(environment.parsed_options, "csv_prefix") else None
    if csv_prefix:
        out_dir = os.path.dirname(csv_prefix)
        out_path = os.path.join(out_dir, "raw_requests.json") if out_dir else "raw_requests.json"
    else:
        out_path = "raw_requests.json"
    with open(out_path, "w") as f:
        json.dump(_raw_requests, f)
    print(f"[load_test_suite] Wrote {len(_raw_requests)} raw requests to {out_path}")


# ── Locust user (same tasks + weights as synthetic_load.py:APIUser) ──────────
class RPSUser(HttpUser):
    wait_time = constant_throughput(1)  # ~1 req/s per user

    @task(1)
    def get_api_status(self):
        self.client.get("/health")

    @task(3)
    def check_inventory(self):
        self.client.get("/inventory")

    @task(10)
    def create_item(self):
        items = [
            Order(object="Synthetic Load", sku="ABC123", quantity=10),
            Order(object="Synthetic Load 1", sku="12345", quantity=1),
            Order(object="Synthetic Load 2", sku="XYZ", quantity=100),
        ]
        with self.client.post(
            url="/order-intake",
            json=[item.model_dump() for item in items],
            catch_response=True,
        ) as resp:
            if resp.status_code in (200, 207, 409):
                resp.success()
            else:
                resp.failure(f"Unexpected status code: {resp.status_code}")

    @task(2)
    def confirm_order_flow(self):
        items = [Order(object="Confirm Test", sku="ABC123", quantity=1).model_dump()]
        with self.client.post(
            url="/order-intake", json=items, catch_response=True
        ) as resp:
            if resp.status_code not in (200, 207, 409):
                resp.failure(f"Unexpected status code: {resp.status_code}")
                return
            resp.success()
        rid = resp.json().get("reservation_id")
        if rid:
            with self.client.post(
                url="/inventory/confirm",
                json={"reservation_id": rid},
                catch_response=True,
            ) as resp:
                if resp.status_code in (200, 404):
                    resp.success()
                else:
                    resp.failure(f"Unexpected status code: {resp.status_code}")

    @task(1)
    def release_order_flow(self):
        items = [Order(object="Release Test", sku="DEF456", quantity=1).model_dump()]
        with self.client.post(
            url="/order-intake", json=items, catch_response=True
        ) as resp:
            if resp.status_code not in (200, 207, 409):
                resp.failure(f"Unexpected status code: {resp.status_code}")
                return
            resp.success()
        rid = resp.json().get("reservation_id")
        if rid:
            with self.client.post(
                url="/inventory/release",
                json={"reservation_id": rid},
                catch_response=True,
            ) as resp:
                if resp.status_code in (200, 404):
                    resp.success()
                else:
                    resp.failure(f"Unexpected status code: {resp.status_code}")

    @task(1)
    def restock_flow(self):
        sku = random.choice(["ABC123", "DEF456", "GHI789", "12345", "XYZ"])
        with self.client.post(
            url="/inventory/restock",
            json={"sku": sku, "quantity": 50},
            catch_response=True,
        ) as resp:
            if resp.status_code == 200:
                resp.success()
            else:
                resp.failure(f"Unexpected status code: {resp.status_code}")

    @task(2)
    def check_single_sku(self):
        sku = random.choice(["ABC123", "DEF456", "GHI789", "12345", "XYZ"])
        self.client.get(f"/inventory/{sku}")

    @task(2)
    def send_order_notification(self):
        channel = random.choice(["email", "sms"])
        with self.client.post(
            url="/notify/order-confirmation",
            json={
                "recipient": f"user-{uuid.uuid4().hex[:8]}@test.com",
                "channel": channel,
                "reservation_id": str(uuid.uuid4()),
                "items": [{"sku": "ABC123", "quantity": 1}],
            },
            catch_response=True,
        ) as resp:
            if resp.status_code == 200:
                resp.success()
            else:
                resp.failure(f"Unexpected status code: {resp.status_code}")

    @task(2)
    def check_analytics_summary(self):
        self.client.get("/analytics/summary")

    @task(1)
    def check_top_skus(self):
        self.client.get("/analytics/top-skus?limit=5")

    @task(1)
    def send_shipping_notification(self):
        channel = random.choice(["email", "sms"])
        with self.client.post(
            url="/notify/shipping-confirmation",
            json={
                "recipient": f"user-{uuid.uuid4().hex[:8]}@test.com",
                "channel": channel,
                "reservation_id": str(uuid.uuid4()),
                "tracking_number": f"TRACK-{uuid.uuid4().hex[:10].upper()}",
            },
            catch_response=True,
        ) as resp:
            if resp.status_code == 200:
                resp.success()
            else:
                resp.failure(f"Unexpected status code: {resp.status_code}")

    @task(1)
    def check_retry_queue(self):
        self.client.get("/retry-queue/pending")

    @task(2)
    def idempotent_order(self):
        key = str(uuid.uuid4())
        items = [Order(object="Idempotent Test", sku="ABC123", quantity=1).model_dump()]
        headers = {"Idempotency-Key": key}
        with self.client.post(
            url="/order-intake", json=items, headers=headers, catch_response=True
        ) as resp:
            if resp.status_code in (200, 207, 409):
                resp.success()
            else:
                resp.failure(f"Unexpected status code: {resp.status_code}")
        with self.client.post(
            url="/order-intake",
            json=items,
            headers=headers,
            catch_response=True,
            name="/order-intake [idempotent replay]",
        ) as resp:
            if resp.status_code in (200, 207, 409):
                resp.success()
            else:
                resp.failure(f"Unexpected status code: {resp.status_code}")
