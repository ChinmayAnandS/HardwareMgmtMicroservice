"""Generate the Python gRPC stubs from proto/ into backend/hardware/ (ignored by git)."""
import sys
from pathlib import Path
import grpc_tools
from grpc_tools import protoc

root = Path(__file__).resolve().parents[1]
out = root / 'backend'
proto = root / 'proto' / 'hardware' / 'v1' / 'hardware.proto'
well_known = Path(grpc_tools.__file__).parent / '_proto'
code = protoc.main(['protoc', f'-I{root / "proto"}', f'-I{well_known}', f'--python_out={out}',
                    f'--grpc_python_out={out}', f'--pyi_out={out}', str(proto)])
if code:
    sys.exit(code)
for package in (out / 'hardware', out / 'hardware' / 'v1'):
    (package / '__init__.py').touch()
print('Generated', out / 'hardware' / 'v1')
