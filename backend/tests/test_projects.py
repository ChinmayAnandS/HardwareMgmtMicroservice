"""HTTP-level regression tests; database calls are mocked, not sent to MongoDB."""

from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from bson import ObjectId
from fastapi.testclient import TestClient

from app.database import get_database
from app.main import app


PROJECT = {
    "_id": ObjectId("507f1f77bcf86cd799439011"),
    "project_id": 101,
    "project_name": "Team Hardware",
    "project_desc": "Class project",
    "members": ["austin"],
    "hardware_sets": {"HWSet1": 3},
}
HARDWARE = {
    "_id": ObjectId("507f1f77bcf86cd799439012"),
    "name": "HWSet1",
    "availability": 10,
    "capacity": 100,
}


@pytest.fixture
def api():
    def collection():
        return SimpleNamespace(
            find=MagicMock(),
            find_one=AsyncMock(),
            insert_one=AsyncMock(),
            update_one=AsyncMock(),
        )

    db = SimpleNamespace(projects=collection(), hardware_sets=collection())
    db.projects.find_one.return_value = deepcopy(PROJECT)
    db.hardware_sets.find_one.return_value = deepcopy(HARDWARE)
    app.dependency_overrides[get_database] = lambda: db
    # No context manager: endpoint tests use the override without MongoDB startup.
    client = TestClient(app)
    try:
        yield client, db
    finally:
        client.close()
        app.dependency_overrides.clear()


def test_create_preserves_document_and_response(api):
    client, db = api
    db.projects.find_one.return_value = None
    db.projects.insert_one.return_value = SimpleNamespace(inserted_id=PROJECT["_id"])
    response = client.post(
        "/api/project/create",
        json={"name": "Team Hardware", "description": "Class project", "project_id": 101, "userid": "austin"},
    )
    assert response.status_code == 200
    assert response.json()["inserted_id"] == str(PROJECT["_id"])
    document = db.projects.insert_one.await_args.args[0]
    assert document == {
        "project_id": 101,
        "project_name": "Team Hardware",
        "project_desc": "Class project",
        "members": ["austin"],
        "hardware_sets": {},
    }


def test_duplicate_project_does_not_insert(api):
    client, db = api
    response = client.post(
        "/api/project/create",
        json={"name": "Duplicate", "description": "", "project_id": 101, "userid": "austin"},
    )
    assert response.status_code == 400
    db.projects.insert_one.assert_not_awaited()


@pytest.mark.parametrize("userid", [None, "austin"])
def test_list_projects_filters_membership_and_serializes_id(api, userid):
    client, db = api

    async def cursor():
        yield deepcopy(PROJECT)

    db.projects.find.return_value = cursor()
    response = client.get("/api/project/", params={"userid": userid} if userid else {})
    assert response.status_code == 200
    assert response.json()[0]["_id"] == str(PROJECT["_id"])
    db.projects.find.assert_called_once_with({"members": {"$in": [userid]}} if userid else {})


@pytest.mark.parametrize("userid,updates", [("new-member", 1), ("austin", 0)])
def test_join_is_idempotent(api, userid, updates):
    client, db = api
    response = client.post("/api/project/join", json={"project_id": 101, "userid": userid})
    assert response.status_code == 200
    assert db.projects.update_one.await_count == updates
    if updates:
        db.projects.update_one.assert_awaited_once_with(
            {"_id": PROJECT["_id"]}, {"$addToSet": {"members": userid}}
        )


def test_join_missing_project(api):
    client, db = api
    db.projects.find_one.return_value = None
    response = client.post("/api/project/join", json={"project_id": 999, "userid": "austin"})
    assert response.status_code == 404
    db.projects.update_one.assert_not_awaited()


@pytest.mark.parametrize("operation,delta", [("checkout", 2), ("checkin", -2)])
def test_hardware_transfer_updates_both_collections(api, operation, delta):
    client, db = api
    response = client.post(
        f"/api/project/{operation}/101",
        json={"project_id": 101, "hwset": "HWSet1", "quantity": 2},
    )
    assert response.status_code == 200
    assert response.json()[f"{operation}_amount"] == 2
    db.projects.update_one.assert_awaited_once_with(
        {"_id": PROJECT["_id"]}, {"$inc": {"hardware_sets.HWSet1": delta}}
    )
    db.hardware_sets.update_one.assert_awaited_once_with(
        {"_id": HARDWARE["_id"]}, {"$inc": {"availability": -delta}}
    )


