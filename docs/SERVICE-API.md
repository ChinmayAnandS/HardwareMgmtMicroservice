# Team access to the Hardware gRPC API

The service speaks **gRPC**. The contract is [`proto/hardware/v1/hardware.proto`](../proto/hardware/v1/hardware.proto): generate a client from it in your language. There is no REST API.

Every caller identifies itself with a **team name** and that team's **code**. There is no shared password and no anonymous access.

| Team | Website sign-in | gRPC access |
|---|---|---|
| `hardware-admin` | Yes, full administrator | Every `HardwareService` RPC |
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

A team whose variable is empty cannot sign in. Codes must be at least 16 characters. To revoke or rotate a team's access, change or delete its variable. (Every variable also accepts a `_FILE` form, e.g. `TEAM_CODE_FRONT_END_FILE=/run/secrets/...`; local Docker Compose does this and writes the codes to `.secrets/team-*`.)

## How teams call the API

Send the credentials in the **`authorization` metadata** on every call: `Basic ` + base64(`team-name:team-code`). There is no login call and no session; every call carries the credentials.

Where to connect:

| Environment | Address |
|---|---|
| Local Docker Compose | `localhost:50051` (plaintext, loopback only) |
| Deployed | The service's gRPC endpoint, over TLS. The platform in front of the service must speak HTTP/2. |

```python
# pip install grpcio grpcio-tools   then, from the repo root:
#   python -m grpc_tools.protoc -Iproto --python_out=. --grpc_python_out=. proto/hardware/v1/hardware.proto
import base64, grpc
from hardware.v1 import hardware_pb2 as pb, hardware_pb2_grpc as rpc

credentials = 'Basic ' + base64.b64encode(b'projects-team:THE-CODE').decode()
metadata = (('authorization', credentials),)

channel = grpc.insecure_channel('localhost:50051')            # grpc.secure_channel(host, grpc.ssl_channel_credentials()) when deployed
stub = rpc.HardwareServiceStub(channel)
page = stub.ListHardware(pb.ListHardwareRequest(q='laptop'), metadata=metadata, timeout=30)
for h in page.items:
    print(h.id, h.name, 'on shelf:', h.available, 'unallocated:', h.unallocated)
```

With [grpcurl](https://github.com/fullstorydev/grpcurl) (no code generation needed; `-proto` reads the contract file):

```bash
AUTH="Basic $(printf 'projects-team:THE-CODE' | base64)"
grpcurl -plaintext -proto proto/hardware/v1/hardware.proto -import-path proto \
  -H "authorization: $AUTH" -d '{"q":"laptop"}' \
  localhost:50051 hardware.v1.HardwareService/ListHardware
```

Use the credentials from a server, never from browser code: anyone can read a code shipped to a browser. (The website signs in separately, with `AuthService.Login`, and receives a short-lived bearer token.)

A team's code only works with its own team name, and the audit log records every change as `team:<name>`.

## What each operation needs

| Operation | RPCs |
|---|---|
| Read | `ListHardware`, `GetHardware`, `GetAvailability`, `ListAllocations`, `ListCheckouts`, `ListTransactions`, `GetSummary` |
| Allocate | `AllocateHardware` |
| Check out | `CreateCheckout` |
| Check in | `ReturnCheckout` |
| Admin only | `CreateHardware`, `UpdateHardware`, `RetireHardware`, `AdjustHardware`, `CorrectCheckout` |

`AuthService.Me` and `Logout` are for website sessions only; a team calling them gets `PERMISSION_DENIED` (`Me`) or `UNAUTHENTICATED` (`Logout`).

## Request numbers: each operation happens once

Every mutating RPC must carry a unique **request number**: the `request_id` field in the message, or the `idempotency-key` metadata entry. If both are sent they must be equal. Letters, digits, `_ . : -`, up to 100 characters.

- Same number + same fields again → the original response is returned and **nothing changes** (no second checkout, no double return).
- Same number + different fields → `ALREADY_EXISTS`, nothing changes.
- Use a **new number for every separate operation**, e.g. `REQ-1001-checkout`, `REQ-1001-return-1`, `REQ-1001-return-2`.
- On a timeout, `UNAVAILABLE` or network error, resend the **exact same request with the same number**.
- No number at all → `INVALID_ARGUMENT`.

`expected_version` is optional for `AllocateHardware` and `ReturnCheckout`.

Fields marked `optional` in the contract track presence: **leaving a field out is different from sending 0 or false**. For example `AllocateHardware` with `quantity` unset is rejected, while `quantity: 0` releases the reservation.

```python
# Reserve 3 laptops for project proj-1 (projects-team)
pool = stub.AllocateHardware(pb.AllocateHardwareRequest(
    hardware_id=HARDWARE_ID, project_id='proj-1', quantity=3,
    reason='Project kickoff', request_id='REQ-1001-allocate'), metadata=metadata, timeout=30)

# Check out 2 of them to a student
checkout = stub.CreateCheckout(pb.CreateCheckoutRequest(
    hardware_id=HARDWARE_ID, project_id='proj-1', user_id='student-7', quantity=2,
    reason='Lab session', request_id='REQ-1001-checkout'), metadata=metadata, timeout=30)

# Return 1
checkout = stub.ReturnCheckout(pb.ReturnCheckoutRequest(
    checkout_id=checkout.id, quantity=1,
    reason='Returned after lab', request_id='REQ-1001-return-1'), metadata=metadata, timeout=30)
```

## Errors

Failures are `grpc.RpcError`s: read `e.code()` and `e.details()` (the details are the human-readable message).

| Status code | Meaning |
|---|---|
| `UNAUTHENTICATED` | Wrong team name or code (or none sent) |
| `PERMISSION_DENIED` | Your team is not allowed to call this RPC |
| `INVALID_ARGUMENT` | A field is missing or invalid, no request number, or `idempotency-key` and `request_id` disagree |
| `NOT_FOUND` | No hardware or checkout with that id |
| `ABORTED` | Stale `expected_version`, or another change landed at the same moment. Refresh and retry |
| `FAILED_PRECONDITION` | A stock rule would be broken, e.g. not enough reserved stock |
| `ALREADY_EXISTS` | Request number reused with different fields, or duplicate asset tag |
| `RESOURCE_EXHAUSTED` | Too many failed attempts from your address (wait five minutes), or the request is over 16 KiB |
| `UNAVAILABLE` | Storage is temporarily unavailable. Resend the same request |

See [API-RESPONSES.md](API-RESPONSES.md) for what each response contains, including the exact messages behind these codes.
