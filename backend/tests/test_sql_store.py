import json
from types import SimpleNamespace
from app.sql_store import AzureSqlStore


def test_mock_sql_lookup_returns_cross_dataset_vendors(tmp_path):
    path = tmp_path / 'rows.json'
    path.write_text(json.dumps([
        {'source_unit': 'Austin', 'vendor': 'Dell', 'commodity': 'Laptop computers', 'category': 'Computer'},
        {'source_unit': 'Texas', 'vendor': 'Microsoft', 'business_line': 'Cloud services', 'category': 'Computer'},
        {'source_unit': 'Dallas', 'vendor': 'Office supplier', 'commodity': 'Desks', 'category': 'Furniture'},
    ]))
    settings = SimpleNamespace(sql_mock_data_file=str(path))
    rows = AzureSqlStore(settings).find_vendor_alternatives('Laptop', 'Computer')
    assert {row['vendor'] for row in rows} == {'Dell', 'Microsoft'}
