# WMS REST API contract (ASSUMED — v0)

> **Status: placeholder.** This is the contract the HHT client is coded against until the
> real Spring Boot API spec replaces it. Field names are camelCase (Spring default).
> Base path: `{wms.base_url}` from the device config, e.g. `http://192.168.1.50:8080`.

## Conventions

- `Content-Type: application/json` both ways.
- After login, every request carries `Authorization: Bearer <token>`.
- Errors use HTTP status + body `{"error": "<machine-code>", "message": "<human text>"}`.
  - 4xx = request rejected (shown to operator, **not retried**).
  - 5xx / timeout / connection refused = WMS unavailable (**retried**, device goes OFFLINE).
- Timestamps are ISO-8601 UTC (`2026-07-11T09:30:00Z`).

## QR / barcode payload conventions

| Entity | Payload | Example |
|---|---|---|
| Operator badge | `OP:<operatorId>` | `OP:1001` |
| Location label | `LOC:<locationCode>` | `LOC:A-01-03` |
| Article label | `ART:<code>` or a bare EAN/Code128 | `ART:8412345678905` |

## Endpoints

### POST /api/v1/auth/login

```json
{"deviceId": "HHT-001", "method": "badge", "operatorId": "1001"}
{"deviceId": "HHT-001", "method": "pin", "pin": "1234"}
```

`200` → `{"token": "…", "operatorId": "1001", "operatorName": "Alice"}`
`401` → unknown badge / wrong PIN.

### GET /api/v1/tasks/next?deviceId=HHT-001

`200` →

```json
{
  "taskId": "T-2026-000123",
  "locationCode": "A-01-03",
  "article": {"code": "8412345678905", "sku": "SKU-4711", "description": "Blue T-Shirt M"},
  "qtyRequested": 3
}
```

`204` → no task available.

### POST /api/v1/tasks/{taskId}/scan-location · /scan-article

```json
{"code": "A-01-03", "scannedAt": "2026-07-11T09:30:00Z"}
```

`200` → `{"valid": true, "message": ""}`

**Design note:** the task payload already carries the expected location/article codes, so
the device validates scans **locally** (works offline). These endpoints are best-effort
progress telemetry when online; a failure here never blocks the operator.

### POST /api/v1/tasks/{taskId}/confirm

Header `Idempotency-Key: <uuid>` (server must de-duplicate — the offline queue may retry).

```json
{
  "idempotencyKey": "9f1c…",
  "deviceId": "HHT-001",
  "operatorId": "1001",
  "taskId": "T-2026-000123",
  "locationCode": "A-01-03",
  "articleCode": "8412345678905",
  "qtyRequested": 3,
  "qtyPicked": 3,
  "shortPick": false,
  "confirmedAt": "2026-07-11T09:31:12Z"
}
```

`200`/`201` → accepted. `409` → duplicate idempotency key: treated as success.
This is the only transactional call; while offline it queues on-device and re-sends FIFO.

### GET /api/v1/health

`200` → connectivity probe (any body). Used to drive the ONLINE/OFFLINE indicator.
