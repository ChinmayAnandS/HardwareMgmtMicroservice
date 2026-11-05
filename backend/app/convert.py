"""Stored documents -> protobuf messages."""
from datetime import datetime
from decimal import Decimal
from google.protobuf import json_format
from hardware.v1 import hardware_pb2 as pb


def plain(value):
    """JSON-safe copy: datetimes become RFC 3339 strings and decimals become floats."""
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, dict):
        return {k: plain(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [plain(v) for v in value]
    return value


def to_pb(cls, doc, **derived):
    """Build `cls` from a stored document: `_id` becomes `id`, nulls and unknown keys are dropped."""
    data = {('id' if k == '_id' else k): plain(v) for k, v in dict(doc, **derived).items() if v is not None}
    return json_format.ParseDict(data, cls(), ignore_unknown_fields=True)


def hardware(doc):
    return to_pb(pb.Hardware, doc, available=doc['total'] - doc['out'], unallocated=doc['total'] - doc['reserved'])


def checkout(doc):
    return to_pb(pb.Checkout, doc, outstanding=doc['quantity'] - doc['returned'])
