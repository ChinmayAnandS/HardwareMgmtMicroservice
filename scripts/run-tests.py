import os
import subprocess
from pathlib import Path
from urllib.parse import quote

password=quote(Path('/run/secrets/mongo_app').read_text().strip(),safe='')
Path('/tmp/test-uri').write_text(f'mongodb://hardware_app:{password}@mongo:27017/hardware_test?replicaSet=rs0&authSource=hardware_test')
os.environ['RUN_INTEGRATION']='1'
subprocess.run(['pytest','backend/tests','e2e','--cov=app','--cov-report=term-missing',
                '--cov-report=xml:/results/coverage.xml','--junitxml=/results/junit.xml'],check=True)
