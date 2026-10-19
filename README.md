# HardwareMgmtMicroservice

Project APIs migrated from `RishmitaR/appad_group_project` into a standalone
FastAPI service. This migration includes project creation, membership, the
project dashboard, and hardware checkout/checkin. It preserves the old
`/api/project` paths and MongoDB document fields.

## Run locally (Python 3.10+)

From the repository root:

```bash
cd backend
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
cp .env.example .env
```

Set `MONGODB_CONN_STRING` in `backend/.env` to the connection string supplied
by the team. The default database is `hardwareManagement`; override it with
`MONGODB_DATABASE` if needed. Existing environment variables take precedence.
Then run:

```bash
python -m uvicorn app.main:app --reload --port 8000
```

Open <http://localhost:8000/docs> to try the APIs. `GET /` reports that the
application is running; it does not check database connectivity. Add your
frontend's origin to the comma-separated `CORS_ORIGINS` setting when integrating.

## Endpoints

| Method | Path | Purpose |
| --- | --- | --- |
| GET | `/api/project/` | List projects; optional `userid` query parameter filters membership |
| POST | `/api/project/create` | Create a project |
| POST | `/api/project/join` | Join a project |
| POST | `/api/project/checkout/{project_id}` | Check hardware out to a project |
| POST | `/api/project/checkin/{project_id}` | Return hardware from a project |
| GET | `/api/project/project-dashboard/{project_id}` | Get project details |

Create request:

```json
{"name": "APAD Demo", "description": "Team project", "project_id": 101, "userid": "austin"}
```

Join request:

```json
{"project_id": 101, "userid": "another-user"}
```

Checkout/checkin request (the body ID must match the URL ID):

```json
{"project_id": 101, "hwset": "HWSet1", "quantity": 2}
```

Project IDs are integers. Quantities must be positive integers. As in the old
implementation, new projects start with empty `hardware_sets`, even if that
optional field is supplied in a create request.

## Database and integration assumptions

- Uses the existing `projects` and `hardware_sets` collections in the configured
  database. No data is copied, deleted, or seeded by startup.
- Hardware documents must already exist and use the old fields, including
  `name` and integer `availability`. Hardware setup/list APIs belong to the
  hardware integration and are not included here.
- Project documents retain `project_id`, `project_name`, `project_desc`,
  `members`, and `hardware_sets`.
- Users/login code is not migrated. The old project endpoints trust the supplied
  `userid` and do not authenticate requests or enforce membership. Connect the
  class authentication contract when it is available.
- Like the old code, checkout/checkin use two sequential writes. They are not
  atomic across collections and can produce inconsistent inventory during
  concurrent requests or a partial database failure. Transactions and a unique
  index on `projects.project_id` are follow-up integration work; the duplicate
  create check alone does not prevent simultaneous duplicate submissions.
- This migration assumes both collections remain accessible in the same
  MongoDB database. If the class separates data ownership by microservice,
  replace direct cross-service collection access with the agreed API contract.

## Changes from the old implementation

- Includes only the project router and the models it uses.
- Adds missing-project checks to checkout/checkin (404 instead of a crash).
- Validates the checkout/checkin path ID and rejects mismatched body IDs (400).
- Removes an unused dashboard-model import and a debug print.
- Uses a per-instance default for the project model's dictionary.
- Opens/closes the MongoDB client with application startup/shutdown, and exposes
  the database through a dependency so tests do not need real credentials.
- Replaces the encoded dependency dump with a UTF-8 requirements file containing
  direct dependencies. Version ranges are declared; they are not a full lockfile.
- Sets explicit local frontend origins instead of wildcard credentialed CORS.

Motor is retained to match the old backend; this is not a driver migration.

## Tests

From `backend` with the virtual environment activated:

```bash
python -m pip install -r requirements-dev.txt
python -m pytest -q
```

The HTTP tests use FastAPI's test client and mocked MongoDB calls. They check
the six endpoints, response/document compatibility, membership filtering,
duplicate and missing projects, positive quantities, ID mismatches, stock
limits, and startup/shutdown configuration. They do not verify a live MongoDB
connection, transactions, or concurrent inventory operations.

## Submit the migration for review

Use a new branch named `migrate-project-apis`, add these files to that branch,
and open a pull request into `main`. Review `docs/pull-request.md` for a prepared
title, description, and the limits of validation. Do not upload your `.env`
or virtual environment.
