from pydantic import BaseModel, Field


class ProjectCreate(BaseModel):
    name: str
    description: str
    project_id: int
    userid: str
    hardware_sets: dict[str, int] = Field(default_factory=dict)


class JoinProject(BaseModel):
    project_id: int
    userid: str


class CheckoutRequest(BaseModel):
    project_id: int
    hwset: str
    quantity: int = Field(gt=0)


class CheckinRequest(BaseModel):
    project_id: int
    hwset: str
    quantity: int = Field(gt=0)
