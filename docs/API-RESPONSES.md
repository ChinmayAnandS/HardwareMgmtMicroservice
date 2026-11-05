# API response guide

What every RPC returns, and what each field means. For credentials, permissions and request numbers see [SERVICE-API.md](SERVICE-API.md). The authoritative definitions are the comments in [`hardware.proto`](../proto/hardware/v1/hardware.proto).

Responses are protobuf messages. Examples below are shown as JSON for readability (that is how `grpcurl` prints them; field names appear in `camelCase` there, e.g. `assetTag`). Two protobuf behaviours to remember:

- **A field that holds its default value (`0`, `""`, `false`) may not be printed by some tools**, but it is always present on the typed message in your client library.
- Timestamps are `google.protobuf.Timestamp` (seconds + nanos since the Unix epoch, UTC). In JSON they print as `2026-11-04T15:30:00Z`.

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

### Derived fields (on every Hardware message)

Every `Hardware` message, whichever RPC returned it, carries two computed numbers:

| Field | Formula | Meaning |
|---|---|---|
| `available` | `total - out` | Units **not currently checked out** (on the shelf). Includes units reserved for other projects, so it is **not** the number a given project can check out. |
| `unallocated` | `total - reserved` | Units **not promised to any project**. This is how many more you can allocate. |

### "How many can project X check out?"

Use the project's allocation, not `available`:

```
can check out now = allocation.quantity - allocation.out
```

Get the allocation from `ListAllocations(project_id=X, hardware_id=Y)`. A check-out larger than this fails with `FAILED_PRECONDITION` (`Insufficient project stock`).

## 2. Record shapes

