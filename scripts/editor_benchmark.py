"""Bounded editor generation, offline browser load and DOM measurements."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import platform
import tempfile
import time
from importlib.metadata import version

from PIL import Image
from playwright.sync_api import sync_playwright
from image_score import core, editor


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--output')
    parser.add_argument('--check')
    args=parser.parse_args()
    if 'PLAYWRIGHT_BROWSERS_PATH' not in os.environ and Path('/tmp/image-score-browsers').is_dir():
        os.environ['PLAYWRIGHT_BROWSERS_PATH']='/tmp/image-score-browsers'
    records=[]
    with tempfile.TemporaryDirectory() as tmp, sync_playwright() as pw:
        root=Path(tmp)
        browser=pw.chromium.launch(args=['--no-sandbox'])
        for name,side,columns,rows,tempo in [('small',64,8,4,120),('max-notes-pixels',2000,16,16,240),('max-duration',128,10,6,30)]:
            image=root/(name+'.png')
            # Easily reproduced, spatially varied RGB pattern. No external dataset.
            im=Image.new('RGB',(side,side))
            im.putdata([((x*13+y*3)%256,(x*7+y*11)%256,(x+y*17)%256) for y in range(side) for x in range(side)])
            im.save(image)
            bundle=root/(name+'-bundle');output=root/(name+'-editor')
            core.convert(image,bundle,columns=columns,rows=rows,tempo=tempo)
            image.unlink()
            start=time.perf_counter();editor.create_editor(bundle,output);generation=time.perf_counter()-start
            html=output/'editor.html'
            context=browser.new_context(offline=True)
            requests=[];errors=[]
            page=context.new_page();page.on('request',lambda r:requests.append(r.url));page.on('pageerror',lambda e:errors.append(str(e)))
            start=time.perf_counter();page.goto(html.as_uri());page.locator('#image').evaluate('(img)=>img.decode()');load=time.perf_counter()-start
            counts=page.evaluate('({elements:document.querySelectorAll("*").length,buttons:buttons.length,samples:score.sample_count})')
            assert counts['buttons']==columns*rows and counts['elements']<400
            start=time.perf_counter()
            sound=page.evaluate('''()=>{const c=new OfflineAudioContext(1,score.sample_count,16000),b=synthesize(c);return {length:b.length,channels:b.numberOfChannels};}''')
            synthesis=time.perf_counter()-start
            assert sound['length']==counts['samples'] and sound['channels']==1
            assert not errors and not [u for u in requests if u.startswith(('http:','https:'))]
            records.append(dict(name=name,pixels=side*side,notes=columns*rows,audio_seconds=counts['samples']/16000,
                generation_seconds=round(generation,6),browser_load_seconds=round(load,6),
                synthesis_roundtrip_seconds=round(synthesis,6),mounted_elements=counts['elements'],
                html_bytes=html.stat().st_size,html_sha256=hashlib.sha256(html.read_bytes()).hexdigest(),
                audio_buffer_bytes=counts['samples']*4))
            context.close()
        browser.close()
    if args.check:
        previous=json.loads(Path(args.check).read_text())['workloads']
        for actual,expected in zip(records,previous,strict=True):
            for key in ('name','pixels','notes','audio_seconds','mounted_elements','html_bytes','html_sha256','audio_buffer_bytes'):
                assert actual[key]==expected[key], (actual['name'],key)
    result={'python':platform.python_version(),'platform':platform.platform(),
            'dependencies':{n:version(n) for n in ['Pillow','playwright']},
            'measurement':'One local sample per workload; wall times are descriptive, not performance thresholds. Browser load includes navigation and image decode wait; synthesis includes browser roundtrip.',
            'workloads':records}
    encoded=json.dumps(result,indent=2)+'\n'
    if args.output:Path(args.output).write_text(encoded)
    else:print(encoded,end='')


if __name__=='__main__':main()
