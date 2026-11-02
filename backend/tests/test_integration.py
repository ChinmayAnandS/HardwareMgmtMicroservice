"""Real MongoDB transaction and HTTP tests. No in-memory substitute."""
import os
import secrets
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from uuid import uuid4
import pytest

pytestmark = pytest.mark.integration
if not os.getenv('RUN_INTEGRATION'):
    pytest.skip('Set RUN_INTEGRATION=1 with a provisioned replica set', allow_module_level=True)

from fastapi.testclient import TestClient
from pymongo import MongoClient
from pymongo.errors import OperationFailure
from app.config import Settings
from app.main import create_app
from app.auth import digest


SERVICE_KEY='integration-test-service-key-'+'x'*16


@pytest.fixture
def client():
    cfg=Settings(environment='test',database='hardware_test',origin='http://localhost:8080',
                 dev_code=secrets.token_urlsafe(32),service_api_key=SERVICE_KEY)
    assert cfg.database.endswith('_test')
    root_password=Path(os.environ['ROOT_PASSWORD_FILE']).read_text().strip()
    root=MongoClient('mongodb://mongo:27017/?replicaSet=rs0',username='root',password=root_password)
    db=root[cfg.database]
    for c in ('hardware','allocations','checkouts','audit','requests','sessions','limits'):
        db[c].delete_many({})
    with TestClient(create_app(cfg)) as client:
        client.headers['Origin']=cfg.origin
        assert client.post('/auth/dev',json={'code':cfg.dev_code}).status_code==200
        client.headers['X-CSRF-Token']=client.get('/api/me').json()['csrf']
        client.root_db=db
        yield client
    root.close()


def post(client,path,body,key=None):
    return client.post(path,json=body,headers={'Idempotency-Key':key or uuid4().hex})


def hardware(client,quantity=5):
    r=post(client,'/api/hardware',{'name':'Laptop','category':'Computer','asset_tag':uuid4().hex,
        'total':quantity,'reason':'Test stock'})
    assert r.status_code==201,r.text
    return r.json()


def allocate(client,h,n=5):
    r=client.put(f"/api/hardware/{h['_id']}/allocations/p",json={
        'quantity':n,'reason':'Project reservation','expected_version':h['version']},
        headers={'Idempotency-Key':uuid4().hex})
    assert r.status_code==200,r.text
    return r.json()


def test_crud_and_adjustments(client):
    h=hardware(client)
    path='/api/hardware/'+h['_id']
    r=client.patch(path,json={'name':'Updated','expected_version':1,'reason':'Rename asset'},headers={'Idempotency-Key':uuid4().hex})
    assert r.status_code==200
    assert post(client,path+'/adjustments',{'delta':-6,'expected_version':2,'confirmed':True,'reason':'Invalid removal'}).status_code==409
    r=post(client,path+'/adjustments',{'delta':2,'expected_version':2,'confirmed':True,'reason':'Purchase units'})
    assert r.json()['total']==7
    assert post(client,path+'/retire',{'expected_version':3,'confirmed':True,'reason':'End of life'}).status_code==200
    assert client.get(path).json()['status']=='retired'


def test_checkout_partial_return_and_correction(client):
    h=hardware(client);allocate(client,h)
    c=post(client,'/api/checkouts',{'hardware_id':h['_id'],'project_id':'p','user_id':'user1','quantity':3,'reason':'Lab work'}).json()
    path='/api/checkouts/'+c['_id']
    r=post(client,path+'/returns',{'quantity':1,'expected_version':1,'reason':'Partial return'})
    assert r.json()['returned']==1
    r=post(client,path+'/corrections',{'returned':0,'expected_version':2,'reason':'Return entered by mistake','confirmed':True})
    assert r.json()['returned']==0
    assert post(client,path+'/returns',{'quantity':4,'expected_version':3,'reason':'Too many'}).status_code==409
    assert client.get('/api/hardware/'+h['_id']).json()['available']==2


def test_concurrent_checkout_never_oversells(client):
    h=hardware(client);allocate(client,h)
    def attempt(i):
        return post(client,'/api/checkouts',{'hardware_id':h['_id'],'project_id':'p','user_id':f'u{i}','quantity':1,'reason':'Concurrent issue'}).status_code
    with ThreadPoolExecutor(max_workers=10) as pool:
        statuses=list(pool.map(attempt,range(20)))
    assert statuses.count(201)==5,statuses
    assert statuses.count(409)==15,statuses
    assert client.get('/api/hardware/'+h['_id']).json()['available']==0
    assert client.root_db.audit.count_documents({'operation':'checkout'})==5


