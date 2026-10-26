"""Seed through the API so audit, RBAC and idempotency apply."""
import json
import os
from pathlib import Path
import httpx

origin = os.environ.get('SEED_ORIGIN', 'http://localhost:8080')
code = (Path(__file__).resolve().parents[1] / '.secrets/login-code').read_text().strip()
with httpx.Client(base_url=origin, headers={'Origin': origin}) as client:
    client.post('/auth/dev', json={'code': code}).raise_for_status()
    me = client.get('/api/me').json()
    for i, row in enumerate(json.loads(Path(__file__).with_name('seed.json').read_text())):
        response = client.post('/api/hardware', json=row, headers={
            'X-CSRF-Token': me['csrf'], 'Idempotency-Key': f'initial-seed-row-{i:04d}'})
        response.raise_for_status()
        print(response.json()['_id'], row['name'])
