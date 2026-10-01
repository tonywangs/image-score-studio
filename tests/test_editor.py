"""Browser editor parity, offline workflow, resource and failure checks."""
import copy
from fractions import Fraction
import hashlib
import json
import os
from pathlib import Path
import random
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from PIL import Image
from playwright.sync_api import sync_playwright
from image_score import core, edit, editor
from test_browser import wait

if 'PLAYWRIGHT_BROWSERS_PATH' not in os.environ and Path('/tmp/image-score-browsers').is_dir():
    os.environ['PLAYWRIGHT_BROWSERS_PATH']='/tmp/image-score-browsers'


class EditorTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.root=Path(self.tmp.name)
        self.path=self.root/'source.png'
        im=Image.new('RGB',(16,16))
        im.putdata([(i*7%256,i*11%256,i*17%256) for i in range(256)])
        im.save(self.path)
        self.bundle=self.root/'bundle'
        core.convert(self.path,self.bundle,columns=4,rows=4)
        self.path.unlink()
        self.score,_,self.parent=edit.load_bundle(self.bundle)
        self.out=self.root/'editor'
        editor.create_editor(self.bundle,self.out)

    def tearDown(self):
        self.tmp.cleanup()

    def test_generation_validation_determinism_and_limits(self):
        other=self.root/'other'
        editor.create_editor(self.bundle,other)
        self.assertEqual((self.out/'editor.html').read_bytes(),(other/'editor.html').read_bytes())
        self.assertEqual([p.name for p in self.out.iterdir()],['editor.html'])
        with self.assertRaises(OSError):editor.create_editor(self.bundle,self.out)
        with self.assertRaises(ValueError):editor.create_editor(self.bundle,self.bundle/'child')
        loaded=edit.load_bundle(self.bundle)
        with patch.object(core,'MAX_FILE',100), patch.object(edit,'load_bundle',return_value=loaded):
            with self.assertRaisesRegex(ValueError,'10 MiB'):editor.create_editor(self.bundle,self.root/'large')
        self.assertFalse((self.root/'large').exists())
        (self.bundle/'report.html').write_text('changed')
        with self.assertRaisesRegex(ValueError,'checksum'):editor.create_editor(self.bundle,self.root/'bad')
        self.assertFalse((self.root/'bad').exists())

    def test_240_seeded_browser_python_and_fraction_reference(self):
        rng=random.Random(73191)
        cases=[];expected=[]
        for i in range(240):
            base=copy.deepcopy(self.score)
            if i%2:
                base=edit.apply_edits(base,{'format':'image-score-edit','version':1,'transpose':-30,
                     'mute':{'n001':True},'velocity':{'n002':0}},self.parent)
            low=min(e['pitch'] for e in base['events']);high=max(e['pitch'] for e in base['events'])
            spec={'format':'image-score-edit','version':1,'tempo':rng.randint(30,240),
                  'transpose':rng.randint(-low,119-high),
                  'velocity':{f'n{j:03d}':rng.randrange(128) for j in rng.sample(range(16),rng.randrange(17))},
                  'mute':{f'n{j:03d}':bool(rng.randrange(2)) for j in rng.sample(range(16),rng.randrange(17))}}
            transformed=edit.apply_edits(base,spec,self.parent)
            # Independent exact-rational reference, rather than calling Python helpers.
            us=round(Fraction(60_000_000,spec['tempo']))
            for j,(original,event) in enumerate(zip(base['events'],transformed['events'])):
                name=f'n{j:03d}'
                self.assertEqual(event['pitch'],original['pitch']+spec['transpose'])
                self.assertEqual(event['velocity'],spec['velocity'].get(name,original['velocity']))
                self.assertEqual(event['muted'],spec['mute'].get(name,original.get('muted',False)))
                self.assertEqual(event['start_sample'],round(Fraction(j*us*16000,2_000_000)))
                self.assertEqual(event['end_sample'],round(Fraction((j+1)*us*16000,2_000_000)))
                self.assertEqual(event['start_tick'],j*240)
            cases.append([base,spec]);expected.append(transformed)
        with sync_playwright() as pw:
            browser=pw.chromium.launch(args=['--no-sandbox'])
            page=browser.new_page(offline=True);page.goto((self.out/'editor.html').as_uri())
            actual=page.evaluate('(cases)=>cases.map(([base,spec])=>transform(base,spec))',cases)
            self.assertEqual(actual,expected)
            # JSON bytes match Python canonical output; ties-to-even is deliberate.
            exported=page.evaluate('(cases)=>cases.map(([base,spec])=>canonical(normalize(spec,base)))',cases)
            for text,(_,spec) in zip(exported,cases):self.assertEqual(text.encode(),core.canonical(edit.validate_edits(spec,{f'n{i:03d}' for i in range(16)})))
            self.assertEqual(page.evaluate('[roundEven(5,2),roundEven(7,2)]'),[2,4])
            browser.close()

    def test_offline_edit_preview_export_cli_reopen_and_failures(self):
        # Hostile metadata and HTML are handled only as input data.
        hostile='</script><script>alert(1)</script><img src=https://invalid.example/x>'
        source=copy.deepcopy(self.score);source['input_name']=hostile
        (self.bundle/'score.json').write_bytes(core.canonical(source))
        (self.bundle/'report.html').write_text('<script>alert(2)</script>')
        (self.bundle/'checksums.json').write_bytes(core.canonical({name:hashlib.sha256((self.bundle/name).read_bytes()).hexdigest() for name in edit.ASSETS}))
        fresh=self.root/'hostile';editor.create_editor(self.bundle,fresh)
        with sync_playwright() as pw:
            browser=pw.chromium.launch(args=['--no-sandbox'])
            context=browser.new_context(offline=True,accept_downloads=True)
            page=context.new_page();requests=[];errors=[];dialogs=[]
            page.on('request',lambda r:requests.append(r.url))
            page.on('pageerror',lambda e:errors.append(str(e)))
            page.on('dialog',lambda d:(dialogs.append(d.message),d.dismiss()))
            page.goto((fresh/'editor.html').as_uri())
            self.assertEqual(page.locator('#name').inner_text(),hostile)
            self.assertEqual(page.locator('#name img').count(),0)
            # Keyboard navigation, focus indicator, and stable identifiers.
            page.locator('#notes button').first.focus();page.keyboard.press('End')
            self.assertEqual(page.evaluate('selected'),15)
            self.assertEqual(page.locator('#notes button:focus').evaluate('(e)=>getComputedStyle(e).outlineStyle'),'solid')
            page.keyboard.press('Home');page.keyboard.press('ArrowRight')
            self.assertEqual(page.evaluate('selected'),1)
            for field,value in [('tempo','90'),('transpose','7'),('velocity','41')]:
                page.locator('#'+field).fill(value);page.locator('#'+field).press('Enter')
            page.locator('#mute').check()
            self.assertEqual(page.evaluate('score.events[1].velocity'),41)
            self.assertTrue(page.evaluate('score.events[1].muted'))
            # Invalid edits preserve state, history, and usable controls.
            before=page.evaluate('canonical(edits)');history=page.evaluate('undo.length')
            page.locator('#transpose').fill('119');page.locator('#transpose').press('Enter')
            self.assertIn('Transposed pitch',page.get_by_role('alert').inner_text())
            self.assertEqual(page.evaluate('canonical(edits)'),before)
            self.assertEqual(page.evaluate('undo.length'),history)
            page.locator('#undo').click();self.assertFalse(page.evaluate('score.events[1].muted'))
            page.locator('#redo').click();self.assertTrue(page.evaluate('score.events[1].muted'))
            page.locator('#restart').click();page.locator('#play').click()
            wait(page,'playing && currentTime()>.15')
            self.assertEqual(page.locator('#play').inner_text(),'Pause')
            state=page.evaluate('({t:currentTime(),i:selected,s:score.events[selected]})')
            self.assertGreaterEqual(state['t']*16000,state['s']['start_sample']-800)
            self.assertLessEqual(state['t']*16000,state['s']['end_sample']+800)
            page.locator('#play').click();paused=page.evaluate('currentTime()')
            page.wait_for_timeout(80);self.assertEqual(page.evaluate('currentTime()'),paused)
            page.locator('#seek').fill('1.4');page.locator('#seek').dispatch_event('input')
            self.assertEqual(page.evaluate('selected'),4)
            page.locator('#play').click();wait(page,'playing')
            page.locator('#tempo').fill('100');page.locator('#tempo').press('Enter')
            self.assertTrue(page.evaluate('!playing && node===null && position===0 && buffer===null'))
            # Verify synthesized buffer contains sound and exact silence at muted cells.
            info=page.evaluate('''()=>{const b=synthesize(context),v=b.getChannelData(0),e=score.events[1];return {length:b.length,peak:Math.max(...v.slice(0,4000)),silent:v.slice(e.start_sample,e.end_sample).every(x=>x===0)}}''')
            self.assertEqual(info['length'],page.evaluate('score.sample_count'))
            self.assertGreater(info['peak'],0.01);self.assertTrue(info['silent'])
            saved={}
            for button,name in [('export','edits.json'),('save','session.json'),('export','edits-again.json')]:
                with page.expect_download() as dl:page.locator('#'+button).click()
                saved[name]=self.root/name;dl.value.save_as(saved[name])
            self.assertEqual(saved['edits.json'].read_bytes(),saved['edits-again.json'].read_bytes())
            spec=json.loads(saved['edits.json'].read_text());state=page.evaluate('score')
            rendered=self.root/'rendered'
            subprocess.run([sys.executable,'-m','image_score.cli','edit',str(self.bundle),str(saved['edits.json']),str(rendered)],check=True,capture_output=True)
            self.assertEqual(json.loads((rendered/'score.json').read_text()),state)
            # Actual rendered report opens and decodes offline.
            report=context.new_page();report.goto((rendered/'report.html').as_uri());wait(report,'audio.readyState>=2');report.close()
            page.locator('#reset').click();self.assertEqual(page.evaluate('score.settings.tempo'),120)
            page.locator('#import').set_input_files(saved['edits.json']);wait(page,'score.settings.tempo===100')
            self.assertEqual(page.evaluate('score'),state)
            page.reload();page.locator('#import').set_input_files(saved['session.json']);wait(page,'score.settings.tempo===100')
            self.assertEqual(page.evaluate('score'),state)
            good=page.evaluate('canonical(edits)')
            invalid=[b'{"format":"image-score-edit","version":1,"version":1}',
                     b'{"format":"image-score-edit","version":1.0}',
                     b'{"format":"image-score-edit","version":1,"tempo":1e2}',
                     b'{"format":"image-score-edit","version":true}',
                     b'{"format":"image-score-edit","version":1,"mute":{"n000":1}}',
                     b'{"format":"image-score-edit","version":1,"velocity":{"n999":3}}',
                     b'{"format":"image-score-edit","version":1,"transpose":null}',
                     b'{"format":"image-score-edit","version":1,"__proto__":{}}',
                     b'\xff',b'x'*40961,b'{"x":'+b'{"x":'*10+b'0'+b'}'*11]
            session=json.loads(saved['session.json'].read_text());session['source_bundle_sha256']='0'*64
            invalid.append(json.dumps(session).encode())
            for raw in invalid:
                page.locator('#import').set_input_files({'name':'invalid.json','mimeType':'application/json','buffer':raw})
                wait(page,"document.getElementById('import').value==='' && document.getElementById('error').textContent!==''")
                self.assertEqual(page.evaluate('canonical(edits)'),good)
            # Bounded undo history and redo invalidation after a new edit.
            page.evaluate('()=>{for(let i=0;i<80;i++)commit({...edits,tempo:100+i%2});}')
            self.assertEqual(page.evaluate('undo.length'),50)
            page.locator('#undo').click();page.locator('#tempo').fill('110');page.locator('#tempo').press('Enter')
            self.assertEqual(page.evaluate('redo.length'),0)
            # Editing a note pins its controls instead of moving under typed input.
            page.locator('#restart').click();page.locator('#play').click();wait(page,'playing')
            page.locator('#velocity').focus();self.assertFalse(page.evaluate('playing'))
            # End-of-score and replay use the same bounded clock.
            page.evaluate('seek(duration()-.05)');page.locator('#play').click()
            wait(page,"document.getElementById('status').textContent==='Finished'")
            page.locator('#play').click();wait(page,'playing && currentTime()<1');page.locator('#play').click()
            # Unsupported audio preserves export and editing capability.
            noaudio=context.new_page();noaudio.add_init_script('window.AudioContext=undefined;window.webkitAudioContext=undefined;')
            noaudio.goto((fresh/'editor.html').as_uri());noaudio.locator('#play').click()
            self.assertIn('unavailable',noaudio.get_by_role('alert').inner_text())
            with noaudio.expect_download():noaudio.locator('#export').click()
            noaudio.close()
            # Pending resume must not start stale audio after an edit.
            delayed=context.new_page()
            delayed.add_init_script('''const Real=window.AudioContext;window.AudioContext=class extends Real {resume(){return new Promise(resolve=>{window.finishResume=()=>super.resume().then(resolve);});}};''')
            delayed.goto((fresh/'editor.html').as_uri());delayed.locator('#play').click()
            wait(delayed,'typeof window.finishResume==="function"')
            delayed.locator('#tempo').fill('95');delayed.locator('#tempo').press('Enter')
            delayed.evaluate('window.finishResume()');delayed.wait_for_timeout(100)
            self.assertTrue(delayed.evaluate('!playing && node===null'))
            delayed.close()
            # A real next-generation editor rejects the previous source session.
            next_editor=self.root/'next-editor';editor.create_editor(rendered,next_editor)
            next_page=context.new_page();next_page.goto((next_editor/'editor.html').as_uri())
            next_page.locator('#import').set_input_files(saved['session.json'])
            wait(next_page,"document.getElementById('error').textContent.includes('different source')")
            self.assertEqual(next_page.evaluate('edits.transpose'),0);next_page.close()
            # Reject duration expansion at the largest grid, preserving state.
            large=copy.deepcopy(source);large['events']=large['events']*16
            rejection=page.evaluate('base=>{try{transform(base,{format:"image-score-edit",version:1,tempo:30});return false;}catch(e){return e.message;}}',large)
            self.assertIn('60 seconds',rejection)
            self.assertFalse(errors);self.assertFalse(dialogs)
            self.assertFalse([u for u in requests if u.startswith(('http:','https:'))])
            browser.close()


if __name__=='__main__':unittest.main()
