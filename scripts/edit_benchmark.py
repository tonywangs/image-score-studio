"""Deterministic edit workloads with fresh-process Linux wait4 measurements."""
import argparse
import hashlib
from importlib.metadata import version
import json
import os
from pathlib import Path
import platform
import subprocess
import sys
import tempfile
import time

from fixtures import generate
from image_score import core


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--output')
    parser.add_argument('--check')
    args = parser.parse_args()
    records = []
    workloads = [
        ('identity', 'gradient.png', 8, 4, 120, {}),
        ('combined', 'photo.jpg', 7, 3, 120, {'tempo':73, 'transpose':12, 'velocity':{'n000':40}, 'mute':{'n001':True}}),
        ('all-muted', 'gradient.png', 8, 4, 120, {'mute':{f'n{i:03d}':True for i in range(32)}}),
        ('max-notes-pixels', 'large.png', 16, 16, 240, {'transpose':-12}),
        ('max-duration', 'alpha.png', 10, 6, 120, {'tempo':30, 'transpose':7})]
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        generate(root/'inputs')
        for name, filename, columns, rows, tempo, changes in workloads:
            source = root/(name+'-source')
            core.convert(root/'inputs'/filename, source, columns=columns, rows=rows, tempo=tempo)
            # Each worker receives only a saved bundle and explicit edit file.
            output = root/name
            spec = root/(name+'.json')
            spec.write_bytes(core.canonical(dict(format='image-score-edit', version=1, **changes)))
            command = [sys.executable, '-m', 'image_score.cli', 'edit', str(source), str(spec), str(output)]
            start = time.perf_counter()
            process = subprocess.Popen(command, stdout=subprocess.DEVNULL)
            _, status, usage = os.wait4(process.pid, 0)
            process.returncode = os.waitstatus_to_exitcode(status)
            elapsed = time.perf_counter()-start
            if process.returncode:
                raise RuntimeError(f'edit workload failed: {name}')
            score = json.loads((output/'score.json').read_bytes())
            records.append(dict(name=name, seconds=round(elapsed, 6), peak_rss_kib=usage.ru_maxrss,
                                notes=len(score['events']), audio_seconds=score['sample_count']/score['sample_rate'],
                                parent_bundle_sha256=score['provenance']['parent_bundle_sha256'],
                                edits_sha256=hashlib.sha256(spec.read_bytes()).hexdigest(),
                                artifacts={p.name:dict(bytes=p.stat().st_size, sha256=hashlib.sha256(p.read_bytes()).hexdigest())
                                           for p in sorted(output.iterdir())}))
    result = dict(python=platform.python_version(), platform=platform.platform(), machine=platform.machine(),
                  dependencies={name:version(name) for name in ('Pillow','mido','playwright')}, workloads=records)
    if args.check:
        reference = json.loads(Path(args.check).read_text())['workloads']
        for actual, expected in zip(records, reference, strict=True):
            for key in ('name', 'notes', 'audio_seconds', 'parent_bundle_sha256', 'edits_sha256', 'artifacts'):
                if actual[key] != expected[key]:
                    raise AssertionError(f"reference mismatch: {actual['name']} {key}")
        print('All edited workload artifact hashes match the recorded reference.')
    encoded = json.dumps(result, indent=2)+'\n'
    if args.output:
        Path(args.output).write_text(encoded)
    else:
        print(encoded, end='')


if __name__ == '__main__':
    main()
