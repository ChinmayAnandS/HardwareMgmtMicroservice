"""Read secret through a masked terminal prompt and upload via a private file."""
import getpass
import hashlib
import json
import os
import subprocess
import sys
import tempfile
from urllib.parse import urlsplit

stage=sys.argv[1]
assert stage in ('dev','prod')
uri=getpass.getpass('Atlas application SRV URI (masked): ').strip()
parsed=urlsplit(uri)
if parsed.scheme!='mongodb+srv' or not parsed.hostname or parsed.username!='hardware_app':
    raise SystemExit('Use the hardware_app Atlas mongodb+srv URI')
if 'tls=false' in parsed.query.lower() or 'tlsallowinvalid' in parsed.query.lower():
    raise SystemExit('TLS verification must remain enabled')
subscription=json.loads(subprocess.check_output(['az','account','show','-o','json'],text=True))['id']
suffix=hashlib.sha256(f'{subscription}-{stage}'.encode()).hexdigest()[:10]
fd,path=tempfile.mkstemp()
try:
    with os.fdopen(fd,'w') as f:
        f.write(uri)
    subprocess.run(['az','keyvault','secret','set','--vault-name',f'hw-{suffix}',
                    '--name','mongo-uri','--file',path,'-o','none'],check=True)
finally:
    os.unlink(path)
