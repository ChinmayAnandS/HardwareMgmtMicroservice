# Calling the API from another microservice

Browser users sign in with Microsoft Entra ID. Another microservice does **not** sign in: it sends a shared secret in the `X-API-Key` header. No cookie, CSRF token or Origin header is needed.

## Enable it

Generate a long random key once and keep it out of Git:

```bash
python -c "import secrets; print(secrets.token_urlsafe(48))"
```

Set it on **both** sides:

| Where | Setting |
|---|---|
| Hardware service (App Service → Environment variables) | `SERVICE_API_KEY` = the key, optional `SERVICE_NAME` = `project-service` |
| Calling microservice | the same key, sent as the `X-API-Key` header |

The service refuses to start if `SERVICE_API_KEY` is shorter than 32 characters. Leave it unset to disable key access entirely. To rotate, change the value on both sides and restart.

## What the key may do

| Allowed | Endpoint |
|---|---|
| Read anything | every `GET /api/...` except `/api/me` |
| Check out | `POST /api/checkouts` |
| Check in (return) | `POST /api/checkouts/{checkout_id}/returns` |
| Set a project allocation | `PUT /api/hardware/{hardware_id}/allocations/{project_id}` |

Creating, editing, retiring, adjusting stock and correcting returns stay with signed-in administrators (HTTP 403 for the key). Audit events record the caller as `service:<SERVICE_NAME>`.

## Request numbers: each operation happens once

Every checkout, return and allocation must carry a unique **request number**, either as `request_id` in the JSON body or as the `Idempotency-Key` header (if both are sent they must match). Allowed characters: letters, digits, `_ . : -`, up to 100 characters.

- Same number + same body again → the original response is returned and **nothing is changed** (no second checkout, no double return, no extra audit event).
- Same number + different body → HTTP 409; nothing is changed.
- Use a **new number for every distinct operation**. Two partial returns of one checkout need two numbers, e.g. `RET-1001-1` and `RET-1001-2`. A good pattern is `<your request id>-<operation>`, e.g. `REQ-1001-checkout`, `REQ-1001-return`.
- Numbers are remembered permanently and per caller (the service key and each administrator have separate namespaces).

`expected_version` is optional for allocation and return when a request number is sent; include it if you also want HTTP 409 when someone changed the record since you read it.

Retry safely: on a timeout, 503 or network error, resend the **exact same body with the same number**.

## Examples

```bash
BASE=https://YOUR-APP.azurewebsites.net
KEY=...   # the shared key

# Reserve 3 laptops for project proj-1
curl -X PUT "$BASE/api/hardware/$HARDWARE_ID/allocations/proj-1" \
  -H "X-API-Key: $KEY" -H "Content-Type: application/json" \
  -d '{"quantity":3,"reason":"Project kickoff","request_id":"REQ-1001-allocate"}'

# Check out 2 of them to a student
curl -X POST "$BASE/api/checkouts" \
  -H "X-API-Key: $KEY" -H "Content-Type: application/json" \
  -d '{"hardware_id":"'$HARDWARE_ID'","project_id":"proj-1","user_id":"student-7","quantity":2,"reason":"Lab session","request_id":"REQ-1001-checkout"}'

# Return 1 (use the checkout _id from the previous response)
curl -X POST "$BASE/api/checkouts/$CHECKOUT_ID/returns" \
  -H "X-API-Key: $KEY" -H "Content-Type: application/json" \
  -d '{"quantity":1,"reason":"Returned after lab","request_id":"REQ-1001-return-1"}'
```

Python caller:

```python
import os, requests

BASE = os.environ["HARDWARE_API_URL"]
HEADERS = {"X-API-Key": os.environ["HARDWARE_API_KEY"]}

def checkout(hardware_id, project_id, user_id, quantity, request_id):
    body = {"hardware_id": hardware_id, "project_id": project_id, "user_id": user_id,
            "quantity": quantity, "reason": "Requested by project service", "request_id": request_id}
    for attempt in range(3):            # same body + same request_id on every retry
        try:
            r = requests.post(f"{BASE}/api/checkouts", json=body, headers=HEADERS, timeout=30)
            if r.status_code != 503:
                return r
        except requests.RequestException:
            pass
    raise RuntimeError("hardware service unavailable")
```

## Errors

| Status | Meaning |
|---|---|
| 401 | Missing or wrong `X-API-Key` |
| 403 | The key is not allowed to call this endpoint |
| 409 | Request number reused with a different body, stale `expected_version`, or not enough reserved stock |
| 422 | No request number, header and `request_id` disagree, or invalid fields |
