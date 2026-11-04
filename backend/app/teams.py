"""Teams that may use the hardware service, and what each team may do.

Each team signs in with its team name and a code. The code is NOT stored here: it comes
from the environment variable listed for the team (generate one per team with
    python -c "import secrets; print(secrets.token_urlsafe(18))"
). A team whose variable is empty is disabled.

- ui=True  -> the team can sign in to the website (name + code form) as a full administrator.
- api      -> what the team may call directly with HTTP Basic auth (username = team name,
              password = team code). 'all' means every /api endpoint.

To add a team or change permissions, edit TEAMS below and redeploy.
"""
import re

TEAMS = {
    'hardware-admin': {'env': 'TEAM_CODE_HARDWARE_ADMIN', 'ui': True, 'api': 'all'},
    'projects-team': {'env': 'TEAM_CODE_PROJECTS_TEAM', 'ui': False, 'api': ('read', 'allocate', 'checkout', 'return')},
    'front-end': {'env': 'TEAM_CODE_FRONT_END', 'ui': False, 'api': ('read', 'allocate', 'checkout', 'return')},
    'users-team': {'env': 'TEAM_CODE_USERS_TEAM', 'ui': False, 'api': ('read')}
}

# Operation name -> (HTTP method, path pattern) pairs it covers.
OPERATIONS = {
    'read': (('GET', re.compile(r'^/api/(?!me$).+')), ('HEAD', re.compile(r'^/api/(?!me$).+'))),
    'allocate': (('PUT', re.compile(r'^/api/hardware/[^/]+/allocations/[^/]+$')),),
    'checkout': (('POST', re.compile(r'^/api/checkouts$')),),
    'return': (('POST', re.compile(r'^/api/checkouts/[^/]+/returns$')),),
}


def allowed(team: str, method: str, path: str) -> bool:
    api = TEAMS[team]['api']
    if api == 'all':
        return path.startswith('/api/') and path != '/api/me'
    return any(m == method and pattern.match(path) for op in api for m, pattern in OPERATIONS[op])
