"""Seed through the gRPC API so audit, RBAC and idempotency apply."""
import base64
import json
import os
import sys
from pathlib import Path
import grpc

root = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(root / 'backend'))
from hardware.v1 import hardware_pb2 as pb, hardware_pb2_grpc as rpc  # noqa: E402  (run scripts/generate-proto.py first)

target = os.environ.get('SEED_TARGET', 'localhost:50051')
code = (root / '.secrets/team-hardware-admin').read_text().strip()
authorization = 'Basic ' + base64.b64encode(f'hardware-admin:{code}'.encode()).decode()
with grpc.insecure_channel(target) as channel:
    stub = rpc.HardwareServiceStub(channel)
    for i, row in enumerate(json.loads(Path(__file__).with_name('seed.json').read_text())):
        # A stable request number makes reruns return the original result instead of duplicating stock.
        hardware = stub.CreateHardware(pb.CreateHardwareRequest(**row, request_id=f'initial-seed-row-{i:04d}'),
                                       metadata=(('authorization', authorization),), timeout=30)
        print(hardware.id, hardware.name)
