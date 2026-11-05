from datetime import datetime
from typing import Annotated, Literal
from pydantic import BaseModel, ConfigDict, Field, StringConstraints

Text = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=160)]
Identifier = Annotated[str, StringConstraints(pattern=r'^[A-Za-z0-9_.:@-]{1,100}$')]
Reason = Annotated[str, StringConstraints(strip_whitespace=True, min_length=3, max_length=1000)]
Status = Literal['active', 'damaged', 'under_maintenance', 'retired']
Quantity = Annotated[int, Field(strict=True, ge=0, le=1000000)]
Positive = Annotated[int, Field(strict=True, ge=1, le=1000000)]
# A caller-chosen unique request number. Sending the same number again never repeats the operation.
RequestId = Annotated[str, StringConstraints(pattern=r'^[A-Za-z0-9_.:-]{1,100}$')]
Entity = Annotated[str, StringConstraints(min_length=1, max_length=300)]


class Model(BaseModel):
    model_config = ConfigDict(extra='forbid')


class Command(Model):
    request_id: RequestId | None = None


class Create(Command):
    name: Text
    category: Text
    description: Annotated[str, Field(max_length=2000)] = ''
    asset_tag: Text
    serial: Annotated[str, Field(max_length=160)] = ''
    total: Quantity
    reason: Reason


class Change(Command):
    expected_version: Positive
    reason: Reason


class Edit(Change):
    hardware_id: Identifier
    name: Text | None = None
    category: Text | None = None
    description: Annotated[str, Field(max_length=2000)] | None = None
    asset_tag: Text | None = None
    serial: Annotated[str, Field(max_length=160)] | None = None
    status: Literal['active', 'damaged', 'under_maintenance'] | None = None


class Adjust(Change):
    hardware_id: Identifier
    delta: Annotated[int, Field(strict=True, ge=-1000000, le=1000000)]
    confirmed: Literal[True]


class Retire(Change):
    hardware_id: Identifier
    confirmed: Literal[True]


class Allocate(Change):
    hardware_id: Identifier
    project_id: Identifier
    quantity: Quantity
    # Optional here: the request number already protects retries; send it to also detect stale edits.
    expected_version: Positive | None = None


class Checkout(Command):
    hardware_id: Identifier
    project_id: Identifier
    user_id: Identifier
    quantity: Positive
    reason: Reason


class Return(Change):
    checkout_id: Identifier
    quantity: Positive
    expected_version: Positive | None = None


class Correction(Change):
    checkout_id: Identifier
    returned: Quantity
    confirmed: Literal[True]


class Page(Model):
    page: Annotated[int, Field(strict=True, ge=1, le=10000)] = 1
    size: Annotated[int, Field(strict=True, ge=1, le=100)] = 25


class HardwareQuery(Page):
    q: Annotated[str, Field(max_length=160)] = ''
    status: Status | None = None
    category: Annotated[str, Field(max_length=160)] | None = None
    sort: Literal['name_key', 'asset_key', 'status', 'total'] = 'name_key'
    direction: Literal['asc', 'desc'] = 'asc'


class AllocationQuery(Page):
    project_id: Identifier | None = None
    hardware_id: Identifier | None = None


class CheckoutQuery(AllocationQuery):
    user_id: Identifier | None = None
    outstanding: bool = True


class TransactionQuery(AllocationQuery):
    actor_id: Entity | None = None
    user_id: Identifier | None = None
    operation: Annotated[str, Field(max_length=30)] | None = None
    start: datetime | None = None
    end: datetime | None = None


class TeamLogin(Model):
    name: Annotated[str, StringConstraints(pattern=r'^[A-Za-z0-9._-]{1,40}$')]
    code: Annotated[str, Field(min_length=1, max_length=200)]


class DevLogin(Model):
    code: Annotated[str, Field(min_length=20, max_length=200)]
