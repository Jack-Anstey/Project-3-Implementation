# Project 3: API Example

This is the UW CSE P 590: Cloud Applications Project 3 MVP deployment example for Team 5.

## Project Members

- Jack Anstey
- Aaron Huber
- Yogesh Balaje Mahendron
- Tyler Reitz
- Mitali Shenoy
- Javier Contreras Tenorio

## Quick Start

**Prerequisites:** Python 3.12+, pip

1. Install dependencies:
   ```bash
   pip install -r requirements.txt
   ```
2. Start the server:
   ```bash
   python -m src
   ```
3. Open Swagger UI: [http://localhost:8080/order-processing/docs](http://localhost:8080/order-processing/docs)

<details>
<summary>VS Code debugger setup</summary>

Add this to your `.vscode/launch.json`:

```json
{
  "version": "0.2.0",
  "configurations": [
    {
      "name": "Launch API",
      "type": "debugpy",
      "request": "launch",
      "module": "src",
      "justMyCode": true,
      "console": "integratedTerminal"
    }
  ]
}
```

</details>

## Try It: Place Your First Order

Once the server is running, walk through the core flow in three steps:

**1. Check inventory**

```bash
curl http://localhost:8080/order-processing/inventory
```

You'll see the default SKUs: ABC123 (500), DEF456 (300), GHI789 (200), 12345 (100), XYZ (1000).

**2. Place an order**

```bash
curl -X POST http://localhost:8080/order-processing/order-intake \
  -H "Content-Type: application/json" \
  -d '[{"object": "Widget", "sku": "ABC123", "quantity": 2}]'
```

The response includes a `reservation_id` — copy it for the next step. Reservations expire in **3 seconds**, so move quickly!

**3. Confirm the reservation**

```bash
curl -X POST http://localhost:8080/order-processing/inventory/confirm \
  -H "Content-Type: application/json" \
  -d '{"reservation_id": "<paste your reservation_id here>"}'
```

You should get `"status": "confirmed"`. That's the full happy path!

## How It Works

When a client sends `POST /order-intake`:

1. If an `Idempotency-Key` header is present and a cached response exists, the cached result is returned immediately.
2. Each item in the order is validated against inventory; available items receive a soft reservation with a TTL.
3. An `OrderValidated` event is published to the event bus.
4. Subscribers react in parallel: the saga tracker records the new reservation, analytics logs the order, the notification service dispatches a confirmation, and the idempotency store caches the response.
5. The client receives an `OrderResponse` containing accepted/rejected items and a `reservation_id` for downstream confirmation or release.

**Saga state machine** — each reservation moves through these lifecycle states:

```text
RECEIVED → RESERVED / PARTIALLY_RESERVED / REJECTED
                ↓
           NOTIFYING
            ↓      ↓
      CONFIRMED   COMPENSATING → COMPENSATION_COMPLETE
                       (also: → EXPIRED if TTL lapses)
```

## API Endpoints

| Method | Path | Tag | Description |
| ------ | ---- | --- | ----------- |
| GET | `/health` | Health | Application health check |
| POST | `/order-intake` | Orders | Submit orders; validates stock, creates reservations |
| GET | `/saga/{reservation_id}` | Orders | Get saga state and history for a reservation |
| POST | `/inventory/confirm` | Inventory | Confirm a soft reservation after payment |
| POST | `/inventory/release` | Inventory | Release a soft reservation (payment failed/timeout) |
| GET | `/inventory/{sku}` | Inventory | Look up current stock for a single SKU |
| POST | `/notify/order-confirmation` | Notifications | Send an order confirmation notification |
| POST | `/notify/shipping-confirmation` | Notifications | Send a shipping confirmation notification |
| GET | `/inventory` | Diagnostics | Get current stock levels for all SKUs |
| POST | `/inventory/restock` | Diagnostics | Add stock to an existing or new SKU |
| GET | `/notify/log` | Diagnostics | Get all sent notifications |
| GET | `/analytics/summary` | Diagnostics | Aggregate order analytics (counts, acceptance rate) |
| GET | `/analytics/top-skus` | Diagnostics | Most-requested SKUs ranked by quantity |
| GET | `/analytics/trend` | Diagnostics | Order counts bucketed by hour |
| GET | `/analytics/log` | Diagnostics | Raw analytics event log |
| GET | `/retry-queue/pending` | Diagnostics | Pending retry tasks |
| GET | `/retry-queue/dead-letters` | Diagnostics | Permanently failed retry tasks |
| GET | `/saga/all` | Diagnostics | All tracked sagas with status and history |

<details>
<summary><h2>Detailed Endpoint Reference</h2></summary>

> **Base URL:** `/order-processing` when deployed.
> **Default inventory:** ABC123 (500), DEF456 (300), GHI789 (200), 12345 (100), XYZ (1000).
> Soft reservations expire after **3 seconds**.

---

### Orders

#### POST /order-intake

Submit one or more items. Each item is validated against live inventory — available items receive a soft reservation, unavailable items are rejected with a reason. Use `GET /inventory` beforehand to check stock levels.

**Headers**

| Header | Required | Description |
|--------|----------|-------------|
| `Idempotency-Key` | No | Arbitrary string for duplicate request detection. If the same key is sent again, the cached response is returned without creating new reservations. |

**Request body** — `list[Order]`

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `object` | string | — | Display name of the item |
| `sku` | string | — | SKU identifier to look up in inventory |
| `quantity` | int | 1 | Quantity to reserve |

**Status codes**

| Code | Meaning |
|------|---------|
| 200 | All items successfully reserved |
| 207 | Partial fulfillment — some reserved, some rejected |
| 409 | All items rejected (invalid SKU or insufficient stock) |

**Example — 2-item order with mixed result (207)**

```bash
curl -X POST http://localhost:8080/order-processing/order-intake \
  -H "Content-Type: application/json" \
  -H "Idempotency-Key: order-abc-123" \
  -d '[
    {"object": "Widget",  "sku": "ABC123", "quantity": 2},
    {"object": "Gadget",  "sku": "INVALID", "quantity": 1}
  ]'
```

```json
{
  "reservation_id": "a1b2c3d4-...",
  "accepted_items": [
    {"sku": "ABC123", "requested": 2, "reserved": 2, "status": "reserved"}
  ],
  "rejected_items": [
    {"sku": "INVALID", "requested": 1, "reserved": 0, "status": "rejected", "reason": "SKU not found in inventory"}
  ],
  "status": "partial"
}
```

> **Failure states to watch for:**
> - **Invalid SKU** → item rejected with reason `"SKU not found in inventory"`.
> - **Insufficient stock** → item partially accepted with reason `"Only N of M available"`.
> - **Reservation expiry** — reservations expire in **3 seconds**. Call `POST /inventory/confirm` promptly or the held stock is released automatically.

---

#### GET /saga/{reservation_id}

Returns the saga (order lifecycle) state and full transition history for a reservation. Use the `reservation_id` from a `POST /order-intake` response.

**Status codes**

| Code | Meaning |
|------|---------|
| 200 | Saga found |
| 404 | No saga exists for this reservation ID |

**Example — 200 response**

```json
{
  "reservation_id": "a1b2c3d4-...",
  "current_status": "RESERVED",
  "created_at": "2026-03-14T10:00:00Z",
  "updated_at": "2026-03-14T10:00:01Z",
  "history": [
    {"from_status": "RECEIVED", "to_status": "RESERVED", "timestamp": "2026-03-14T10:00:01Z"}
  ],
  "order_count": 2
}
```

---

### Inventory

#### POST /inventory/confirm

Permanently commits a soft reservation. Stock is deducted from available inventory. Must be called within **3 seconds** of the reservation or it will have expired.

**Request body** — `ReservationAction`

| Field | Type | Description |
|-------|------|-------------|
| `reservation_id` | string | UUID from `POST /order-intake` response |

**Status codes**

| Code | Meaning |
|------|---------|
| 200 | Reservation confirmed |
| 404 | Reservation not found or already expired |

**Example — success**

```bash
curl -X POST http://localhost:8080/order-processing/inventory/confirm \
  -H "Content-Type: application/json" \
  -d '{"reservation_id": "a1b2c3d4-..."}'
```

```json
{"reservation_id": "a1b2c3d4-...", "status": "confirmed"}
```

**Example — expired reservation (404)**

```json
{"reservation_id": "a1b2c3d4-...", "status": "not_found"}
```

> **Failure:** If more than 3 seconds have elapsed since the reservation was created, the reservation expires automatically and this endpoint returns 404.

---

#### POST /inventory/release

Cancels a soft reservation and returns held stock to available inventory. Use when payment fails or the customer cancels.

**Request body** — same as `/inventory/confirm`.

**Status codes** — same as `/inventory/confirm` (200 on success, 404 if not found/expired).

**Example — success**

```json
{"reservation_id": "a1b2c3d4-...", "status": "released"}
```

> **Failure:** Same 3-second TTL applies. If the reservation has already expired or been confirmed, this returns 404.

---

#### GET /inventory/{sku}

Returns the current available quantity for a single SKU. Always returns 200 — unknown SKUs return `quantity: 0`.

**Example**

```bash
curl http://localhost:8080/order-processing/inventory/ABC123
```

```json
{"sku": "ABC123", "quantity": 500}
```

---

### Notifications

#### POST /notify/order-confirmation

Sends an order confirmation notification via email or SMS. The `channel` field must be `"email"` or `"sms"` — any other value returns 422.

**Request body** — `OrderNotificationRequest`

| Field | Type | Description |
|-------|------|-------------|
| `recipient` | string | Email address or phone number |
| `channel` | string | `"email"` or `"sms"` |
| `reservation_id` | string | UUID from `POST /order-intake` |
| `items` | list[dict] | Items included in the order |

**Status codes**

| Code | Meaning |
|------|---------|
| 200 | Notification sent |
| 422 | Invalid `channel` value |

**Example**

```bash
curl -X POST http://localhost:8080/order-processing/notify/order-confirmation \
  -H "Content-Type: application/json" \
  -d '{
    "recipient": "user@example.com",
    "channel": "email",
    "reservation_id": "a1b2c3d4-...",
    "items": [{"sku": "ABC123", "quantity": 2}]
  }'
```

```json
{
  "notification_id": "notif-...",
  "status": "sent",
  "channel": "email",
  "event_type": "order_confirmation"
}
```

---

#### POST /notify/shipping-confirmation

Same pattern as order confirmation, but includes a `tracking_number` instead of `items`.

**Request body** — `ShippingNotificationRequest`

| Field | Type | Description |
|-------|------|-------------|
| `recipient` | string | Email address or phone number |
| `channel` | string | `"email"` or `"sms"` |
| `reservation_id` | string | UUID from `POST /order-intake` |
| `tracking_number` | string | Shipment tracking number |

**Status codes** — 200 (sent) or 422 (invalid channel).

---

### Diagnostics

All diagnostic endpoints are **read-only** and always return **200** (except `/inventory/restock` which returns 422 for negative quantity).

| Endpoint | Query Params | Returns |
|----------|-------------|---------|
| `GET /inventory` | — | `{sku: quantity, ...}` |
| `POST /inventory/restock` | — | `{sku, previous_quantity, current_quantity}` (422 if qty < 0) |
| `GET /notify/log` | — | `{notifications: [...], count}` |
| `GET /analytics/summary` | — | `{total_orders, accepted, partial, rejected, acceptance_rate, ...}` |
| `GET /analytics/top-skus` | `limit` (default 5) | `{top_skus: [{sku, total_requested}], limit}` |
| `GET /analytics/trend` | `hours` (default 24) | `{hours_requested, trend: [{hour, order_count}]}` |
| `GET /analytics/log` | — | `{events: [...], count}` |
| `GET /retry-queue/pending` | — | `[{task_id, callable_name, attempts, ...}]` |
| `GET /retry-queue/dead-letters` | — | Same shape as pending |
| `GET /saga/all` | — | `{sagas: [{reservation_id, current_status, history, ...}], count}` |

</details>

## Architecture

An order-processing API built with FastAPI that validates orders against live inventory, tracks order lifecycles with a saga state machine, and publishes domain events for analytics, notifications, and retry handling.

Key modules by responsibility:

- **Core** — `app.py`, `router.py`, `inputs.py`, `responses.py`. The entrypoint (`app.py`) creates a FastAPI instance, attaches the immutable `ROUTER`, and runs uvicorn. `__main__.py` enables `python -m src`.
- **Inventory** — `inventory.py`. In-memory stock ledger with soft reservations, TTL-based expiry, and confirm/release operations.
- **Event System** — `event_bus.py`, `events.py`, `event_handlers.py`. Async publish/subscribe bus that decouples order validation from side-effects (saga tracking, analytics, notifications, idempotency caching).
- **Saga** — `saga.py`. Order lifecycle state machine that tracks each reservation through `reserved → confirmed | released | expired`.
- **Supporting Services** — `notifications.py` (order/shipping confirmation dispatch), `analytics.py` (order counting, top SKUs, hourly trends), `idempotency.py` (header-based duplicate detection), `retry_queue.py` (exponential-backoff task queue with dead-letter support).
- **Middleware** — `middleware.py`. Enforces a 30-second request timeout on all routes.

<details>
<summary>Project file tree</summary>

```text
├── 📁docker
│   └── Dockerfile
├── 📁src
│   ├── 📁scripts
│   │   ├── analytics.py
│   │   ├── event_bus.py
│   │   ├── event_handlers.py
│   │   ├── events.py
│   │   ├── idempotency.py
│   │   ├── inputs.py
│   │   ├── inventory.py
│   │   ├── notifications.py
│   │   ├── responses.py
│   │   ├── retry_queue.py
│   │   ├── router.py
│   │   └── saga.py
│   ├── 📁utils
│   │   ├── custom_logger.py
│   │   └── middleware.py
│   ├── __main__.py
│   └── app.py
├── 📁tests
│   ├── generate_report.py
│   ├── generate_spreadsheet.py
│   ├── load_test_suite.py
│   ├── run_load_test.py
│   ├── synthetic_load.py
│   ├── test_analytics.py
│   ├── test_event_bus.py
│   ├── test_idempotency.py
│   ├── test_inventory.py
│   ├── test_middleware.py
│   ├── test_notifications.py
│   ├── test_retry_queue.py
│   └── test_saga.py
├── .gitignore
├── README.md
└── requirements.txt
```

</details>

## Testing

**Unit tests** — 8 test modules covering inventory, event bus, saga, notifications, analytics, idempotency, retry queue, and middleware. All test dependencies (`pytest`, `pytest-asyncio`, `openpyxl`) are included in `requirements.txt`. Tests must be run from the repository root directory:

```bash
pytest tests/ -v
```

If tests fail with `ModuleNotFoundError`, set your `PYTHONPATH` to the repository root:

```bash
# Unix / Mac
PYTHONPATH=. pytest tests/ -v

# Windows
set PYTHONPATH=. && pytest tests/ -v
```

**Load testing** — The FastAPI server must be running before starting load tests. Start it first (see [Quickstart](#quickstart)), then run the full load test suite with a single command:

```bash
python tests/run_load_test.py --host http://localhost:8080/order-processing
```

This orchestrator runs all of the following scripts in sequence:

- **`load_test_suite.py`** — Locust-based synthetic load that ramps through multiple RPS tiers against the API
- **`generate_report.py`** — Parses Locust CSV output and raw request data into an HTML report and JSON summary
- **`generate_spreadsheet.py`** — Builds an Excel spreadsheet with charts from the test results

Results are saved to a timestamped folder in `reports/` (e.g. `reports/load_test_20260315_140000/`). Open `report.html` in your browser to view the compiled results.

You can adjust the run time with `--run-time` (default: `280s` for 4 RPS tiers).

You can also run each script individually if you only need part of the flow:

```bash
# Run just the Locust load test
locust -f tests/load_test_suite.py --host http://localhost:8080/order-processing --headless --run-time 60s --csv reports/stats

# Generate the HTML report from existing Locust output
python tests/generate_report.py --csv-dir reports/ --host http://localhost:8080/order-processing --output-dir reports/

# Generate the Excel spreadsheet from existing report data
python tests/generate_spreadsheet.py --summary reports/summary.json --locust-csv reports/stats_stats.csv --output reports/results.xlsx
```

## Infrastructure as Code (IAC)

The IAC of this project is relatively simple:

The `docker` directory hosts all of our files needed for containerization, which enables us to specific in our dependency handling before pushing to AWS Lambda (serverless). Only the [Lambda Web Adaptor configurations](https://github.com/awslabs/aws-lambda-web-adapter?tab=readme-ov-file#configurations) remain for a completely deployable IAC app.

Since we are using a specific `ENTRYPOINT` of `python -m src` to start the application, the API is more a traditional web app rather than a start/stop Lambda handler container. For deployment, we'd use the [Lambda Web Adapter](https://github.com/awslabs/aws-lambda-web-adapter) for this specific implementation. We then get all the benefits of Lambda while keeping development simple: pulling the latest version of a given Docker container as needed.
