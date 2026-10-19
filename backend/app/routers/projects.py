from fastapi import APIRouter, Depends, HTTPException, status
from motor.motor_asyncio import AsyncIOMotorDatabase

from app.database import get_database
from app.models import CheckinRequest, CheckoutRequest, JoinProject, ProjectCreate

router = APIRouter(prefix="/project", tags=["project"])


async def require_project(db: AsyncIOMotorDatabase, project_id: int) -> dict:
    project = await db.projects.find_one({"project_id": project_id})
    if not project:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Project '{project_id}' does not exist",
        )
    return project


def require_matching_id(path_id: int, body_id: int) -> None:
    if path_id != body_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="project_id in the URL must match project_id in the request body",
        )


@router.get("/")
async def list_projects(
    userid: str | None = None,
    db: AsyncIOMotorDatabase = Depends(get_database),
) -> list[dict]:
    projects = []
    query = {"members": {"$in": [userid]}} if userid else {}
    async for project in db.projects.find(query):
        project["_id"] = str(project["_id"])
        projects.append(project)
    return projects


@router.post("/create")
async def create_project(
    project: ProjectCreate, db: AsyncIOMotorDatabase = Depends(get_database)
):
    existing = await db.projects.find_one({"project_id": project.project_id})
    if existing:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Project '{project.project_id}' already exists",
        )

    new_project = {
        "project_id": project.project_id,
        "project_name": project.name,
        "project_desc": project.description,
        "members": [project.userid],
        "hardware_sets": {},
    }
    insert_result = await db.projects.insert_one(new_project)
    return {
        "message": "Project created successfully",
        "project": {
            "project_id": new_project["project_id"],
            "project_name": new_project["project_name"],
            "project_desc": new_project["project_desc"],
        },
        "inserted_id": str(insert_result.inserted_id),
    }


@router.post("/join")
async def join_project(
    project: JoinProject, db: AsyncIOMotorDatabase = Depends(get_database)
):
    existing = await db.projects.find_one({"project_id": project.project_id})
    if not existing:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Project '{project.project_id}' not found",
        )

    if project.userid not in existing.get("members", []):
        await db.projects.update_one(
            {"_id": existing["_id"]},
            {"$addToSet": {"members": project.userid}},
        )
    return {
        "message": "Joined project successfully",
        "project_id": existing["project_id"],
        "project_name": existing["project_name"],
        "project_desc": existing["project_desc"],
    }


@router.post("/checkout/{project_id}")
async def checkout_hardware(
    project_id: int,
    checkout: CheckoutRequest,
    db: AsyncIOMotorDatabase = Depends(get_database),
):
    require_matching_id(project_id, checkout.project_id)
    project = await require_project(db, project_id)
    hardware_set = await db.hardware_sets.find_one({"name": checkout.hwset})
    if not hardware_set:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Hardware '{checkout.hwset}' does not exist",
        )

    availability = hardware_set["availability"]
    if checkout.quantity > availability:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                f"Requested quantity {checkout.quantity} exceeds available stock "
                f"({availability})"
            ),
        )

    await db.projects.update_one(
        {"_id": project["_id"]},
        {"$inc": {f"hardware_sets.{checkout.hwset}": checkout.quantity}},
    )
    await db.hardware_sets.update_one(
        {"_id": hardware_set["_id"]},
        {"$inc": {"availability": -checkout.quantity}},
    )
    return {
        "message": "Checkout is successful",
        "project_id": project["project_id"],
        "checkout_amount": checkout.quantity,
    }


@router.post("/checkin/{project_id}")
async def checkin_hardware(
    project_id: int,
    checkin: CheckinRequest,
    db: AsyncIOMotorDatabase = Depends(get_database),
):
    require_matching_id(project_id, checkin.project_id)
    project = await require_project(db, project_id)
    hardware_set = await db.hardware_sets.find_one({"name": checkin.hwset})
    if not hardware_set:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Hardware '{checkin.hwset}' does not exist",
        )

    project_availability = project.get("hardware_sets", {}).get(checkin.hwset)
    if project_availability is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"'{checkin.hwset}' not found in project hardware set",
        )
    if checkin.quantity > project_availability:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                f"Requested quantity of {checkin.quantity} to check in exceeds "
                f"current amount of stock checked out ({project_availability})"
            ),
        )

    await db.projects.update_one(
        {"_id": project["_id"]},
        {"$inc": {f"hardware_sets.{checkin.hwset}": -checkin.quantity}},
    )
    await db.hardware_sets.update_one(
        {"_id": hardware_set["_id"]},
        {"$inc": {"availability": checkin.quantity}},
    )
    return {
        "message": "Checkin is successful",
        "project_id": project["project_id"],
        "checkin_amount": checkin.quantity,
    }


@router.get("/project-dashboard/{project_id}")
async def show_project_details(
    project_id: int, db: AsyncIOMotorDatabase = Depends(get_database)
):
    project = await require_project(db, project_id)
    return {
        "project_id": project_id,
        "project_name": project["project_name"],
        "project_desc": project["project_desc"],
        "members": project["members"],
        "hardware_sets": project["hardware_sets"],
    }
