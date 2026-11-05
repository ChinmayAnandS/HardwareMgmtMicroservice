"""Generate the gRPC stubs on first use so `pytest` works from a fresh checkout."""
import subprocess
import sys
from pathlib import Path

root = Path(__file__).resolve().parents[2]
if not (root / 'backend' / 'hardware' / 'v1' / 'hardware_pb2.py').exists():
    subprocess.run([sys.executable, str(root / 'scripts' / 'generate-proto.py')], check=True)
