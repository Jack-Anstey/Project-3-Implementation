# project_03 MWV scaffold

## Start

```bash
make up
make bootstrap
```

## API checks

Health:

```bash
curl -sS http://localhost:8000/health
```

Create accepted order (`sku-123` has stock):

```bash
curl -sS -X POST http://localhost:8000/orders \
  -H 'content-type: application/json' \
  -d '{"customer_id":"c-1","sku":"sku-123","quantity":1}'
```

Create rejected order (`sku-low` starts at 0):

```bash
curl -sS -X POST http://localhost:8000/orders \
  -H 'content-type: application/json' \
  -d '{"customer_id":"c-2","sku":"sku-low","quantity":1}'
```

Get order by id:

```bash
curl -sS http://localhost:8000/orders/<ORDER_ID>
```

## Smoke test (recommended)

```bash
make smoke
```

## Worker logs

```bash
docker compose logs --tail=100 inventory-worker order-status-worker notification-worker analytics-worker
```
