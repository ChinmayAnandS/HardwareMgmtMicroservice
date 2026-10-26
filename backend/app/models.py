from typing import Annotated, Literal
from pydantic import BaseModel, ConfigDict, Field, StringConstraints

Text = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=160)]
Identifier = Annotated[str, StringConstraints(pattern=r'^[A-Za-z0-9_.:@-]{1,100}$')]
Reason = Annotated[str, StringConstraints(strip_whitespace=True, min_length=3, max_length=1000)]
Status = Literal['active', 'damaged', 'under_maintenance', 'retired']
Quantity = Annotated[int, Field(strict=True, ge=0, le=1000000)]
Positive = Annotated[int, Field(strict=True, ge=1, le=1000000)]


class Model(BaseModel):
    model_config = ConfigDict(extra='forbid')


class Create(Model):
    name: Text
    category: Text
    description: Annotated[str, Field(max_length=2000)] = ''
    asset_tag: Text
    serial: Annotated[str, Field(max_length=160)] = ''
    total: Quantity
    reason: Reason


class Change(Model):
    expected_version: Positive
    reason: Reason


class Edit(Change):
    name: Text | None = None
    category: Text | None = None
    description: Annotated[str, Field(max_length=2000)] | None = None
    asset_tag: Text | None = None
    serial: Annotated[str, Field(max_length=160)] | None = None
    status: Literal['active', 'damaged', 'under_maintenance'] | None = None


class Adjust(Change):
    delta: Annotated[int, Field(strict=True, ge=-1000000, le=1000000)]
    confirmed: Literal[True]


class Retire(Change):
    confirmed: Literal[True]


class Allocate(Change):
    quantity: Quantity


class Checkout(Model):
    hardware_id: Identifier
    project_id: Identifier
    user_id: Identifier
    quantity: Positive
    reason: Reason


class Return(Change):
    quantity: Positive


class Correction(Change):
    returned: Quantity
    confirmed: Literal[True]


class Login(Model):
    code: Annotated[str, Field(min_length=20, max_length=200)]
