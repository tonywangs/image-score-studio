"""Offline Chromium checks. Requires the pinned Playwright browser installed."""
from pathlib import Path
import hashlib
import json
import struct
import tempfile
import unittest
import time

from PIL import Image
from playwright.sync_api import sync_playwright
from image_score.core import convert
from image_score import core, edit


def wait(page, expression):
    deadline=time.monotonic()+10
    while time.monotonic()<deadline:
        if page.evaluate(expression):return
        page.wait_for_timeout(20)
    raise AssertionError("browser timed out: "+expression)


class BrowserTests(unittest.TestCase):
    def test_offline_playback_seek_keyboard_downloads_and_decoding(self):
        self.exercise_report(False)

    def test_edited_offline_playback_seek_keyboard_downloads_and_hostile_import(self):
        self.exercise_report(True)

    def exercise_report(self, editing):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            path=root/'<img src=x onerror=alert(1)> & "score".png'
            im=Image.new('RGB',(11,7));im.putdata([(i*3%256,i*7%256,i*11%256) for i in range(77)]);im.save(path)
            out=root/'bundle'
            score=convert(path,out,columns=3,rows=2,tempo=120)
            display_name=path.name
            if editing:
                display_name='</script><script>alert("import")</script><img src=https://invalid.example/x onerror=alert(1)>'
                score['input_name']=display_name
                (out/'score.json').write_bytes(core.canonical(score))
                (out/'report.html').write_text('<script>alert("untrusted report")</script>')
                (out/'checksums.json').write_bytes(core.canonical({name:hashlib.sha256((out/name).read_bytes()).hexdigest() for name in edit.ASSETS}))
                spec=root/'edits.json'
                spec.write_bytes(core.canonical({'format':'image-score-edit','version':1,'tempo':90,
                                                'transpose':7,'velocity':{'n001':30,'n002':0},'mute':{'n001':True}}))
                path.unlink()
                edited=root/'edited'
                score=edit.edit_bundle(out,spec,edited)
                out=edited
            note_seconds=score['tempo_us']/2_000_000
            duration=score['sample_count']/score['sample_rate']
            with sync_playwright() as pw:
                browser=pw.chromium.launch(headless=True,args=['--no-sandbox'])
                context=browser.new_context(accept_downloads=True,offline=True)
                requests=[];errors=[];dialogs=[]
                page=context.new_page()
                page.on('request',lambda request:requests.append(request.url))
                page.on('pageerror',lambda error:errors.append(str(error)))
                page.on('dialog',lambda dialog:(dialogs.append(dialog.message),dialog.dismiss()))
                page.goto((out/'report.html').as_uri())
                wait(page, "audio.readyState >= 2")
                self.assertEqual(page.locator('#name').inner_text(),display_name)
                self.assertEqual(page.locator('#name img').count(),0)
                self.assertTrue(page.evaluate('audio.paused'))
                self.assertEqual(page.locator('#notes button').count(),6)
                page.locator('#play').click()
                wait(page, 'audio.currentTime > 0.3')
                self.assertEqual(page.locator('#play').inner_text(),'Pause')
                state=page.evaluate('({time:audio.currentTime,selected})')
                self.assertGreaterEqual(state['time'],state['selected']*note_seconds-0.03)
                self.assertLessEqual(state['time'],(state['selected']+1)*note_seconds+0.03)
                page.locator('#play').click()
                paused=page.evaluate('audio.currentTime')
                page.wait_for_timeout(150)
                self.assertAlmostEqual(page.evaluate('audio.currentTime'),paused,places=3)
                seek_time=3.2*note_seconds
                page.locator('#seek').evaluate('(el,value)=>el.value=value',seek_time);page.locator('#seek').dispatch_event('input')
                wait(page, 'selected === 3')
                self.assertAlmostEqual(page.evaluate('audio.currentTime'),seek_time,places=2)
                first=page.locator('#notes button').first
                first.focus();first.press('End')
                self.assertEqual(page.locator('#notes button:focus').inner_text(),('n005' if editing else '6')+' · MIDI '+str(score['events'][5]['pitch']))
                self.assertEqual(page.evaluate('selected'),5)
                page.keyboard.press('ArrowLeft');self.assertEqual(page.evaluate('selected'),4)
                page.keyboard.press('Home');self.assertEqual(page.evaluate('selected'),0)
                page.locator('#notes button').nth(2).click();self.assertEqual(page.evaluate('selected'),2)
                if editing:
                    page.locator('#notes button').nth(1).click()
                    self.assertIn('n001',page.locator('#detail').inner_text())
                    self.assertIn('velocity 30 · muted',page.locator('#detail').inner_text())
                    self.assertIn('Parent bundle SHA-256',page.locator('#hash').inner_text())
                    self.assertEqual(page.locator('#notes button').nth(1).inner_text(),'n001 · muted')
                    self.assertEqual(page.locator('#notes button').nth(2).inner_text(),'n002 · rest')
                    region=score['events'][1]['region']
                    self.assertAlmostEqual(page.evaluate("parseFloat(document.getElementById('region').style.left)"),100*region[0]/score['width'],places=3)
                page.locator('#restart').click();self.assertEqual(page.evaluate('audio.currentTime'),0)
                self.assertTrue(page.evaluate('audio.paused'))
                # Independently decode actual embedded WAV through Chromium's audio decoder.
                samples=page.evaluate('''async()=>{const ctx=new OfflineAudioContext(1,score.sample_count,score.sample_rate);const buffer=await ctx.decodeAudioData(decode(files['preview.wav']).buffer);return {rate:buffer.sampleRate,length:buffer.length,data:Array.from(buffer.getChannelData(0))}}''')
                raw=(out/'preview.wav').read_bytes()[44:]
                expected=struct.unpack('<'+'h'*(len(raw)//2),raw)
                self.assertEqual(samples['rate'],16000);self.assertEqual(samples['length'],len(expected))
                self.assertLess(max(abs(a-b/32768) for a,b in zip(samples['data'],expected)),1/32768)
                for name in ['score.json','score.mid','preview.wav','image.png','report.html']:
                    with page.expect_download() as download_info:
                        page.get_by_role('link',name='↓ '+name,exact=True).click()
                    downloaded=root/('download-'+name);download_info.value.save_as(downloaded)
                    if name!='report.html':self.assertEqual(downloaded.read_bytes(),(out/name).read_bytes())
                    else:
                        second=context.new_page();second.goto(downloaded.as_uri());wait(second, 'audio.readyState >= 2')
                        self.assertEqual(second.locator('nav a').count(),5)
                        self.assertEqual(second.locator('#notes button').count(),6);second.close()
                page.locator('#seek').evaluate('(el,value)=>el.value=value',duration-0.1);page.locator('#seek').dispatch_event('input');page.locator('#play').click()
                wait(page, 'audio.ended')
                self.assertEqual(page.locator('#state').inner_text(),'Finished')
                self.assertEqual(page.evaluate('selected'),5)
                page.locator('#play').click();wait(page, '!audio.paused && audio.currentTime < 1')
                self.assertFalse(errors);self.assertFalse(dialogs)
                self.assertFalse([url for url in requests if url.startswith(('http:','https:'))])
                browser.close()


if __name__=='__main__':unittest.main()
