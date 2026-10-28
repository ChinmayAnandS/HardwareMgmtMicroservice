"""Read-only vendor lookup against Azure SQL, with deterministic local mock mode."""
import json
import logging
from pathlib import Path
from typing import Any

log = logging.getLogger('hardware.sql')


class AzureSqlStore:
    def __init__(self, settings):
        self.settings = settings
        self.mock = bool(settings.sql_mock_data_file)
        self._rows: list[dict[str, Any]] = []
        if self.mock:
            self._rows = json.loads(Path(settings.sql_mock_data_file).read_text())

    def close(self):
        return None

    def find_vendor_alternatives(self, name: str, category: str | None = None, limit: int = 10):
        terms = [x.casefold() for x in (name, category) if x]
        if self.mock:
            rows = self._rows
            def score(row):
                text = ' '.join(str(row.get(k, '')) for k in ('vendor', 'commodity', 'category', 'business_line')).casefold()
                return sum(term in text for term in terms)
            return sorted((dict(r) for r in rows if score(r)), key=score, reverse=True)[:limit]

        if not all((self.settings.azure_sql_server, self.settings.azure_sql_database,
                    self.settings.azure_sql_user, self.settings.azure_sql_password)):
            return []
        try:
            import pyodbc
        except ImportError as exc:
            raise RuntimeError('pyodbc is required for Azure SQL lookups') from exc
        # The table name is configuration-only and validated as a simple identifier.
        table = self.settings.azure_sql_table
        if not table.replace('_', '').isalnum():
            raise RuntimeError('AZURE_SQL_TABLE must be a simple table name')
        connection_string = (
            f'DRIVER={{{self.settings.azure_sql_driver}}};'
            f'SERVER={self.settings.azure_sql_server};DATABASE={self.settings.azure_sql_database};'
            f'UID={self.settings.azure_sql_user};PWD={self.settings.azure_sql_password};'
            'Encrypt=yes;TrustServerCertificate=no;Connection Timeout=10;'
        )
        search = f'%{name}%'
        category_search = f'%{category or name}%'
        query = f'''SELECT TOP (?) source_unit, vendor, commodity, department, business_line,
                    quantity, unit_price, payment_amount, monthly_charges, category
                    FROM dbo.{table}
                    WHERE commodity LIKE ? OR business_line LIKE ? OR category LIKE ? OR vendor LIKE ?
                    ORDER BY vendor'''
        with pyodbc.connect(connection_string, timeout=10) as connection:
            cursor = connection.cursor()
            cursor.execute(query, limit, search, search, category_search, search)
            columns = [column[0] for column in cursor.description]
            return [dict(zip(columns, row)) for row in cursor.fetchall()]
