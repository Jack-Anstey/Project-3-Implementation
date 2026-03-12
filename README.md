# Project 3: API Example

This is the UW CSE P 590: Cloud Applications Project 3 MVP deployment example for Team 5.

## Project Members

- Jack Anstey
- Aaron Huber
- Yogesh Balaje Mahendron
- Tyler Reitz
- Mitali Shenoy
- Javier Contreras Tenorio

## API Architecture

An order-processing API built with FastAPI that validates orders against live inventory, tracks order lifecycles with a saga state machine, and publishes domain events for analytics, notifications, and retry handling.

```text
├── 📁architecture-diagrams
│   ├── architecture-diagram.png
│   └── sequence_diagram.png
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
├── CLAUDE.md
├── README.md
└── requirements.txt
```

Key modules by responsibility:

- **Core** — `app.py`, `router.py`, `inputs.py`, `responses.py`. The entrypoint (`app.py`) creates a FastAPI instance, attaches the immutable `ROUTER`, and runs uvicorn. `__main__.py` enables `python -m src`.
- **Inventory** — `inventory.py`. In-memory stock ledger with soft reservations, TTL-based expiry, and confirm/release operations.
- **Event System** — `event_bus.py`, `events.py`, `event_handlers.py`. Async publish/subscribe bus that decouples order validation from side-effects (saga tracking, analytics, notifications, idempotency caching).
- **Saga** — `saga.py`. Order lifecycle state machine that tracks each reservation through `reserved → confirmed | released | expired`.
- **Supporting Services** — `notifications.py` (order/shipping confirmation dispatch), `analytics.py` (order counting, top SKUs, hourly trends), `idempotency.py` (header-based duplicate detection), `retry_queue.py` (exponential-backoff task queue with dead-letter support).
- **Middleware** — `middleware.py`. Enforces a 30-second request timeout on all routes.

## API Endpoints

| Method | Path | Tag | Description |
|--------|------|-----|-------------|
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

## Order Processing Flow

When a client sends `POST /order-intake`:

1. If an `Idempotency-Key` header is present and a cached response exists, the cached result is returned immediately.
2. Each item in the order is validated against inventory; available items receive a soft reservation with a TTL.
3. An `OrderValidated` event is published to the event bus.
4. Subscribers react in parallel: the saga tracker records the new reservation, analytics logs the order, the notification service dispatches a confirmation, and the idempotency store caches the response.
5. The client receives an `OrderResponse` containing accepted/rejected items and a `reservation_id` for downstream confirmation or release.

## Testing

**Unit tests** — 8 test modules covering inventory, event bus, saga, notifications, analytics, idempotency, retry queue, and middleware:

```bash
pytest tests/ -v
```

**Load testing** — Locust-based synthetic load with orchestration and reporting scripts (`run_load_test.py`, `load_test_suite.py`, `generate_report.py`, `generate_spreadsheet.py`):

```bash
locust -f tests/synthetic_load.py --host=http://localhost:8080/order-processing --users 50 --spawn-rate 5 --run-time 60s --headless
```

You can adjust the number of users, spawn rate, and run time to test other scenarios to see if our asynchronous endpoints can handle them.

## Local Development

To run the application locally, you can simply input `python -m src` in your root directory, or leverage the following `launch.json` for easier debugging:

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

You can then view the live application's `Swagger UI` through [http://localhost:8080/order-processing/docs](http://localhost:8080/order-processing/docs) by default.

## Infrastructure as Code (IAC)

The IAC of this project is relatively simple:

The `docker` directory hosts all of our files needed for containerization, which enables us to specific in our dependency handling before pushing to AWS Lambda (serverless). Only the [Lambda Web Adaptor configurations](https://github.com/awslabs/aws-lambda-web-adapter?tab=readme-ov-file#configurations) remain for a completely deployable IAC app.

Since we are using a specific `ENTRYPOINT` of `python -m src` to start the application, the API is more a traditional web app rather than a start/stop Lambda handler container. For deployment, we'd use the [Lambda Web Adapter](https://github.com/awslabs/aws-lambda-web-adapter) for this specific implementation. We then get all the benefits of Lambda while keeping development simple: pulling the latest version of a given Docker container as needed.

## Architecture Diagrams

See [`architecture-diagrams/architecture-diagram.png`](architecture-diagrams/architecture-diagram.png) and [`architecture-diagrams/sequence_diagram.png`](architecture-diagrams/sequence_diagram.png) for the full system architecture and order sequence flow.
