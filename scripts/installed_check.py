"""Build a wheel and install into an isolated target, with pip networking disabled."""
import os
import json
from pathlib import Path
import subprocess
import sys
import tempfile

from PIL import Image


def main():
    root=Path(__file__).resolve().parents[1]
    with tempfile.TemporaryDirectory() as tmp:
        tmp=Path(tmp);wheels=tmp/'wheels';wheels.mkdir()
        subprocess.run([sys.executable,'-m','pip','wheel','--no-index','--no-deps','--no-build-isolation','--wheel-dir',str(wheels),str(root)],check=True)
        target=tmp/'installed'
        subprocess.run([sys.executable,'-m','pip','install','--no-index','--no-deps','--target',str(target),str(next(wheels.glob('*.whl')))],check=True)
        # Pillow comes from the already-provisioned environment, copied into isolation.
        import PIL
        import shutil
        site=Path(PIL.__file__).parent.parent
        shutil.copytree(site/'PIL',target/'PIL')
        for directory in site.glob('pillow.libs'):shutil.copytree(directory,target/directory.name)
        Image.new('RGB',(8,4),(240,120,60)).save(tmp/'fixture.png')
        executable=target/'bin'/'image-score'
        env={**os.environ,'PYTHONPATH':str(target),'PIP_NO_INDEX':'1'}
        # -S avoids site packages and the editable checkout. Execute installed entry script.
        subprocess.run([sys.executable,'-S',str(executable),str(tmp/'fixture.png'),str(tmp/'bundle')],cwd=tmp,env=env,check=True)
        assert (tmp/'bundle'/'report.html').stat().st_size>1000
        assert len(list((tmp/'bundle').iterdir()))==6
        (tmp/'fixture.png').unlink()
        edits = {'format': 'image-score-edit', 'version': 1, 'tempo': 90,
                 'transpose': 7, 'velocity': {'n001': 40}, 'mute': {'n000': True}}
        (tmp/'edits.json').write_text(json.dumps(edits))
        subprocess.run([sys.executable, '-S', str(target/'bin'/'image-score-edit'),
                        str(tmp/'bundle'), str(tmp/'edits.json'), str(tmp/'edited')],
                       cwd=tmp, env=env, check=True)
        score=json.loads((tmp/'edited'/'score.json').read_text())
        original=json.loads((tmp/'bundle'/'score.json').read_text())
        assert score['version']==2 and score['settings']['tempo']==90
        assert score['events'][0]['muted'] and score['events'][1]['velocity']==40
        assert score['events'][1]['pitch']==original['events'][1]['pitch']+7
        assert len(list((tmp/'edited').iterdir()))==6
        # A second import exercises installed version-2 compatibility and unmuting.
        (tmp/'edits.json').write_text(json.dumps({'format':'image-score-edit','version':1,'mute':{'n000':False}}))
        subprocess.run([sys.executable, '-S', str(target/'bin'/'image-score-edit'),
                        str(tmp/'edited'), str(tmp/'edits.json'), str(tmp/'unmuted')],
                       cwd=tmp, env=env, check=True)
        assert not json.loads((tmp/'unmuted'/'score.json').read_text())['events'][0]['muted']
        subprocess.run([sys.executable, '-S', str(target/'bin'/'image-score-editor'),
                        str(tmp/'edited'), str(tmp/'editor')],cwd=tmp,env=env,check=True)
        html=(tmp/'editor'/'editor.html').read_text()
        assert '__SCRIPT__' not in html and '__PAYLOAD__' not in html
        from playwright.sync_api import sync_playwright
        with sync_playwright() as pw:
            browser=pw.chromium.launch(args=['--no-sandbox'])
            page=browser.new_page(offline=True)
            page.goto((tmp/'editor'/'editor.html').as_uri())
            assert page.locator('#notes button').count()==32
            page.locator('#transpose').fill('-2')
            page.locator('#transpose').press('Enter')
            with page.expect_download() as download:
                page.locator('#export').click()
            download.value.save_as(tmp/'browser-edits.json')
            expected=page.evaluate('score')
            browser.close()
        subprocess.run([sys.executable, '-S', str(target/'bin'/'image-score-edit'),
                        str(tmp/'edited'), str(tmp/'browser-edits.json'), str(tmp/'browser-rendered')],
                       cwd=tmp,env=env,check=True)
        assert json.loads((tmp/'browser-rendered'/'score.json').read_text())==expected
    print('Isolated installed entry points: PASS (offline conversion, source deletion, editing, re-editing, browser editor export and CLI render)')


if __name__=='__main__':main()
