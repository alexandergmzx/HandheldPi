# WMS REST API — client notes (v1)

> **Status: real contract.** The canonical specification lives in the WMS
> repository: `warehouse-management/API.md` (contract version `v1`). This file
> documents only the subset the HHT uses and how the device reacts to it.
> The former "ASSUMED — v0" placeholder contract is retired.

Base URL: `{wms.base_url}` from the device config, e.g. `http://192.168.1.50:8080`.
All `/api/v1` requests and responses are `application/json`, camelCase,
ISO-8601 UTC timestamps. After login every request carries
`Authorization: Bearer <opaque-token>`. The client sends a fresh
`X-Correlation-Id` UUID per request; the server echoes it, so device and
server logs join on it.

## Error contract (RFC 9457 problem+json)

Application errors carry `type`, `title`, `status`, `code`, `correlationId`,
optionally `detail` and safe extensions. Device mapping
(`src/hht/wms/http_client.py`):

| Server response | Raised as | Device behavior |
| --- | --- | --- |
| 5xx, timeout, connection refused | `WmsUnavailable` | go OFFLINE; queued ops stay pending and retry |
| `401 INVALID_TOKEN / TOKEN_EXPIRED / TOKEN_REVOKED` | `WmsAuthError` | drop to login, **keep the queue**; resume flush after re-login |
| other 4xx | `WmsRejected(status, code)` | shown to operator / dead-letters the op chain (see below); never blindly retried |

**v1 changes the meaning of 409 on confirm:** a retried confirm with the same
`confirmationId` and quantity returns **200** with the original result, so any
409 (`CONFIRMATION_ID_REUSED`, `INVALID_TASK_STATE`, `TASK_NOT_ASSIGNED_TO_USER`,
`INSUFFICIENT_STOCK`) is a real failure — the old v0 "409 = duplicate = success"
shortcut is gone.

## QR / badge payload conventions

| Entity | Payload | Example | Defined by |
| --- | --- | --- | --- |
| Location label | `LOC:<code>` | `LOC:A-01-01` | WMS (exact, case-sensitive) |
| Article label | `ART:<sku>` | `ART:ART-001` | WMS (exact, case-sensitive; **no bare EAN**) |
| Operator badge | `OP:<username>` | `OP:picker02` | **device-side convention only** — the badge supplies the login username; the WMS never sees badges |

The WMS prints location/article labels itself
(`GET /api/v1/admin/labels/...`); operator badges come from
`scripts/make_badge.py` in this repo.

## Endpoints used by the HHT

### `POST /api/v1/auth/login`

```json
{"username": "picker02", "password": "<PIN digits>", "deviceCode": "HHT-PI-01"}
```

`200` → `{token, tokenType, expiresAt, user: {id, username, role}, device: {id, code}}`.
The device must be pre-registered and active in the WMS (`device` table);
login binds the token to this user/device pair. Errors surfaced to the
operator: `401 INVALID_CREDENTIALS`, `403 USER_INACTIVE`, `403 DEVICE_INACTIVE`,
`404 DEVICE_NOT_REGISTERED`, `409 DEVICE_ASSIGNMENT_CONFLICT` ("Device busy").

The badge scan supplies `username`; the PIN pad entry is sent as `password`
(pickers used from the HHT therefore have numeric passwords of exactly
`workflow.pin_length` digits).

### `POST /api/v1/auth/logout`

Revokes the token; `204`, idempotent. Called on operator logout — but **only
when the offline queue is empty**, because queued replay needs the token.

### `GET /api/v1/hht/tasks/next`

Returns the caller's current active task, else atomically claims the next
`AVAILABLE` task in global FIFO order. `200` →

```json
{
  "id": 101, "state": "ASSIGNED", "orderNumber": "DEMO-1001",
  "lineNumber": 1, "taskSequence": 1,
  "location": {"code": "A-01-01"},
  "article": {"sku": "ART-001", "description": "Black basic T-shirt"},
  "quantity": 20, "assignedAt": "2026-07-11T14:24:00Z"
}
```

`204` → no work (device shows NO TASK). `409 TASK_ASSIGNMENT_CONFLICT` →
request again. Because the server hands back the *current active* task, the
device refuses to fetch while queued ops are pending (the active task would be
the still-unsynced pick). The returned `state` is mapped to the matching
screen so an interrupted task resumes where it left off.

### `POST /api/v1/hht/tasks/{taskId}/scan-location` · `/scan-article`

```json
{"qrValue": "LOC:A-01-01"}
```

`200` → `{taskId, state, ..., confirmedAt}` (+ `replayed: true` when the
transition already happened — treated as success). These are **authoritative
state transitions**, required before confirm. Task-level errors
(`409 WRONG_LOCATION / WRONG_ARTICLE / INVALID_TASK_STATE /
TASK_NOT_ASSIGNED_TO_USER`, `404 TASK_NOT_FOUND`) show a banner; state-level
ones drop the task.

### `POST /api/v1/hht/tasks/{taskId}/confirm`

```json
{"confirmationId": "7a3d389f-9150-43ef-90e6-0955ea37d2a7", "quantity": 20}
```

`quantity` must equal the task quantity exactly (`422 QUANTITY_MISMATCH`
otherwise — the device blocks mismatched counts locally and never sends them).
Idempotent by `confirmationId`: the device generates the UUID once, persists it
in the queue, and retries with the same ID after network failures.

### `GET /actuator/health`

Tokenless, outside `/api/v1`; `200` only when app + database are healthy.
Drives the ONLINE/OFFLINE indicator.

## Offline protocol (Level 2 store-and-forward)

Claiming a task requires connectivity. Once a task is claimed the device
validates scans **locally** against the task payload (`LOC:{location.code}`,
`ART:{article.sku}` — the same rule the server applies) and queues the ordered
op sequence `scan-location → scan-article → confirm` in sqlite
(`src/hht/wms/offline_queue.py`). On reconnect the queue replays FIFO; server
replay-safety and confirm idempotency make redelivery harmless.

If the server rejects a replayed op (e.g. an administrator blocked the task
while the device was offline), the op and the rest of its task's chain are
parked as **dead-letter** rows (kept for audit, never silently dropped or
retried forever) and the device enters `SYNC_FAILED`: "see supervisor".
Recovery is administrative, via the WMS dashboard (task block/resume, stock
adjustment). While anything is pending the device shows the queue depth,
refuses to claim the next task, and refuses logout.