@pytest.mark.parametrize("operation", ["checkout", "checkin"])
@pytest.mark.parametrize("quantity", [0, -1])
def test_nonpositive_quantity_rejected_before_database_access(api, operation, quantity):
    client, db = api
    response = client.post(
        f"/api/project/{operation}/101",
        json={"project_id": 101, "hwset": "HWSet1", "quantity": quantity},
    )
    assert response.status_code == 422
    db.projects.find_one.assert_not_awaited()
    db.projects.update_one.assert_not_awaited()
    db.hardware_sets.update_one.assert_not_awaited()


@pytest.mark.parametrize("operation", ["checkout", "checkin"])
def test_mismatched_project_ids_rejected(api, operation):
    client, db = api
    response = client.post(
        f"/api/project/{operation}/101",
        json={"project_id": 999, "hwset": "HWSet1", "quantity": 1},
    )
    assert response.status_code == 400
    db.projects.find_one.assert_not_awaited()
    db.hardware_sets.update_one.assert_not_awaited()


@pytest.mark.parametrize("operation", ["checkout", "checkin"])
def test_missing_project_returns_404_without_writes(api, operation):
    client, db = api
    db.projects.find_one.return_value = None
    response = client.post(
        f"/api/project/{operation}/101",
        json={"project_id": 101, "hwset": "HWSet1", "quantity": 1},
    )
    assert response.status_code == 404
    db.projects.update_one.assert_not_awaited()
    db.hardware_sets.update_one.assert_not_awaited()


@pytest.mark.parametrize("operation", ["checkout", "checkin"])
def test_unknown_hardware_returns_404_without_writes(api, operation):
    client, db = api
    db.hardware_sets.find_one.return_value = None
    response = client.post(
        f"/api/project/{operation}/101",
        json={"project_id": 101, "hwset": "Unknown", "quantity": 1},
    )
    assert response.status_code == 404
    db.projects.update_one.assert_not_awaited()
    db.hardware_sets.update_one.assert_not_awaited()


@pytest.mark.parametrize("operation,quantity", [("checkout", 11), ("checkin", 4)])
def test_overdraw_rejected_without_writes(api, operation, quantity):
    client, db = api
    response = client.post(
        f"/api/project/{operation}/101",
        json={"project_id": 101, "hwset": "HWSet1", "quantity": quantity},
    )
    assert response.status_code == 400
    db.projects.update_one.assert_not_awaited()
    db.hardware_sets.update_one.assert_not_awaited()


def test_returning_hardware_not_held_by_project(api):
    client, db = api
    db.projects.find_one.return_value["hardware_sets"] = {}
    response = client.post(
        "/api/project/checkin/101",
        json={"project_id": 101, "hwset": "HWSet1", "quantity": 1},
    )
    assert response.status_code == 404
    db.projects.update_one.assert_not_awaited()
    db.hardware_sets.update_one.assert_not_awaited()


def test_dashboard_preserves_response_fields(api):
    client, _ = api
    response = client.get("/api/project/project-dashboard/101")
    assert response.status_code == 200
    assert response.json() == {key: value for key, value in PROJECT.items() if key != "_id"}


def test_missing_dashboard(api):
    client, db = api
    db.projects.find_one.return_value = None
    assert client.get("/api/project/project-dashboard/999").status_code == 404


def test_service_exposes_only_project_router(api):
    client, _ = api
    paths = client.get("/openapi.json").json()["paths"]
    assert set(paths) == {
        "/",
        "/api/project/",
        "/api/project/create",
        "/api/project/join",
        "/api/project/checkout/{project_id}",
        "/api/project/checkin/{project_id}",
        "/api/project/project-dashboard/{project_id}",
    }


def test_database_lifecycle_uses_environment_and_closes_client(monkeypatch):
    import app.database as database_module

    monkeypatch.setenv("MONGODB_CONN_STRING", "mongodb://localhost:27017")
    monkeypatch.setenv("MONGODB_DATABASE", "test_database")
    motor_client = MagicMock()
    factory = MagicMock(return_value=motor_client)
    monkeypatch.setattr(database_module, "AsyncIOMotorClient", factory)
    with TestClient(app) as client:
        assert client.get("/").status_code == 200
        motor_client.__getitem__.assert_called_once_with("test_database")
    factory.assert_called_once_with("mongodb://localhost:27017", serverSelectionTimeoutMS=5000)
    motor_client.close.assert_called_once()


def test_database_startup_requires_connection_string(monkeypatch):
    import app.database as database_module

    monkeypatch.delenv("MONGODB_CONN_STRING", raising=False)
    monkeypatch.setattr(database_module, "load_dotenv", lambda *_: None)
    with pytest.raises(RuntimeError, match="MONGODB_CONN_STRING"):
        with TestClient(app):
            pass
