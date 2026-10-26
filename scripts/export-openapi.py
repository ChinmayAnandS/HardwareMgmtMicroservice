"""Export actual runtime schema and verify the published contract's route coverage."""
import json
from pathlib import Path
from app.main import create_app

actual=create_app().openapi()
contract=json.loads(Path('openapi.json').read_text())
operations=lambda schema:{(path,method) for path,methods in schema['paths'].items() for method in methods}
if operations(actual)!=operations(contract):
    raise SystemExit('OpenAPI contract routes differ from application routes')
Path('test-results').mkdir(exist_ok=True)
Path('test-results/openapi-runtime.json').write_text(json.dumps(actual,indent=2,sort_keys=True)+'\n')
