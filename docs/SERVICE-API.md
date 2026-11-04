# Team access to the Hardware API

Every caller identifies itself with a **team name** and that team's **code**. There is no shared password and no anonymous access.

| Team | Website sign-in | API access |
|---|---|---|
| `hardware-admin` | Yes, full administrator | Everything |
| `projects-team` | No | Read everything, allocate, check out, check in |
| `front-end` | No | Read everything, allocate, check out, check in |
| `users-team` | No | Read everything (read-only) |

Permissions are defined in `backend/app/teams.py`. To add a team or change what a team may do, edit that file and redeploy.

## Setting up the codes (hardware-admin team)

Generate one code per team and keep each one private:

```
python -c "import secrets; print(secrets.token_urlsafe(18))"
```

Add them to the app as environment variables (in Container Apps, store each as a secret and reference it):

| Variable | Team |
|---|---|
| `TEAM_CODE_HARDWARE_ADMIN` | hardware-admin (required) |
| `TEAM_CODE_PROJECTS_TEAM` | projects-team |
| `TEAM_CODE_FRONT_END` | front-end |
| `TEAM_CODE_USERS_TEAM` | users-team |

A team whose variable is empty cannot sign in. Codes must be at least 16 characters. To revoke or rotate a team's access, change or delete its variable.

## How API teams call the API

Use **HTTP Basic authentication**: username = team name, password = team code. Every request needs it; there is no login step and no cookie.

```bash
curl -u projects-team:THE-CODE https://YOUR-APP-URL/api/hardware
```

```python
import requests
AUTH = ("projects-team", "THE-CODE")
requests.get("https://YOUR-APP-URL/api/hardware", auth=AUTH, timeout=30)
```

```javascript
// Run this from a server, never from browser code: anyone can read a code shipped to a browser.
const headers = { Authorization: "Basic " + Buffer.from("front-end:THE-CODE").toString("base64") };
```

A team's code only works with its own team name, and the audit log records every change as `team:<name>`.

## What each operation needs

| Operation | Endpoint |
|---|---|
| Read | any `GET /api/...` (except `/api/me`), e.g. `/api/hardware`, `/api/hardware/{id}/availability`, `/api/checkouts`, `/api/transactions` |
| Allocate | `PUT /api/hardware/{hardware_id}/allocations/{project_id}` |
| Check out | `POST /api/checkouts` |
| Check in | `POST /api/checkouts/{checkout_id}/returns` |

## Request numbers: each operation happens once

Every check-out, check-in and allocation must carry a unique **request number** as `request_id` in the JSON body (or the `Idempotency-Key` header; if both are sent they must match). Letters, digits, `_ . : -`, up to 100 characters.

- Same number + same body again → the original response is returned and **nothing changes** (no second checkout, no double return).
- Same number + different body → HTTP 409, nothing changes.
- Use a **new number for every separate operation**, e.g. `REQ-1001-checkout`, `REQ-1001-return-1`, `REQ-1001-return-2`.
- On a timeout, 503 or network error, resend the **exact same body with the same number**.

`expected_version` is optional for allocation and check-in.

```bash
# Reserve 3 laptops for project proj-1 (projects-team)
curl -u projects-team:THE-CODE -X PUT "https://YOUR-APP-URL/api/hardware/$HARDWARE_ID/allocations/proj-1" \
  -H "Content-Type: application/json" \
  -d '{"quantity":3,"reason":"Project kickoff","request_id":"REQ-1001-allocate"}'

# Check out 2 of them to a student
curl -u projects-team:THE-CODE -X POST "https://YOUR-APP-URL/api/checkouts" \
  -H "Content-Type: application/json" \
  -d '{"hardware_id":"'$HARDWARE_ID'","project_id":"proj-1","user_id":"student-7","quantity":2,"reason":"Lab session","request_id":"REQ-1001-checkout"}'

# Return 1
curl -u projects-team:THE-CODE -X POST "https://YOUR-APP-URL/api/checkouts/$CHECKOUT_ID/returns" \
  -H "Content-Type: application/json" \
  -d '{"quantity":1,"reason":"Returned after lab","request_id":"REQ-1001-return-1"}'
```

## Errors

| Status | Meaning |
|---|---|
| 401 | Wrong team name or code (or none sent) |
| 403 | Your team is not allowed to call this endpoint |
| 409 | Request number reused with a different body, stale `expected_version`, or not enough reserved stock |
| 422 | No request number, header and `request_id` disagree, or invalid fields |
| 429 | Too many failed attempts from your address; wait five minutes |
