import os
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse


def secret(name):
    path = os.getenv(name + '_FILE')
    return Path(path).read_text().strip() if path else os.getenv(name, '')


@dataclass
class Settings:
    environment: str = os.getenv('ENVIRONMENT', 'production')
    mongo_uri: str = secret('MONGO_URI')
    database: str = os.getenv('MONGO_DATABASE', 'hardware')
    origin: str = os.getenv('PUBLIC_ORIGIN', 'http://localhost:8080').rstrip('/')
    issuer: str = os.getenv('OIDC_ISSUER', '')
    client_id: str = os.getenv('OIDC_CLIENT_ID', '')
    client_secret: str = secret('OIDC_CLIENT_SECRET')
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
        if self.environment == 'production':
            if not self.mongo_uri.startswith('mongodb+srv://') or any(x in self.mongo_uri.lower() for x in ('tls=false', 'tlsallowinvalid', 'ssl=false', 'tlsinsecure')):
                raise RuntimeError('Production requires an Atlas SRV URI with verified TLS')
            if not all((self.issuer, self.client_id, self.client_secret)):
                raise RuntimeError('OIDC configuration is required')
            if not self.origin.startswith('https://') or not self.issuer.startswith('https://'):
                raise RuntimeError('Production requires HTTPS origin and issuer')
            if self.dev_code:
                raise RuntimeError('Development login is forbidden in production')
        elif urlparse(self.origin).hostname not in ('localhost', '127.0.0.1'):
            raise RuntimeError('Local/test origin must be loopback')