def test_idempotency_and_conflicting_reuse(client):
    h=hardware(client)
    key=uuid4().hex
    body={'delta':1,'expected_version':1,'reason':'One purchase','confirmed':True}
    path='/api/hardware/'+h['_id']+'/adjustments'
    a=post(client,path,body,key);b=post(client,path,body,key)
    assert a.status_code==b.status_code==200
    assert a.json()==b.json()
    assert post(client,path,dict(body,delta=2),key).status_code==409
    assert client.root_db.audit.count_documents({'operation':'adjust'})==1


def test_stale_version_confirmation_validation_and_duplicate_asset(client):
    h=hardware(client)
    body={'delta':1,'expected_version':9,'reason':'Purchase','confirmed':True}
    assert post(client,'/api/hardware/'+h['_id']+'/adjustments',body).status_code==409
    del body['confirmed']
    assert post(client,'/api/hardware/'+h['_id']+'/adjustments',body).status_code==422
    assert post(client,'/api/hardware',{'name':'Other','category':'Computer','asset_tag':h['asset_tag'].upper(),'total':2,'reason':'Duplicate'}).status_code==409
    assert post(client,'/api/hardware',{'name':'Bad','category':'Computer','asset_tag':'bad','total':-1,'reason':'Invalid'}).status_code==422


def test_auth_csrf_and_rbac(client):
    h=hardware(client)
    cookie=client.cookies.get('hardware_session')
    client.cookies.clear()
    assert client.get('/api/hardware').status_code==401
    client.cookies.set('hardware_session',cookie)
    client.headers['X-CSRF-Token']='invalid'
    assert post(client,'/api/hardware/'+h['_id']+'/retire',{'expected_version':1,'reason':'Retire','confirmed':True}).status_code==403
    client.headers['X-CSRF-Token']=client.get('/api/me').json()['csrf']
    client.root_db.sessions.update_one({'_id':digest(cookie)},{'$set':{'data.user.roles':['Hardware.Auditor']}})
    assert client.get('/api/hardware').status_code==200
    assert post(client,'/api/hardware/'+h['_id']+'/retire',{'expected_version':1,'reason':'Retire','confirmed':True}).status_code==403
    client.root_db.sessions.update_one({'_id':digest(cookie)},{'$set':{'data.user.roles':[]}})
    assert client.get('/api/hardware').status_code==403


def test_audit_immutable_at_database_and_api(client):
    hardware(client)
    event=client.get('/api/transactions').json()['items'][0]
    assert all(k in event for k in ('actor','operation','entity','before','after','reason','timestamp'))
    assert client.delete('/api/transactions/'+event['_id']).status_code==404
    db=client.app.state.store.db
    with pytest.raises(OperationFailure):
        db.audit.update_one({'_id':event['_id']},{'$set':{'reason':'tamper'}})
    with pytest.raises(OperationFailure):
        db.audit.delete_one({'_id':event['_id']})


def test_search_pagination_filters_and_missing_entity(client):
    hardware(client);hardware(client)
    result=client.get('/api/hardware?q=lap&size=1').json()
    assert result['total']==2 and len(result['items'])==1
    assert client.get('/api/hardware/missing').status_code==404
    assert client.get('/api/transactions?start=2026-01-01T00:00:00').status_code==422
    assert client.get('/api/hardware?sort=$where').status_code==422


def test_allocation_floor_and_audit_filter(client):
    h=hardware(client);h=allocate(client,h)
    post(client,'/api/checkouts',{'hardware_id':h['_id'],'project_id':'p','user_id':'u','quantity':3,'reason':'Equipment issue'})
    h=client.get('/api/hardware/'+h['_id']).json()
    r=client.put(f"/api/hardware/{h['_id']}/allocations/p",json={'quantity':2,'reason':'Too small','expected_version':h['version']},headers={'Idempotency-Key':uuid4().hex})
    assert r.status_code==409
    result=client.get('/api/transactions?project_id=p').json()
    assert result['total']==2


def test_concurrent_duplicate_submission_commits_once(client):
    h=hardware(client)
    key=uuid4().hex
    body={'delta':1,'expected_version':1,'reason':'Single physical delivery','confirmed':True}
    path='/api/hardware/'+h['_id']+'/adjustments'
    with ThreadPoolExecutor(max_workers=5) as pool:
        responses=list(pool.map(lambda _:post(client,path,body,key),range(10)))
    assert all(r.status_code==200 for r in responses)
    assert client.get('/api/hardware/'+h['_id']).json()['total']==6
    assert client.root_db.audit.count_documents({'operation':'adjust'})==1


