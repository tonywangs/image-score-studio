"""Fresh-process CLI workloads; Linux ru_maxrss is reported in KiB."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import platform
import subprocess
import sys
import tempfile
import time

from fixtures import generate


def main():
    p=argparse.ArgumentParser();p.add_argument('--output');p.add_argument('--check');args=p.parse_args()
    records=[]
    with tempfile.TemporaryDirectory() as tmp:
        root=Path(tmp);generate(root/'inputs')
        for name,image,cols,rows,tempo in [('small','gradient.png',8,4,120),('jpeg','photo.jpg',7,3,73),('silence','black.png',4,4,120),('max-notes','large.png',16,16,240),('max-duration','alpha.png',10,6,30)]:
            output=root/name
            command=[sys.executable,'-m','image_score.cli',str(root/'inputs'/image),str(output),'--columns',str(cols),'--rows',str(rows),'--tempo',str(tempo)]
            # wait4 reports this particular child's peak, not a cumulative maximum.
            start=time.perf_counter()
            process=subprocess.Popen(command,stdout=subprocess.DEVNULL)
            _,status,usage=os.wait4(process.pid,0);process.returncode=os.waitstatus_to_exitcode(status)
            elapsed=time.perf_counter()-start
            if process.returncode:raise RuntimeError(f'benchmark failed: {name}')
            score=json.loads((output/'score.json').read_text())
            records.append(dict(name=name,seconds=round(elapsed,6),peak_rss_kib=usage.ru_maxrss,notes=len(score['events']),audio_seconds=score['sample_count']/score['sample_rate'],input_sha256=score['input_sha256'],artifacts={p.name:dict(bytes=p.stat().st_size,sha256=hashlib.sha256(p.read_bytes()).hexdigest()) for p in sorted(output.iterdir())}))
    result=json.dumps(dict(python=platform.python_version(),platform=platform.platform(),workloads=records),indent=2)+'\n'
    if args.check:
        reference=json.loads(Path(args.check).read_text())['workloads']
        for actual,expected in zip(records,reference,strict=True):
            for key in ['name','input_sha256','artifacts']:
                if actual[key]!=expected[key]:raise AssertionError(f"reference mismatch: {actual['name']} {key}")
        print('All workload artifact hashes match the recorded reference.')
    if args.output:Path(args.output).write_text(result)
    else:print(result,end='')


if __name__=='__main__':main()
