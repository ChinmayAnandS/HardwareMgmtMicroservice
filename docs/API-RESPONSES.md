# API response guide

What every endpoint returns, and what each field means. For authentication, permissions and request numbers see [SERVICE-API.md](SERVICE-API.md).

Everything is JSON. Timestamps are ISO 8601 in UTC, e.g. `2026-11-04T15:30:00+00:00`.

## 1. Read this first: the three stock numbers

Every hardware record is a **pool** of identical units. Three counters describe it:

| Field | Meaning | Plain English |
|---|---|---|
| `total` | Units owned | Everything we have in this pool |
| `reserved` | Units allocated to projects | Promised to projects, whether or not anyone has taken them yet |
| `out` | Units checked out right now | Physically **in someone's hands** (checked out and not yet returned) |

The rule the service enforces on every change: **`0 <= out <= reserved <= total`**.

> **`out` does not mean "out of stock".** It means "currently checked out". A pool with `total: 10, out: 10` is fully lent out; a pool with `total: 10, out: 0` has nothing lent out.

```
total ───────────────────────────────────────────────┐
  reserved (allocated to projects) ──────────┐       │
    out (checked out right now) ───┐         │       │
                                   ▼         ▼       ▼
  |████████████████████████|░░░░░░░░░░░░░|▒▒▒▒▒▒▒▒▒▒▒▒|
   checked out (out)        reserved,     not allocated
                            still on      to any project
                            the shelf     (unallocated)
```

### Derived fields (added by some endpoints)

Only `GET /api/hardware/{id}` and `GET /api/hardware/{id}/availability` add these. They are **not** in list responses or in the responses to create, edit, allocate, etc.

| Field | Formula | Meaning |
|---|---|---|
| `available` | `total - out` | Units **not currently checked out** (on the shelf). Includes units reserved for other projects, so it is **not** the number a given project can check out. |
| `unallocated` | `total - reserved` | Units **not promised to any project**. This is how many more you can allocate. |

### "How many can project X check out?"

Use the project's allocation, not `available`:

```
can check out now = allocation.quantity - allocation.out
```

Get the allocation from `GET /api/allocations?project_id=X&hardware_id=Y`. A check-out larger than this fails with 409 `Insufficient project stock`.

## 2. Record shapes

### Hardware

```json
{
  "_id": "7d0c2a9e-4c3b-4f1e-9a55-0e6d2f4b8a10",
  "name": "ThinkPad T14",
  "category": "Laptop",
  "description": "14-inch, 16 GB",
  "asset_tag": "LT-0042",
  "serial": "PF3XYZ",
  "status": "active",
  "total": 10,
  "reserved": 3,
  "out": 1,
  "version": 4,
  "name_key": "thinkpad t14",
  "asset_key": "lt-0042"
}
```

| Field | Notes |
|---|---|
| `_id` | The hardware id. Use it as `hardware_id` everywhere. |
| `status` | `active`, `damaged`, `under_maintenance` or `retired`. Only `active` stock can be allocated or checked out. `retired` is permanent. |
| `version` | Starts at 1 and goes up by 1 on **every** change to this pool, including allocations, check-outs and returns. Send it back as `expected_version` to detect stale data. |
| `name_key`, `asset_key` | Lower-case copies used for searching. Ignore them. |

### Allocation (a project's share of a pool)

```json
{
  "_id": "7d0c2a9e-...:proj-1",
  "hardware_id": "7d0c2a9e-...",
  "project_id": "proj-1",
  "quantity": 3,
  "out": 1,
  "version": 3
}
```

| Field | Notes |
|---|---|
| `_id` | Always `<hardware_id>:<project_id>`. |
| `quantity` | Units reserved for this project. |
| `out` | Of those, how many are checked out right now. Never more than `quantity`. |
| `version` | Goes up by 1 on each allocate, check-out and return for this project. |

### Checkout

```json
{
  "_id": "b51f0c1d-...",
  "project_id": "proj-1",
  "hardware_id": "7d0c2a9e-...",
  "user_id": "student-7",
  "quantity": 2,
  "returned": 1,
  "version": 2,
  "created_at": "2026-11-04T15:30:00+00:00"
}
```

| Field | Notes |
|---|---|
| `_id` | The checkout id. Use it in `/api/checkouts/{checkout_id}/returns`. |
| `quantity` | Units lent in this checkout. Never changes. |
| `returned` | Units given back so far (a running total, not the last return). |
| outstanding | Not a field. Work it out as `quantity - returned`. The checkout is fully closed when `returned == quantity`. |

The check-out `reason` is recorded in the audit log, not on the checkout.

## 3. What each endpoint returns

The most common source of confusion: **the response is not always the thing named in the URL.**

