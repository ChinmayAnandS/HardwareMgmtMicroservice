"""Create ignored local secret files; never overwrite existing credentials."""
import os
import base64
import secrets
from pathlib import Path

root = Path(__file__).resolve().parents[1] / '.secrets'
root.mkdir(mode=0o700, exist_ok=True)
for name, length in [('mongo-root', 48), ('mongo-app', 48), ('login-code', 32), ('mongo-key', 512)]:
    path = root / name
    if not path.exists():
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o644)
        with os.fdopen(fd, 'w') as out:
            out.write(base64.b64encode(secrets.token_bytes(length)).decode() if name == 'mongo-key' else secrets.token_urlsafe(length))
# Directory protects files on the host; readable mounts permit non-root containers.
print('Local secret files created. Read .secrets/login-code for local administrator sign-in.')
