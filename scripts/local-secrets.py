"""Create ignored local secret files; never overwrite existing credentials."""
import os
import base64
import secrets
from pathlib import Path

root = Path(__file__).resolve().parents[1] / '.secrets'
root.mkdir(mode=0o700, exist_ok=True)
secrets_needed = [('mongo-root', 48), ('mongo-app', 48), ('login-code', 32), ('mongo-key', 512)]
# One code per team (see backend/app/teams.py) so the local stack can be called as each team.
secrets_needed += [(f'team-{team}', 24) for team in ('hardware-admin', 'projects-team', 'front-end', 'users-team')]
for name, length in secrets_needed:
    path = root / name
    if not path.exists():
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o644)
        with os.fdopen(fd, 'w') as out:
            out.write(base64.b64encode(secrets.token_bytes(length)).decode() if name == 'mongo-key' else secrets.token_urlsafe(length))
# Directory protects files on the host; readable mounts permit non-root containers.
print('Local secret files created. .secrets/login-code signs in to the website; .secrets/team-* hold the gRPC code for each team.')