Identifiers are called **`id`** (the storage layer's `_id`).

### Hardware

```json
{
  "id": "7d0c2a9e-4c3b-4f1e-9a55-0e6d2f4b8a10",
  "name": "ThinkPad T14",
  "category": "Laptop",
  "description": "14-inch, 16 GB",
  "assetTag": "LT-0042",
  "serial": "PF3XYZ",
  "status": "active",
  "total": "10",
  "reserved": "3",
  "out": "1",
  "version": "4",
  "available": "9",
  "unallocated": "7"
}
```

(grpcurl prints 64-bit integers as strings; your client library gives you integers.)

| Field | Notes |
|---|---|
| `id` | The hardware id. Use it as `hardware_id` everywhere. |
| `status` | `active`, `damaged`, `under_maintenance` or `retired`. Only `active` stock can be allocated or checked out. `retired` is permanent. |
| `version` | Starts at 1 and goes up by 1 on **every** change to this pool, including allocations, check-outs and returns. Send it back as `expected_version`. |

### Allocation (a project's share of a pool)

```json
{ "id": "7d0c2a9e-...:proj-1", "hardwareId": "7d0c2a9e-...", "projectId": "proj-1", "quantity": "3", "out": "1", "version": "3" }
```

| Field | Notes |
|---|---|
| `id` | Always `<hardware_id>:<project_id>`. |
| `quantity` | Units reserved for this project. |
| `out` | Of those, how many are checked out right now. Never more than `quantity`. |
| `version` | Goes up by 1 on each allocate, check-out and return for this project. |

### Checkout

```json
{
  "id": "b51f0c1d-...", "projectId": "proj-1", "hardwareId": "7d0c2a9e-...", "userId": "student-7",
  "quantity": "2", "returned": "1", "version": "2", "createdAt": "2026-11-04T15:30:00Z", "outstanding": "1"
}
```

| Field | Notes |
|---|---|
| `id` | The checkout id. Use it as `checkout_id` in `ReturnCheckout` and `CorrectCheckout`. |
| `quantity` | Units lent in this checkout. Never changes. |
| `returned` | Units given back so far (a running total, not the last return). |
| `outstanding` | `quantity - returned`: units still with the borrower. The checkout is closed when this is 0. |

The check-out `reason` is recorded in the audit log, not on the checkout.

## 3. What each RPC returns

The most common source of confusion: **the response is not always the thing the request was about.**

| RPC | Returns |
|---|---|
| `CreateHardware` | **Hardware** |
| `ListHardware` | **List** of Hardware (section 4) |
| `GetHardware` | **Hardware** |
| `GetAvailability` | `AvailabilityResponse` (section 5) |
| `UpdateHardware` | **Hardware** (after the edit) |
| `RetireHardware` | **Hardware**, `status: "retired"` |
| `AdjustHardware` | **Hardware**, `total` changed by `delta` |
| `AllocateHardware` | **Hardware** (not the allocation!) |
| `ListAllocations` | **List** of Allocation |
| `CreateCheckout` | **Checkout** (not the hardware!) |
| `ListCheckouts` | **List** of Checkout |
| `ReturnCheckout` | **Checkout**, `returned` increased |
| `CorrectCheckout` | **Checkout**, `returned` set to the given value |
| `ListTransactions` | **List** of AuditEvent (section 6) |
| `GetSummary` | `SummaryResponse` (section 7) |

Things to know:

- **`AllocateHardware` returns the hardware pool.** To see the project's resulting allocation, call `ListAllocations(project_id, hardware_id)`. The pool's `reserved` shows the pool-wide effect.
- **`CreateCheckout` and `ReturnCheckout` return the checkout, not the pool.** The pool's new `out` is not in the response; call `GetHardware` for it.
- **`AllocateHardware` sets the quantity; it does not add.** `quantity: 3` means "this project has 3 reserved", whether it had 0 or 10 before. `reserved` on the pool moves by the difference.
- **Returns add; corrections set.** `ReturnCheckout` with `quantity: 1` adds 1 to `returned`. `CorrectCheckout` with `returned: 1` sets `returned` to exactly 1.
- **Retrying with the same request number returns the original response.** It is the stored copy from the first call, so its `version` and counters can be older than the current state. It does not run the operation again.

### Worked example

Start: create a pool of 10 laptops, then run the steps below.

| Step | Call | Response shows | Pool after |
|---|---|---|---|
| 1 | `CreateHardware(total=10)` | Hardware: `total 10, reserved 0, out 0, version 1` | available 10, unallocated 10 |
| 2 | `AllocateHardware(proj-1, quantity=3)` | Hardware: `reserved 3, out 0, version 2` | available 10, unallocated 7 |
| 3 | `CreateCheckout(proj-1, student-7, quantity=2)` | Checkout: `quantity 2, returned 0, outstanding 2, version 1` | `out 2`, available 8 |
| 4 | `ReturnCheckout(quantity=1)` | Checkout: `quantity 2, returned 1, outstanding 1, version 2` | `out 1`, available 9 |

Reading step 3 shows why the pool and the checkout look different: the response is the checkout (`returned: 0` is *its* counter), and the pool's `out: 2` only shows up if you fetch the hardware.

## 4. List responses

`ListHardware`, `ListAllocations`, `ListCheckouts` and `ListTransactions` all return:

```json
{ "items": [ ... ], "total": "57", "page": "1", "size": "25" }
```

| Field | Meaning |
|---|---|
| `items` | The records on this page. |
| `total` | **Number of matching records across all pages.** This is a record count, **not** the stock `total` inside each hardware item. |
| `page`, `size` | The page you asked for (default 1 and 25; `size` is at most 100). |

Last page: `page * size >= total`. The maximum offset is 20 000 records; narrow your filters instead of paging that deep.

Defaults worth knowing:

- `ListCheckouts` returns **only outstanding checkouts** (`returned < quantity`) unless you send `outstanding: false`.
- Hardware sorts by name; checkouts and transactions are newest first.

## 5. Availability response

`GetAvailability` returns:

| Field | Meaning |
|---|---|
| `hardware` | The Hardware message (including `available` and `unallocated`). |
| `availability` | `"available"` if `hardware.available > 0`, otherwise `"unavailable"`. Based on `total - out` only, so it can say `available` for a pool that is entirely reserved to other projects. |
| `alternatives` | Empty when `available > 0`. When nothing is available, up to 10 vendor suggestions from the Azure SQL vendor-spend table. |
| `alternative_lookup_status` | Set only when the lookup failed: `"temporarily_unavailable"`. The rest of the response is still valid and `alternatives` is empty. |

Each `VendorAlternative` is a row from the vendor table: `source_unit`, `vendor`, `commodity`, `department`, `business_line`, `category` (text) and `quantity`, `unit_price`, `payment_amount`, `monthly_charges` (numbers, each present only if the source row has it; use `HasField`). These are spend records, not stock, so `quantity` there is not inventory.

## 6. Audit events (`ListTransactions`)

Every successful change writes one event. Filters: `project_id`, `hardware_id`, `actor_id`, `user_id`, `operation`, `start`, `end` (timestamps).

```json
{
  "id": "e1c0...", "actor": "team:projects-team", "operation": "checkout", "entity": "b51f0c1d-...",
  "hardwareId": "7d0c2a9e-...", "projectId": "proj-1", "userId": "student-7", "reason": "Lab session",
  "timestamp": "2026-11-04T15:30:00Z",
  "before": { "hardware": { }, "allocations": [ ], "checkout": null },
  "after":  { "hardware": { }, "allocations": [ ], "checkout": { } },
  "requestId": "9f86d081..."
}
```

| Field | Notes |
|---|---|
| `actor` | Who did it: `team:<name>` for service callers. |
| `operation` | `create`, `edit`, `retire`, `adjust`, `allocate`, `checkout`, `return` or `correct`. |
| `entity` | The id of what the call returned: the **checkout** id for `checkout`, `return` and `correct`, the **hardware** id for everything else. |
| `project_id`, `user_id` | **Unset** (not empty) when not applicable: `project_id` for `create`, `edit`, `retire`, `adjust`; `user_id` unless the operation involved a checkout. Test with `HasField`. |
| `before` / `after` | `google.protobuf.Struct` snapshots of the **stored** records (so they use the storage layer's `_id`, not `id`). `create` has an empty `before`. Other operations hold `hardware`, `allocations` (all of the pool's allocations) and `checkout` (`null` if none). Compare them to see exactly what changed. Numbers inside a Struct are doubles. |
| `request_id` | **Not the request number you sent.** It is a SHA-256 hash of the caller plus your request number, so you cannot search the log for `REQ-1001-checkout` directly. |

## 7. Summary (`GetSummary`)

```json
{
  "statuses": [ { "status": "active", "records": "12", "total": "140", "out": "18", "reserved": "60" } ],
  "projects": [ { "projectId": "proj-1", "quantity": "8", "out": "3" } ],
  "projectLimit": "100"
}
```

- `statuses`: one row per hardware status. `records` is how many hardware pools have that status; `total`, `out` and `reserved` are the sums across those pools.
- `projects`: one row per project, sorted by id, capped at `project_limit` (100). `quantity` is the sum of units reserved for the project across all pools, and `out` is how many of those are checked out.
- Both lists come from one consistent snapshot.

## 8. Errors

A failed call raises a gRPC error with a **status code** and a **message** (`details`). Nothing is changed by a failed call.

| Code | Message (examples) | What to do |
|---|---|---|
| `NOT_FOUND` | `Entity not found` | No hardware or checkout with that id. |
| `ABORTED` | `Stale version; refresh current values` | `expected_version` is out of date. Fetch the record and retry. |
| `ABORTED` | `Concurrent change; retry` | Another change landed at the same moment. Retry with the same request number. |
| `ALREADY_EXISTS` | `Request number reused for a different request` | The request number was already used with different fields. |
| `ALREADY_EXISTS` | `Duplicate asset tag or request number` | Asset tag already exists (on create). |
| `FAILED_PRECONDITION` | `Insufficient project stock` | Check-out is larger than `allocation.quantity - allocation.out`. |
| `FAILED_PRECONDITION` | `Project has no allocation` | Check out or return for a project with no allocation on this pool. |
| `FAILED_PRECONDITION` | `Hardware is not active` / `Only active stock can be allocated` | Pool status is not `active`. |
| `FAILED_PRECONDITION` | `Return exceeds outstanding quantity` | A return or correction would make `returned` exceed `quantity` or go below 0. |
| `FAILED_PRECONDITION` | `Cannot reopen inactive stock` | A correction that lowers `returned` on non-active stock. |
| `FAILED_PRECONDITION` | `Stock must satisfy 0 <= out <= reserved <= total` | The change would break the core rule, e.g. lowering `total` below `reserved`, or allocating more than `total`. |
| `FAILED_PRECONDITION` | `Release allocations before retirement` / `Return all checkouts before changing status` | Set allocations to 0 and return everything first. |
| `FAILED_PRECONDITION` | `Retired hardware is immutable` / `Already retired` | Retired pools cannot change. |
| `INVALID_ARGUMENT` | `confirmed: Field required` (and similar `field: reason`) | The message names the field. Fields that must be `true` (`confirmed`) must be sent explicitly. |
| `INVALID_ARGUMENT` | `Send a unique request_id (or idempotency-key metadata)` | Missing request number. |
| `INVALID_ARGUMENT` | `idempotency-key metadata and request_id differ` | Send one, or make them equal. |
| `INVALID_ARGUMENT` | `Delta must be nonzero`, `Start must precede end`, `Narrow filters; maximum offset is 20000` | Fix the request. |

`UNAUTHENTICATED`, `PERMISSION_DENIED`, `RESOURCE_EXHAUSTED` and `UNAVAILABLE` are covered in [SERVICE-API.md](SERVICE-API.md).

## 9. Other RPCs

| RPC | Returns |
|---|---|
| `grpc.health.v1.Health/Check` | `status: SERVING` while the process is up (standard gRPC health protocol) |
| `OpsService.Ready` | `{status: "ready"}`, or `UNAVAILABLE` if the database is not a replica set |
| `AuthService.Login`, `DevLogin` | `token` (use as `authorization: Bearer <token>`), `expires_at`, `name`, `roles` (website only) |
| `AuthService.Me` | `sub`, `name`, `roles` of the signed-in website user. Not available to team callers. |
| `AuthService.GetAuthMethods` | `password` and `local`: which sign-in forms the website should show |
| `AuthService.Logout` | Empty message; the token stops working |
