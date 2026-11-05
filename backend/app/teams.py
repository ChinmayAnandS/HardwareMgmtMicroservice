"""Teams that may use the hardware service, and what each team may do.

Each team signs in with its team name and a code. The code is NOT stored here: it comes
from the environment variable listed for the team (generate one per team with
    python -c "import secrets; print(secrets.token_urlsafe(18))"
). A team whose variable is empty is disabled.

- ui=True  -> the team can sign in to the website (name + code form) as a full administrator.
- api      -> what the team may call directly with Basic credentials in the `authorization`
              metadata (team name + team code). 'all' means every HardwareService RPC.

To add a team or change permissions, edit TEAMS below and redeploy.
"""

TEAMS = {
    'hardware-admin': {'env': 'TEAM_CODE_HARDWARE_ADMIN', 'ui': True, 'api': 'all'},
    'projects-team': {'env': 'TEAM_CODE_PROJECTS_TEAM', 'ui': False, 'api': ('read', 'allocate', 'checkout', 'return')},
    'front-end': {'env': 'TEAM_CODE_FRONT_END', 'ui': False, 'api': ('read', 'allocate', 'checkout', 'return')},
    'users-team': {'env': 'TEAM_CODE_USERS_TEAM', 'ui': False, 'api': ('read',)},
}

# Operation name -> the HardwareService RPCs it covers. RPCs in no operation (CreateHardware,
# UpdateHardware, RetireHardware, AdjustHardware, CorrectCheckout) are for 'all' teams only.
OPERATIONS = {
    'read': ('GetHardware', 'ListHardware', 'GetAvailability', 'ListAllocations', 'ListCheckouts',
             'ListTransactions', 'GetSummary'),
    'allocate': ('AllocateHardware',),
    'checkout': ('CreateCheckout',),
    'return': ('ReturnCheckout',),
}
READ_RPCS = frozenset(OPERATIONS['read'])
HARDWARE_SERVICE = 'hardware.v1.HardwareService'


def allowed(team: str, method: str) -> bool:
    """`method` is the RPC's full name, e.g. /hardware.v1.HardwareService/CreateCheckout."""
    service, _, name = method.lstrip('/').partition('/')
    if service != HARDWARE_SERVICE:
        return False
    api = TEAMS[team]['api']
    return api == 'all' or any(name in OPERATIONS[op] for op in api)
