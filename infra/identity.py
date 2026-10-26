"""Provision a single-tenant Entra web application and assign the invoking admin."""
import json
import hashlib
import os
import subprocess
import sys
from pathlib import Path


def az(*args):
    return json.loads(subprocess.check_output(['az', *args, '-o', 'json'],text=True))


stage=sys.argv[1]
assert stage in ('dev','prod')
env=json.loads(Path(f'.secrets/environment-{stage}.json').read_text())
origin=f"https://hardware-ui.{env['properties']['defaultDomain']}"
roles=[{'allowedMemberTypes':['User'],'description':description,'displayName':name,
        'id':rid,'isEnabled':True,'value':value} for name,value,rid,description in [
        ('Hardware administrator','Hardware.Admin','6f3dd740-f480-4304-96fd-fc8d119bb759','Manage hardware and inventory'),
        ('Hardware auditor','Hardware.Auditor','7b287791-9eca-4df3-a110-d3a92236abb2','Read inventory and audit history')]]
root=Path('.secrets');root.mkdir(mode=0o700,exist_ok=True)
rolefile=root/f'roles-{stage}.json';rolefile.write_text(json.dumps(roles))
application=az('ad','app','create','--display-name',f'hardware-{stage}',
    '--sign-in-audience','AzureADMyOrg','--web-redirect-uris',origin+'/auth/callback',
    '--app-roles','@'+str(rolefile))
sp=az('ad','sp','create','--id',application['appId'])
admin=az('ad','signed-in-user','show')['id']
body={'principalId':admin,'resourceId':sp['id'],'appRoleId':roles[0]['id']}
az('rest','--method','POST','--url',f"https://graph.microsoft.com/v1.0/users/{admin}/appRoleAssignments",'--body',json.dumps(body))
az('ad','sp','update','--id',sp['id'],'--set','appRoleAssignmentRequired=true')
credential=az('ad','app','credential','reset','--id',application['appId'],'--append','--years','1')
tenant=az('account','show')['tenantId']
subscription=az('account','show')['id']
suffix=hashlib.sha256(f'{subscription}-{stage}'.encode()).hexdigest()[:10]
secretfile=root/f'oidc-secret-{stage}'
fd=os.open(secretfile,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
with os.fdopen(fd,'w') as f:
    f.write(credential['password'])
az('keyvault','secret','set','--vault-name',f'hw-{suffix}','--name','oidc-secret','--file',str(secretfile))
secretfile.unlink()
(root/f'identity-{stage}.json').write_text(json.dumps({'client_id':application['appId'],
    'issuer':f'https://login.microsoftonline.com/{tenant}/v2.0','origin':origin},indent=2))
print('Entra application created. Rotate its credential before its one-year expiry.')
