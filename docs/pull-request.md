# Suggested title

Migrate project APIs into the hardware microservice

# Suggested description

The new repository does not yet expose project endpoints. Add the six project
APIs from the old APAD backend, including project creation/joining, the project
dashboard, and hardware checkout/checkin, with their supporting FastAPI app,
MongoDB setup, and request models. Preserve `/api/project/...` paths and the
existing collection/document format.

Checkout/checkin now return 404 for a missing project and reject mismatched
URL/body project IDs. Include a clean dependency file, environment template,
local run instructions, and HTTP regression tests with mocked database calls.

Validation: run `cd backend && python -m pytest -q`. Tests do not use a live
MongoDB instance. Verify the team's connection string and existing hardware
documents in a development database before class-wide integration.

Existing integration limits remain: project endpoints trust supplied user IDs,
inventory updates are sequential across collections, and duplicate project
creation is not protected by a unique index. See README integration notes.