| Endpoint | Status | Returns |
|---|---|---|
| `POST /api/hardware` | 201 | **Hardware** record |
| `GET /api/hardware` | 200 | **List** of hardware (see section 4) |
| `GET /api/hardware/{id}` | 200 | Hardware + `available` + `unallocated` |
| `GET /api/hardware/{id}/availability` | 200 | Hardware + `available` + `unallocated` + `availability` + `alternatives` (section 5) |
| `PATCH /api/hardware/{id}` | 200 | **Hardware** record (after the edit) |
| `POST /api/hardware/{id}/retire` | 200 | **Hardware** record, `status: "retired"` |
| `POST /api/hardware/{id}/adjustments` | 200 | **Hardware** record, `total` changed by `delta` |
| `PUT /api/hardware/{id}/allocations/{project_id}` | 200 | **Hardware** record (not the allocation!) |
| `GET /api/allocations` | 200 | **List** of allocations |
| `POST /api/checkouts` | 201 | **Checkout** record (not the hardware!) |
| `GET /api/checkouts` | 200 | **List** of checkouts |
| `POST /api/checkouts/{id}/returns` | 200 | **Checkout** record, `returned` increased |
| `POST /api/checkouts/{id}/corrections` | 200 | **Checkout** record, `returned` set to the given value |
| `GET /api/transactions` | 200 | **List** of audit events (section 6) |
| `GET /api/summary` | 200 | Totals per status and per project (section 7) |

Things to know:

- **Allocate returns the hardware record.** To see the project's resulting allocation, call `GET /api/allocations?project_id=...&hardware_id=...`. Check the response's `reserved` for the pool-wide effect.
- **Check-out and return return the checkout record, not the hardware.** The pool's new `out` is not in the response; call `GET /api/hardware/{id}` for it.
- **Mutation responses never include `available` or `unallocated`.** Only the two GET endpoints in rows 3 and 4 do.
- **`PUT .../allocations/{project_id}` sets the quantity; it does not add.** `{"quantity": 3}` means "this project has 3 reserved", whether it had 0 or 10 before. `reserved` on the pool moves by the difference.
- **Returns add; corrections set.** `POST .../returns` with `quantity: 1` adds 1 to `returned`. `POST .../corrections` with `returned: 1` sets `returned` to exactly 1.
- **Retrying with the same request number returns the original response.** It is the stored copy from the first call, so its `version` and counters can be older than the current state. It does not run the operation again. A replayed check-out still answers 201.

### Worked example

Start: create a pool of 10 laptops, then run the steps below.

| Step | Call | Response shows | Pool after |
|---|---|---|---|
| 1 | Create `total: 10` | Hardware: `total 10, reserved 0, out 0, version 1` | available 10, unallocated 10 |
| 2 | Allocate 3 to `proj-1` | Hardware: `reserved 3, out 0, version 2` | available 10, unallocated 7 |
| 3 | Check out 2 to `student-7` | Checkout: `quantity 2, returned 0, version 1` | `out 2`, available 8 |
| 4 | Return 1 | Checkout: `quantity 2, returned 1, version 2` | `out 1`, available 9 |

Reading step 3 shows why the pool and the checkout look different: the response is the checkout (`returned: 0` is *its* counter), and the pool's `out: 2` only shows up if you fetch the hardware.

## 4. List responses

`GET /api/hardware`, `/api/allocations`, `/api/checkouts` and `/api/transactions` all use this envelope:

```json
{ "items": [ ... ], "total": 57, "page": 1, "size": 25 }
```

| Field | Meaning |
|---|---|
| `items` | The records on this page. |
| `total` | **Number of matching records across all pages.** This is a record count, **not** the `total` stock field inside each hardware item. |
| `page`, `size` | The page you asked for (default 1 and 25; `size` is at most 100). |

Last page: `page * size >= total`. The maximum offset is 20 000 records; narrow your filters instead of paging that deep.

Defaults worth knowing:

- `GET /api/checkouts` returns **only outstanding checkouts** (`returned < quantity`) unless you pass `outstanding=false`.
- Hardware sorts by name; checkouts and transactions are newest first.
- Hardware items do not include `available` or `unallocated`.

## 5. Availability response

`GET /api/hardware/{id}/availability` is the hardware record plus:

| Field | Meaning |
|---|---|
| `available`, `unallocated` | As in section 1. |
| `availability` | `"available"` if `available > 0`, otherwise `"unavailable"`. Based on `total - out` only, so it can say `available` for a pool that is entirely reserved to other projects. |
| `alternatives` | `[]` when `available > 0`. When nothing is available, up to 10 vendor suggestions from the Azure SQL vendor-spend table. |
| `alternative_lookup_status` | Present only when the lookup failed: `"temporarily_unavailable"`. The rest of the response is still valid. `alternatives` is `[]` in that case. |

Each alternative is a row from the vendor table: `source_unit`, `vendor`, `commodity`, `department`, `business_line`, `quantity`, `unit_price`, `payment_amount`, `monthly_charges`, `category`. These are spend records, not stock, so `quantity` there is not inventory.

## 6. Audit events (`GET /api/transactions`)

