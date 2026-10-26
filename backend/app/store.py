import hashlib
import json
from datetime import datetime, timezone
from uuid import uuid4
from fastapi import HTTPException
from pymongo import MongoClient
from pymongo.errors import DuplicateKeyError
from pymongo.read_concern import ReadConcern
from pymongo.write_concern import WriteConcern
from .domain import Conflict, require, transition, validate


def now():
    return datetime.now(timezone.utc)


def clean(value):
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, dict):
        return {k: clean(v) for k, v in value.items()}
    if isinstance(value, list):
        return [clean(v) for v in value]
    return value


class Store:
    def __init__(self, settings):
        self.client = MongoClient(settings.mongo_uri, serverSelectionTimeoutMS=5000,
                                  connectTimeoutMS=5000, socketTimeoutMS=10000,
                                  maxPoolSize=20, tz_aware=True)
        self.db = self.client[settings.database]

    def get(self, collection, entity, session=None):
        value = self.db[collection].find_one({'_id': entity}, session=session)
        if value is None:
            raise HTTPException(404, 'Entity not found')
        return value

    def mutate(self, actor, key, command, entity, data):
        request_id = hashlib.sha256((actor + ':' + key).encode()).hexdigest()
        fingerprint = hashlib.sha256(json.dumps([command, entity, data], sort_keys=True).encode()).hexdigest()

        def work(s):
            previous = self.db.requests.find_one({'_id': request_id}, session=s)
            if previous:
                require(previous['fingerprint'] == fingerprint, 'Idempotency key reused for a different request')
                return previous['response']
            if command == 'create':
                h = {k: data[k] for k in ('name', 'category', 'description', 'asset_tag', 'serial', 'total')}
                h.update(_id=str(uuid4()), status='active', out=0, reserved=0, version=1,
                         name_key=h['name'].casefold(), asset_key=h['asset_tag'].casefold())
                validate(h, [])
                self.db.hardware.insert_one(h, session=s)
                before, after, response = {}, {'hardware': h}, h
                project_id = None
            else:
                c = self.get('checkouts', entity, s) if command in ('return', 'correct') else None
                hardware_id = c['hardware_id'] if c else entity
                h = self.get('hardware', hardware_id, s)
                aa = list(self.db.allocations.find({'hardware_id': hardware_id}, session=s))
                # All commands also replace hardware: it is the serialization point.
                expected = c if c else h
                if 'expected_version' in data:
                    require(data['expected_version'] == expected['version'], 'Stale version; refresh current values')
                if c:
                    data2 = dict(data, project_id=c['project_id'])
                else:
                    data2 = data
                before = {'hardware': h, 'allocations': aa, 'checkout': c}
                nh, na, nc = transition(h, aa, command, data2, c)
                result = self.db.hardware.replace_one({'_id': h['_id'], 'version': h['version']}, nh, session=s)
                require(result.modified_count == 1, 'Concurrent change; retry')
                for a in na:
                    self.db.allocations.replace_one({'_id': a['_id']}, a, upsert=True, session=s)
                if nc:
                    if command == 'checkout':
                        nc.update(_id=str(uuid4()), created_at=now())
                    self.db.checkouts.replace_one({'_id': nc['_id']}, nc, upsert=True, session=s)
                after = {'hardware': nh, 'allocations': na, 'checkout': nc}
                response = nc if nc else nh
                project_id = data2.get('project_id')
            event = {'_id': str(uuid4()), 'actor': actor, 'operation': command,
                     'entity': response['_id'], 'hardware_id': after['hardware']['_id'],
                     'project_id': project_id, 'user_id': (after.get('checkout') or {}).get('user_id'), 'reason': data['reason'], 'timestamp': now(),
                     'before': before, 'after': after, 'request_id': request_id}
            self.db.audit.insert_one(event, session=s)
            response = clean(response)
            self.db.requests.insert_one({'_id': request_id, 'fingerprint': fingerprint,
                                         'response': response}, session=s)
            return response

        try:
            with self.client.start_session() as s:
                return s.with_transaction(work, read_concern=ReadConcern('snapshot'),
                                          write_concern=WriteConcern('majority'))
        except DuplicateKeyError:
            prior = self.db.requests.find_one({'_id': request_id})
            if prior and prior['fingerprint'] == fingerprint:
                return prior['response']
            raise HTTPException(409, 'Duplicate asset tag or idempotency key') from None
        except Conflict as e:
            raise HTTPException(409, str(e)) from None
