import hashlib
from concurrent.futures import ThreadPoolExecutor
from fractions import Fraction
import subprocess
import sys
import io
import json
import math
import random
import struct
import tempfile
import unittest
import wave
from pathlib import Path
from unittest.mock import patch

import mido
from PIL import Image
from image_score import core


class CoreTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.path = self.root / 'input.png'

    def tearDown(self):
        self.tmp.cleanup()

    def fixture(self, color=(128, 64, 255), size=(9, 7), mode='RGB'):
        Image.new(mode, size, color).save(self.path)

    def test_240_seeded_oracle_and_independent_midi(self):
        rng = random.Random(20260928)
        scales = {'major':[0,2,4,5,7,9,11], 'minor':[0,2,3,5,7,8,10], 'pentatonic':[0,2,4,7,9]}
        for case in range(240):
            w, h = rng.randrange(1, 12), rng.randrange(1, 12)
            cols, rows = rng.randint(1, min(w, 3)), rng.randint(1, min(h, 2))
            pixels = [tuple(rng.randrange(256) for _ in range(4)) for _ in range(w*h)]
            if case % 6 == 0:
                pixels = [(case % 256,)*3+(255,)]*(w*h)
            if case % 6 == 1:
                pixels = [(i*255//max(w*h-1,1),)*3+(255,) for i in range(w*h)]
            if case % 6 == 2:
                pixels = [(255,0,100,0)]*(w*h)
            im = Image.new('RGBA',(w,h)); im.putdata(pixels); im.save(self.path)
            before = self.path.read_bytes()
            scale = list(scales)[case % 3]
            tempo = [30, 73, 120, 239, 240][case % 5]
            score, image = core.score_image(self.path, tempo=tempo, columns=cols, rows=rows, scale=scale)
            notes = [48+12*o+d for o in range(3) for d in scales[scale]]
            for i, event in enumerate(score['events']):
                r, c = divmod(i, cols)
                x0,x1 = c*w//cols,(c+1)*w//cols
                y0,y1 = r*h//rows,(r+1)*h//rows
                cells = [pixels[y*w+x] for y in range(y0,y1) for x in range(x0,x1)]
                avg = [sum((p[k]*p[3]+127)//255 for p in cells)//len(cells) for k in range(3)]
                lum = sum(a*b for a,b in zip(avg,[2126,7152,722]))//10000
                self.assertEqual(event['region'], [x0,y0,x1,y1])
                self.assertEqual(event['rgb'],avg)
                self.assertEqual(event['brightness'],lum)
                self.assertEqual(event['pitch'],notes[lum*len(notes)//256])
                self.assertEqual(event['velocity'],0 if lum==0 else 24+lum*103//255)
                self.assertEqual(event['start_tick'],i*240)
                tempo_us=round(Fraction(60_000_000,tempo))
                self.assertEqual(score['tempo_us'],tempo_us)
                self.assertEqual(event['start_sample'],round(Fraction(i*tempo_us*16000,2_000_000)))
                self.assertEqual(event['end_sample'],round(Fraction((i+1)*tempo_us*16000,2_000_000)))
            midi = mido.MidiFile(file=io.BytesIO(core.midi_bytes(score)))
            self.assertEqual(midi.type,0);self.assertEqual(midi.ticks_per_beat,480)
            tick=0; actual=[]
            for message in midi.tracks[0]:
                tick+=message.time
                if message.type=='set_tempo':self.assertEqual(message.tempo,score['tempo_us'])
                if message.type in ('note_on','note_off'):actual.append((tick,message.type,message.note,message.velocity))
            expected=[]
            for event in score['events']:
                if event['velocity']:
                    expected.extend([(event['start_tick'],'note_on',event['pitch'],event['velocity']), (event['start_tick']+240,'note_off',event['pitch'],0)])
            self.assertEqual(actual,expected);self.assertEqual(tick,cols*rows*240)
            a=core.artifacts(score,image)
            score2,image2=core.score_image(self.path,tempo=tempo,columns=cols,rows=rows,scale=scale)
            self.assertEqual(a,core.artifacts(score2,image2))
            self.assertEqual(before,self.path.read_bytes())
            self.assertEqual(score['input_sha256'],hashlib.sha256(before).hexdigest())

    def test_wav_analytical_frequency_peak_and_silence(self):
        for color in [(0,0,0),(255,255,255),(120,120,120),(255,0,0)]:
            self.fixture(color)
            score,_=core.score_image(self.path,rows=1,columns=1,tempo=60)
            raw=core.wav_bytes(score)
            self.assertEqual(raw[:4],b'RIFF');self.assertEqual(struct.unpack('<I',raw[4:8])[0],len(raw)-8)
            with wave.open(io.BytesIO(raw)) as wav:
                self.assertEqual((wav.getnchannels(),wav.getsampwidth(),wav.getframerate()),(1,2,16000))
                self.assertEqual(wav.getnframes(),8000)
                samples=struct.unpack('<8000h',wav.readframes(8000))
            self.assertTrue(all(math.isfinite(v) and abs(v)<=22937 for v in samples))
            self.assertEqual(samples[0],0);self.assertEqual(samples[-1],0)
            if color==(0,0,0):self.assertEqual(set(samples),{0});continue
            crossings=[i for i in range(201,7800) if samples[i-1]<=0<samples[i]]
            measured=16000*(len(crossings)-1)/(crossings[-1]-crossings[0])
            expected=440*2**((score['events'][0]['pitch']-69)/12)
            self.assertLess(abs(measured-expected),0.5)

    def test_formats_orientation_palette_and_grayscale(self):
        for mode,color in [('1',1),('L',120),('LA',(120,128)),('P',0)]:
            self.fixture(color,mode=mode)
            s,_=core.score_image(self.path,columns=1,rows=1)
            self.assertEqual(len(s['events']),1)
        self.path=self.root/'photo.jpg'
        im=Image.new('RGB',(8,5),(255,0,0));exif=Image.Exif();exif[274]=6;im.save(self.path,exif=exif)
        s,_=core.score_image(self.path,columns=1,rows=1)
        self.assertEqual((s['width'],s['height']),(5,8))
        self.path=self.root/'palette.png'
        im=Image.new('P',(1,1));im.putpalette([255,0,0]+[0]*765);im.save(self.path,transparency=0)
        s,_=core.score_image(self.path,columns=1,rows=1)
        self.assertEqual(s['events'][0]['velocity'],0)

    def test_limits_malformed_and_collision(self):
        self.fixture()
        for args in [dict(tempo=29),dict(tempo=241),dict(tempo=float('nan')),dict(scale='bad'),dict(rows=0),dict(columns=257),dict(rows=20,columns=10,tempo=30),dict(columns=10)]:
            with self.assertRaises(ValueError):core.score_image(self.path,**args)
        for data in [b'',b'not an image',b'\x89PNG\r\n\x1a\n',b'x'*(core.MAX_INPUT+1)]:
            self.path.write_bytes(data)
            with self.assertRaises((ValueError,OSError)):core.score_image(self.path,rows=1,columns=1)
        self.fixture()
        with patch.object(core,'MAX_PIXELS',1):
            with self.assertRaises(ValueError):core.read_image(self.path)
        s,im=core.score_image(self.path,rows=1,columns=1)
        with patch.object(core,'MAX_FILE',1):
            with self.assertRaises(ValueError):core.artifacts(s,im)
        with patch.object(core,'MAX_BUNDLE',1):
            with self.assertRaises(ValueError):core.artifacts(s,im)
        out=self.root/'out';core.convert(self.path,out,columns=1,rows=1)
        original={p.name:p.read_bytes() for p in out.iterdir()}
        with self.assertRaises(FileExistsError):core.convert(self.path,out,columns=1,rows=1)
        self.assertEqual(original,{p.name:p.read_bytes() for p in out.iterdir()})
        for name,digest in json.loads((out/'checksums.json').read_text()).items():
            self.assertEqual(hashlib.sha256((out/name).read_bytes()).hexdigest(),digest)

    def test_write_failure_and_cancellation_cleanup(self):
        for exception in [OSError('disk full'),KeyboardInterrupt()]:
            out=self.root/'failed'
            original=Path.open
            def fail(path,*args,**kwargs):
                if path.name=='second':raise exception
                return original(path,*args,**kwargs)
            with patch.object(Path,'open',fail):
                with self.assertRaises(type(exception)):core.publish({'first':b'ok','second':b'bad'},out)
            self.assertFalse(out.exists())
            self.assertFalse(list(self.root.glob(".image-score-*")))

    def test_concurrent_publication_and_symlink_collisions(self):
        out=self.root/'shared'
        def writer(value):
            try:
                core.publish({'value':value},out)
                return 'ok'
            except FileExistsError:
                return 'exists'
        with ThreadPoolExecutor(max_workers=2) as pool:
            result=list(pool.map(writer,[b'one',b'two']))
        self.assertEqual(sorted(result),['exists','ok'])
        self.assertIn((out/'value').read_bytes(),[b'one',b'two'])
        self.assertFalse(list(self.root.glob('.image-score-*')))
        link=self.root/'link';link.symlink_to(out,target_is_directory=True)
        with self.assertRaises(FileExistsError):core.publish({'other':b'x'},link)
        empty=self.root/'empty';empty.mkdir()
        with self.assertRaises(FileExistsError):core.publish({'other':b'x'},empty)
        self.assertEqual(list(empty.iterdir()),[])

    def test_boundary_grids_and_rejected_formats(self):
        self.fixture(size=(256,1))
        score,_=core.score_image(self.path,columns=256,rows=1,tempo=240)
        self.assertEqual(len(score['events']),256)
        self.assertEqual(score['sample_count'],512000)
        self.fixture(size=(60,1))
        score,_=core.score_image(self.path,columns=60,rows=1,tempo=30)
        self.assertEqual(score['sample_count'],960000)
        self.fixture(size=(61,1))
        with self.assertRaises(ValueError):core.score_image(self.path,columns=61,rows=1,tempo=30)
        Image.new('I;16',(2,2)).save(self.path)
        with self.assertRaises(ValueError):core.read_image(self.path)
        Image.new('RGB',(2,2)).save(self.path,format='GIF')
        with self.assertRaises(ValueError):core.read_image(self.path)
        im=Image.new('RGB',(2,2));im.save(self.path,save_all=True,append_images=[Image.new('RGB',(2,2),'red')],duration=20)
        with self.assertRaises(ValueError):core.read_image(self.path)
        self.path=self.root/'cmyk.jpg';Image.new('CMYK',(2,2),(0,255,255,0)).save(self.path)
        _,image=core.score_image(self.path,columns=1,rows=1)
        self.assertEqual(image.mode,'RGB')

    def test_cli_invalid_input_exit_and_no_output(self):
        self.path.write_bytes(b'broken')
        result=subprocess.run([sys.executable,'-m','image_score.cli',str(self.path),str(self.root/'out')],capture_output=True,text=True)
        self.assertEqual(result.returncode,2)
        self.assertNotIn('Traceback',result.stderr)
        self.assertFalse((self.root/'out').exists())

    def test_cli_sigterm_during_write_cleans_staging(self):
        self.fixture()
        program = """
import os, signal, sys
from pathlib import Path
from unittest.mock import patch
from image_score.cli import main
original=Path.open
def interrupted(path,*args,**kwargs):
    if path.name=='score.mid':os.kill(os.getpid(),signal.SIGTERM)
    return original(path,*args,**kwargs)
with patch.object(Path,'open',interrupted):
    sys.exit(main())
"""
        result=subprocess.run([sys.executable,'-c',program,str(self.path),str(self.root/'out'),'--rows','1','--columns','1'],capture_output=True,text=True)
        self.assertEqual(result.returncode,130)
        self.assertIn('Cancelled',result.stderr)
        self.assertFalse((self.root/'out').exists())
        self.assertFalse(list(self.root.glob('.image-score-*')))


if __name__=='__main__':unittest.main()
