import os
from dataclasses import dataclass, field
from pathlib import Path
from .teams import TEAMS


def secret(name):
    path = os.getenv(name + '_FILE')
    return Path(path).read_text().strip() if path else os.getenv(name, '')


@dataclass
class Settings:
    environment: str = os.getenv('ENVIRONMENT', 'production')
    mongo_uri: str = secret('MONGO_URI')
    database: str = os.getenv('MONGO_DATABASE', 'hardware')
    grpc_port: int = int(os.getenv('GRPC_PORT', '50051'))
    # Team name -> code, for every team in teams.py whose TEAM_CODE_* variable is set.
    team_codes: dict = field(default_factory=lambda: {
        name: secret(team['env']) for name, team in TEAMS.items() if secret(team['env'])})
    dev_code: str = secret('DEV_LOGIN_CODE')
    session_seconds: int = 3600
    azure_sql_server: str = os.getenv('AZURE_SQL_SERVER', '')
    azure_sql_database: str = os.getenv('AZURE_SQL_DATABASE', '')
    azure_sql_user: str = os.getenv('AZURE_SQL_USER', '')
    azure_sql_password: str = secret('AZURE_SQL_PASSWORD')
    azure_sql_driver: str = os.getenv('AZURE_SQL_DRIVER', 'ODBC Driver 18 for SQL Server')
    azure_sql_table: str = os.getenv('AZURE_SQL_TABLE', 'unified_procurement')
    sql_mock_data_file: str = os.getenv('SQL_MOCK_DATA_FILE', '')

    def check(self):
        if not self.mongo_uri:
            raise RuntimeError('MONGO_URI or MONGO_URI_FILE is required')
        if self.environment not in ('local', 'test', 'production'):
            raise RuntimeError('Invalid ENVIRONMENT')
        for name, code in self.team_codes.items():
            if name not in TEAMS:
                raise RuntimeError(f'Unknown team {name}')
            if len(code) < 16:
                raise RuntimeError(f'Code for team {name} must be at least 16 characters')
        if self.environment == 'production':
            if not self.mongo_uri.startswith('mongodb+srv://') or any(x in self.mongo_uri.lower() for x in ('tls=false', 'tlsallowinvalid', 'ssl=false', 'tlsinsecure')):
                raise RuntimeError('Production requires an Atlas SRV URI with verified TLS')
            if not self.ui_teams():
                raise RuntimeError('Production requires TEAM_CODE_HARDWARE_ADMIN')
            if self.dev_code:
                raise RuntimeError('Development login is forbidden in production')

    def ui_teams(self):
        return [name for name in self.team_codes if TEAMS[name]['ui']]