Every successful change writes one event. Filters: `project_id`, `hardware_id`, `actor_id`, `user_id`, `operation`, `start`, `end` (timestamps must include a timezone).

```json
{
  "_id": "e1c0...",
  "actor": "team:projects-team",
  "operation": "checkout",
  "entity": "b51f0c1d-...",
  "hardware_id": "7d0c2a9e-...",
  "project_id": "proj-1",
  "user_id": "student-7",
  "reason": "Lab session",
  "timestamp": "2026-11-04T15:30:00+00:00",
  "before": { "hardware": { }, "allocations": [ ], "checkout": null },
  "after":  { "hardware": { }, "allocations": [ ], "checkout": { } },
  "request_id": "9f86d081..."
}
```

| Field | Notes |
|---|---|
| `actor` | Who did it: `team:<name>` for API callers. |
| `operation` | `create`, `edit`, `retire`, `adjust`, `allocate`, `checkout`, `return` or `correct`. |
| `entity` | The id of what the call returned: the **checkout** id for `checkout`, `return` and `correct`, the **hardware** id for everything else. |
| `project_id` | `null` for `create`, `edit`, `retire`, `adjust`. |
| `user_id` | `null` unless the operation involved a checkout. |
| `before` / `after` | Full snapshots. `create` has `before: {}` and `after: {"hardware": ...}`. Other operations hold `hardware`, `allocations` (all of the pool's allocations) and `checkout` (`null` if none). Compare them to see exactly what changed. |
| `request_id` | **Not the request number you sent.** It is a SHA-256 hash of the caller plus your request number, so you cannot search the log for `REQ-1001-checkout` directly. |

## 7. Summary (`GET /api/summary`)

```json
{
  "statuses": [
    { "_id": "active", "records": 12, "total": 140, "out": 18, "reserved": 60 }
  ],
  "projects": [
    { "_id": "proj-1", "quantity": 8, "out": 3 }
  ],
  "project_limit": 100
}
```

- `statuses`: one row per hardware status. `_id` is the **status**. `records` is how many hardware pools have that status; `total`, `out` and `reserved` are the sums across those pools.
- `projects`: one row per project, sorted by id, capped at `project_limit` (100). `_id` is the **project id**. `quantity` is the sum of units reserved for the project across all pools, and `out` is how many of those are checked out.
- Both lists come from one consistent snapshot.

## 8. Errors

Errors use the HTTP status plus a JSON body.

Most errors:

```json
{ "detail": "Insufficient project stock" }
```

Validation errors (422) carry a list instead:

```json
{ "detail": [ { "type": "missing", "loc": ["body", "reason"], "msg": "Field required" } ] }
```

`loc` tells you which field is wrong.

Messages you will meet, and what to do:

| Status | `detail` | Meaning |
|---|---|---|
| 404 | `Entity not found` | No hardware or checkout with that id. |
| 409 | `Stale version; refresh current values` | `expected_version` is out of date. Fetch the record and retry. |
| 409 | `Concurrent change; retry` | Another change landed at the same moment. Retry with the same request number. |
| 409 | `Idempotency key reused for a different request` | The request number was already used with a different body. |
| 409 | `Duplicate asset tag or idempotency key` | Asset tag already exists (on create). |
| 409 | `Insufficient project stock` | Check-out is larger than `allocation.quantity - allocation.out`. |
| 409 | `Project has no allocation` | Check out or return for a project with no allocation on this pool. |
| 409 | `Hardware is not active` / `Only active stock can be allocated` | Pool status is not `active`. |
| 409 | `Return exceeds outstanding quantity` | Return (or correction) would make `returned` exceed `quantity` or go below 0. |
| 409 | `Cannot reopen inactive stock` | A correction that lowers `returned` on non-active stock. |
| 409 | `Stock must satisfy 0 <= out <= reserved <= total` | The change would break the core rule, e.g. lowering `total` below `reserved`, or allocating more than `total`. |
| 409 | `Release allocations before retirement` / `Return all checkouts before changing status` | Set allocations to 0 and return everything first. |
| 409 | `Retired hardware is immutable` / `Already retired` | Retired pools cannot change. |
| 422 | `Send a unique request_id in the body or an Idempotency-Key header` | Missing request number. |
| 422 | `Idempotency-Key header and request_id differ` | Send one, or make them equal. |

401, 403 and 429 are covered in [SERVICE-API.md](SERVICE-API.md).

## 9. Other endpoints

| Endpoint | Returns |
|---|---|
| `GET /health` | `{"status": "alive"}` |
| `GET /ready` | `{"status": "ready"}`, or 503 if the database is not a replica set |
| `GET /api/me` | Signed-in website user (`sub`, `name`, `roles`, `csrf`). Not available to API teams using Basic auth. |
| `GET /api/openapi.json` | The OpenAPI schema |
| `POST /auth/password`, `/auth/dev`, `/auth/logout` | `{"status": "signed_in"}` or `{"status": "signed_out"}` (website only) |
