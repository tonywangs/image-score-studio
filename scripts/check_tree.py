"""Check the versioned/proposed source tree against publication size limits."""
from pathlib import Path
import subprocess

root=Path(__file__).resolve().parents[1]
paths=subprocess.check_output(['git','ls-files','--cached','--others','--exclude-standard','-z'],cwd=root).split(b'\0')
files=sorted({name.decode() for name in paths if name})
assert len(files)<=1000, 'more than 1000 source files'
total=0
for name in files:
    path=root/name
    assert path.is_file() and not path.is_symlink(), f'not a regular file: {name}'
    size=path.stat().st_size
    assert size<=10*1024*1024, f'file exceeds 10 MiB: {name}'
    assert not name.endswith('.log') or name=='results/tests.log', f'non-test log: {name}'
    total+=size
assert total<=32*1024*1024, 'source tree exceeds 32 MiB'
print(f'Publication limits: {len(files)} files, {total} bytes; PASS')
