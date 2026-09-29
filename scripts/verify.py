"""One verification command; all dependencies must already be provisioned."""
import os
from pathlib import Path
import subprocess
import sys

root=Path(__file__).resolve().parents[1]
os.chdir(root)
if 'PLAYWRIGHT_BROWSERS_PATH' not in os.environ and Path('/tmp/image-score-browsers').is_dir():
    os.environ['PLAYWRIGHT_BROWSERS_PATH']='/tmp/image-score-browsers'
for command in [[sys.executable,'-m','unittest','discover','-s','tests','-v'],[sys.executable,'scripts/installed_check.py'],[sys.executable,'scripts/benchmark.py','--check','results/benchmark.json']]:
    subprocess.run(command,check=True)
print('All verification stages passed.')
