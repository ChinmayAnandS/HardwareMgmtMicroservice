"""Deploy the two containers after bootstrap, Atlas setup and identity setup."""
import hashlib
import json
import subprocess
import sys
from pathlib import Path


def az(*args):
    return json.loads(subprocess.check_output(['az', *args, '-o', 'json'],text=True))


stage=sys.argv[1]
assert stage in ('dev','prod')
account=az('account','show')
suffix=hashlib.sha256(f"{account['id']}-{stage}".encode()).hexdigest()[:10]
rg=f'hardware-{stage}';acr=f'hw{suffix}';vault=f'hw-{suffix}'
env=az('containerapp','env','show','-g',rg,'-n',rg)
identity=az('identity','show','-g',rg,'-n','runtime')['id']
auth=json.loads(Path(f'.secrets/identity-{stage}.json').read_text())
registry=az('acr','show','-n',acr)['loginServer']
# Local Docker images must be tested/scanned before invocation; CI handles subsequent releases.
subprocess.run(['az','acr','login','--name',acr],check=True)
for component,context in [('api','backend'),('ui','ui')]:
    image=f'{registry}/hardware-{component}:bootstrap'
    subprocess.run(['docker','build','-t',image,context],check=True)
    subprocess.run(['docker','push',image],check=True)
connection=az('monitor','app-insights','component','show','-g',rg,'--app','hardware')['connectionString']
common={'location':env['location'],'identity':{'type':'UserAssigned','userAssignedIdentities':{identity:{}}},
        'properties':{'managedEnvironmentId':env['id'],'workloadProfileName':'Consumption'}}
for component,port,external in [('api',8000,False),('ui',8080,True)]:
    name='hardware-'+component
    # Resolve an immutable digest even for the initial deployment.
    digest=az('acr','manifest','show-metadata','--registry',acr,'--name',name+':bootstrap')['digest']
    container={'name':name,'image':f'{registry}/{name}@{digest}',
      'resources':{'cpu':0.25,'memory':'0.5Gi'},
      'probes':[{'type':kind,'httpGet':{'path':path,'port':port,'httpHeaders':[{'name':'Host','value':'localhost'}]},
                 'periodSeconds':interval,'timeoutSeconds':5,'failureThreshold':failure} for kind,path,interval,failure in
                 [('Liveness','/health',30,3),('Readiness','/ready',10,3),('Startup','/health',5,30)]]}
    configuration={'activeRevisionsMode':'Single','ingress':{'external':external,'targetPort':port,
          'transport':'auto','allowInsecure':False},'registries':[{'server':registry,'identity':identity}]}
    if component=='api':
        configuration['secrets']=[{'name':name,'keyVaultUrl':f'https://{vault}.vault.azure.net/secrets/{name}',
                                    'identity':identity} for name in ('mongo-uri','oidc-secret')]
        values={'ENVIRONMENT':'production','PUBLIC_ORIGIN':auth['origin'],'MONGO_DATABASE':'hardware',
                'OIDC_ISSUER':auth['issuer'],'OIDC_CLIENT_ID':auth['client_id'],
                'APPLICATIONINSIGHTS_CONNECTION_STRING':connection}
        container['env']=[{'name':k,'value':v} for k,v in values.items()]+[
            {'name':'MONGO_URI','secretRef':'mongo-uri'},{'name':'OIDC_CLIENT_SECRET','secretRef':'oidc-secret'}]
    else:
        api=az('containerapp','show','-g',rg,'-n','hardware-api')['properties']['configuration']['ingress']['fqdn']
        container['env']=[{'name':'API_ORIGIN','value':'https://'+api},
                          {'name':'PUBLIC_HOST','value':auth['origin'].split('://')[1]},
                          {'name':'PUBLIC_SCHEME','value':'https'}]
    spec=json.loads(json.dumps(common))
    spec['properties'].update(configuration=configuration,template={'containers':[container],
        'scale':{'minReplicas':0,'maxReplicas':3,'rules':[{'name':'http','http':{'metadata':{'concurrentRequests':'20'}}}]}})
    path=Path(f'.secrets/{name}-{stage}.json');path.write_text(json.dumps(spec))
    az('containerapp','create','-g',rg,'-n',name,'--yaml',str(path))
print(auth['origin'])