def test_failure_to_append_audit_rolls_back_stock_and_receipt(client):
    h=hardware(client)
    root=client.root_db
    role=root.command('rolesInfo','hardwareApp',showPrivileges=True)['roles'][0]
    original=role['privileges']
    without_audit=[p for p in original if p['resource'].get('collection')!='audit']
    try:
        root.command('updateRole','hardwareApp',privileges=without_audit,roles=[])
        r=post(client,'/api/hardware/'+h['_id']+'/adjustments',{
            'delta':1,'expected_version':1,'reason':'Audit permission failure','confirmed':True})
        assert r.status_code==503,r.text
    finally:
        root.command('updateRole','hardwareApp',privileges=original,roles=[])
    assert root.hardware.find_one({'_id':h['_id']})['total']==5
    assert root.audit.count_documents({})==1
    assert root.requests.count_documents({})==1


def test_origin_and_expired_session(client):
    from datetime import datetime, timedelta, timezone
    h=hardware(client)
    client.headers['Origin']='https://attacker.invalid'
    assert post(client,'/api/hardware/'+h['_id']+'/retire',{
        'expected_version':1,'reason':'Forged origin','confirmed':True}).status_code==403
    cookie=client.cookies.get('hardware_session')
    client.root_db.sessions.update_one({'_id':digest(cookie)},
        {'$set':{'expires_at':datetime.now(timezone.utc)-timedelta(seconds=1)}})
    assert client.get('/api/hardware').status_code==401


def service(client):
    """A caller with no browser session: only the X-API-Key header."""
    other=TestClient(client.app)
    other.headers['X-API-Key']=SERVICE_KEY
    return other


def test_service_key_needs_no_login_and_request_number_is_applied_once(client):
    h=hardware(client)
    svc=service(client)
    r=svc.put(f"/api/hardware/{h['_id']}/allocations/proj-1",json={'quantity':3,'reason':'Project request','request_id':'ALLOC-1001'})
    assert r.status_code==200,r.text
    again=svc.put(f"/api/hardware/{h['_id']}/allocations/proj-1",json={'quantity':3,'reason':'Project request','request_id':'ALLOC-1001'})
    assert again.status_code==200 and again.json()==r.json()
    body={'hardware_id':h['_id'],'project_id':'proj-1','user_id':'student-7','quantity':2,'reason':'Lab laptops','request_id':'REQ-1001'}
    first=svc.post('/api/checkouts',json=body)
    retry=svc.post('/api/checkouts',json=body)
    assert first.status_code==retry.status_code==201,first.text
    assert first.json()==retry.json()
    assert client.root_db.checkouts.count_documents({})==1
    assert client.get(f"/api/hardware/{h['_id']}").json()['out']==2
    ret={'quantity':1,'reason':'One returned','request_id':'RET-1001'}
    a=svc.post(f"/api/checkouts/{first.json()['_id']}/returns",json=ret)
    b=svc.post(f"/api/checkouts/{first.json()['_id']}/returns",json=ret)
    assert a.status_code==b.status_code==200,a.text
    assert b.json()['returned']==1
    assert client.get(f"/api/hardware/{h['_id']}").json()['out']==1
    events=list(client.root_db.audit.find({'operation':{'$in':['checkout','return','allocate']}}))
    assert len(events)==3 and {e['actor'] for e in events}=={'service:project-service'}


def test_service_key_limits_and_request_number_validation(client):
    h=hardware(client)
    svc=service(client)
    assert svc.get('/api/hardware').status_code==200
    assert svc.get('/api/me').status_code==403
    assert svc.post('/api/hardware',json={'name':'X','category':'Y','asset_tag':uuid4().hex,'total':1,'reason':'Not allowed'}).status_code==403
    assert svc.post(f"/api/hardware/{h['_id']}/adjustments",json={'delta':1,'expected_version':1,'reason':'Not allowed','confirmed':True}).status_code==403
    bad=TestClient(client.app); bad.headers['X-API-Key']='wrong'
    assert bad.get('/api/hardware').status_code==401
    assert TestClient(client.app).get('/api/hardware').status_code==401
    body={'hardware_id':h['_id'],'project_id':'p','user_id':'u','quantity':1,'reason':'Missing number'}
    assert svc.post('/api/checkouts',json=body).status_code==422
    assert svc.post('/api/checkouts',json=dict(body,request_id='A-1'),headers={'Idempotency-Key':'B-2'}).status_code==422
    svc.put(f"/api/hardware/{h['_id']}/allocations/p",json={'quantity':1,'reason':'Reserve one','request_id':'ALLOC-1'})
    assert svc.post('/api/checkouts',json=dict(body,request_id='SAME-1')).status_code==201
    assert svc.post('/api/checkouts',json=dict(body,request_id='SAME-1',quantity=2)).status_code==409
