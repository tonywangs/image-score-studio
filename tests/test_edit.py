"""Independent edit oracle, media checks, and untrusted bundle boundaries."""
import copy
from fractions import Fraction
import hashlib
import io
import json
import math
import os
from pathlib import Path
import random
import struct
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
import wave

import mido
from PIL import Image
from image_score import core, edit


EMPTY = {'format': 'image-score-edit', 'version': 1}


def oracle(original, spec):
    """Reference calculation independent of edit normalization/validation/renderers."""
    tempo = spec.get('tempo', original['settings']['tempo'])
    micros = round(Fraction(60_000_000, tempo))
    result = []
    for i, source in enumerate(original['events']):
        note = copy.deepcopy(source)
        name = 'n' + str(i).zfill(3)
        note.update(id=name, pitch=source['pitch'] + spec.get('transpose', 0),
                    velocity=spec.get('velocity', {}).get(name, source['velocity']),
                    muted=spec.get('mute', {}).get(name, source.get('muted', False)),
                    start_sample=round(Fraction(i * micros * 16000, 2000000)),
                    end_sample=round(Fraction((i+1) * micros * 16000, 2000000)))
        result.append(note)
    return micros, result


def pcm(raw):
    with wave.open(io.BytesIO(raw)) as audio:
        assert (audio.getnchannels(), audio.getsampwidth(), audio.getframerate()) == (1, 2, 16000)
        length = audio.getnframes()
        return struct.unpack('<' + str(length) + 'h', audio.readframes(length))


def check_midi(test, raw, events, micros):
    midi = mido.MidiFile(file=io.BytesIO(raw))
    test.assertEqual((midi.type, len(midi.tracks), midi.ticks_per_beat), (0, 1, 480))
    tick = 0
    actual, tempos, programs = [], [], []
    for message in midi.tracks[0]:
        tick += message.time
        if message.type == 'set_tempo':
            tempos.append((tick, message.tempo))
        elif message.type == 'program_change':
            programs.append((tick, message.channel, message.program))
        elif message.type in ('note_on', 'note_off'):
            actual.append((tick, message.type, message.channel, message.note, message.velocity))
    expected = []
    for i, event in enumerate(events):
        if event['velocity'] and not event['muted']:
            expected.extend([(i*240, 'note_on', 0, event['pitch'], event['velocity']),
                             ((i+1)*240, 'note_off', 0, event['pitch'], 0)])
    test.assertEqual(actual, expected)
    test.assertEqual(tempos, [(0, micros)])
    test.assertEqual(programs, [(0, 0, 0)])
    test.assertEqual(tick, len(events)*240)
    test.assertAlmostEqual(midi.length, float(Fraction(tick*micros, 480000000)), places=10)


class EditTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.source = self.root/'source.png'
        image = Image.new('RGB', (6, 2))
        image.putdata([(i*23 % 256, i*43 % 256, i*71 % 256) for i in range(12)])
        image.save(self.source)
        self.bundle = self.root/'bundle'
        self.score = core.convert(self.source, self.bundle, columns=3, rows=2)
        self.spec = self.root/'edits.json'
        self.spec.write_bytes(core.canonical(EMPTY))
        self.out = self.root/'edited'

    def tearDown(self):
        self.tmp.cleanup()

    def rewrite(self, score=None, **assets):
        if score is not None:
            assets['score.json'] = core.canonical(score)
        for name, raw in assets.items():
            (self.bundle/name).write_bytes(raw)
        manifest = {name: hashlib.sha256((self.bundle/name).read_bytes()).hexdigest() for name in edit.ASSETS}
        (self.bundle/'checksums.json').write_bytes(core.canonical(manifest))

    def test_240_seeded_reference_media_and_determinism(self):
        rng = random.Random(20260930)
        score, image, parent = edit.load_bundle(self.bundle)
        for case in range(240):
            spec = dict(EMPTY, tempo=rng.choice([30, 73, 120, 239, 240]),
                        transpose=rng.randint(-24, 24),
                        velocity={f'n{i:03d}': rng.randrange(128) for i in range(6) if rng.randrange(2)},
                        mute={f'n{i:03d}': bool(rng.randrange(2)) for i in range(6) if rng.randrange(2)})
            if case % 12 == 0:
                spec['mute'] = {f'n{i:03d}': True for i in range(6)}
            if case % 12 == 1:
                spec['velocity'] = {f'n{i:03d}': 0 for i in range(6)}
            before = core.canonical(score), core.canonical(spec)
            actual = edit.apply_edits(score, spec, parent)
            micros, expected = oracle(score, spec)
            self.assertEqual(actual['events'], expected, case)
            self.assertEqual(actual['tempo_us'], micros)
            self.assertEqual(actual['sample_count'], expected[-1]['end_sample'])
            self.assertEqual(before, (core.canonical(score), core.canonical(spec)))
            for key in ('input_sha256', 'input_name', 'width', 'height', 'pillow'):
                self.assertEqual(actual[key], score[key])
            files = core.artifacts(actual, image)
            self.assertEqual(files, core.artifacts(edit.apply_edits(score, spec, parent), image))
            check_midi(self, files['score.mid'], expected, micros)
            samples = pcm(files['preview.wav'])
            self.assertEqual(len(samples), expected[-1]['end_sample'])
            self.assertEqual(len(files['preview.wav']), 44+2*len(samples))
            for note in expected:
                segment = samples[note['start_sample']:note['end_sample']]
                self.assertEqual((segment[0], segment[-1]), (0, 0))
                if note['muted'] or not note['velocity']:
                    self.assertEqual(set(segment), {0})
                else:
                    self.assertLessEqual(max(map(abs, segment)), math.ceil(32767*0.7*note['velocity']/127))
                    self.assertGreater(max(map(abs, segment)), 0)
            self.assertEqual(json.loads(files['score.json']), actual)
            for name, checksum in json.loads(files['checksums.json']).items():
                self.assertEqual(hashlib.sha256(files[name]).hexdigest(), checksum)

    def test_identity_sequential_and_original_image_deleted(self):
        originals = {p.name: p.read_bytes() for p in self.bundle.iterdir()}
        self.source.unlink()
        first = edit.edit_bundle(self.bundle, self.spec, self.out)
        for name in ('score.mid', 'preview.wav', 'image.png'):
            self.assertEqual((self.out/name).read_bytes(), originals[name])
        loaded, image, parent = edit.load_bundle(self.out)
        self.assertEqual(first, loaded)
        for actual, expected in zip(first['events'], self.score['events']):
            self.assertEqual({k: v for k, v in actual.items() if k not in ('id', 'muted')}, expected)
        second = edit.apply_edits(loaded, dict(EMPTY, transpose=12, tempo=73,
                                  velocity={'n001': 127}, mute={'n001': True}), parent)
        self.assertEqual(second['events'][1]['velocity'], 127)
        third = edit.apply_edits(second, dict(EMPTY, transpose=-12, tempo=120,
                                 velocity={'n001': 40}, mute={'n001': False}), 'f'*64)
        direct = edit.apply_edits(self.score, dict(EMPTY, velocity={'n001': 40}), 'f'*64)
        self.assertEqual(third['events'], direct['events'])
        self.assertEqual(core.midi_bytes(third), core.midi_bytes(direct))
        self.assertEqual(core.wav_bytes(third), core.wav_bytes(direct))
        self.assertEqual(third['provenance']['parent_bundle_sha256'], 'f'*64)
        # Applying a relative transposition twice accumulates; absolute assignments repeat.
        once = edit.apply_edits(loaded, dict(EMPTY, transpose=2, velocity={'n000': 20}), parent)
        twice = edit.apply_edits(once, dict(EMPTY, transpose=2, velocity={'n000': 20}), parent)
        self.assertEqual(twice['events'][0]['pitch'], loaded['events'][0]['pitch']+4)
        self.assertEqual(twice['events'][0]['velocity'], 20)
        self.assertEqual(originals, {p.name: p.read_bytes() for p in self.bundle.iterdir()})
        # Parent fingerprint is independent of manifest whitespace/key order.
        manifest = json.loads((self.bundle/'checksums.json').read_bytes())
        (self.bundle/'checksums.json').write_text(json.dumps(manifest, separators=(',', ':')))
        self.assertEqual(edit.load_bundle(self.bundle)[2], first['provenance']['parent_bundle_sha256'])

    def test_frequency_transposition_and_amplitude(self):
        score, _, parent = edit.load_bundle(self.bundle)
        for semitones in (-12, 0, 12, 24):
            spec = dict(EMPTY, tempo=30, transpose=semitones,
                        velocity={f'n{i:03d}': 64 for i in range(6)}, mute={'n003': True})
            edited = edit.apply_edits(score, spec, parent)
            samples = pcm(core.wav_bytes(edited))
            for note in edited['events']:
                segment = samples[note['start_sample']:note['end_sample']]
                if note['muted']:
                    self.assertEqual(set(segment), {0})
                    continue
                crossings = [i for i in range(201, len(segment)-200) if segment[i-1] <= 0 < segment[i]]
                hz = 16000*(len(crossings)-1)/(crossings[-1]-crossings[0])
                target = 440*2**((note['pitch']-69)/12)
                self.assertLess(abs(hz-target), 0.5)
                self.assertLessEqual(max(map(abs, segment)), 11559)
        # Explicit pitch bounds, including silent notes; no clipping is allowed.
        upgraded = edit.apply_edits(score, EMPTY, parent)
        for pitch in (0, 119):
            for note in upgraded['events']:
                note['pitch'] = pitch
            edit.validate_score(upgraded)
            self.assertEqual(edit.apply_edits(upgraded, EMPTY, parent)['events'][0]['pitch'], pitch)
        for transpose in (1, -120):
            with self.assertRaises(ValueError):
                edit.apply_edits(upgraded, dict(EMPTY, transpose=transpose), parent)

    def test_invalid_specs_fail_before_render(self):
        bad = [None, [], {}, dict(EMPTY, version=True), dict(EMPTY, version=2),
               dict(EMPTY, format='other'), dict(EMPTY, extra=1), dict(EMPTY, tempo=29),
               dict(EMPTY, tempo=241), dict(EMPTY, tempo=120.0), dict(EMPTY, tempo=True),
               dict(EMPTY, transpose=1.5), dict(EMPTY, transpose=120),
               dict(EMPTY, transpose=-119), dict(EMPTY, velocity=[]), dict(EMPTY, mute=[]),
               dict(EMPTY, velocity={'n999': 20}), dict(EMPTY, velocity={'n000': -1}),
               dict(EMPTY, velocity={'n000': 128}), dict(EMPTY, velocity={'n000': True}),
               dict(EMPTY, mute={'n000': 1}), dict(EMPTY, mute={'n000': 'false'}),
               dict(EMPTY, mute={'n999': True})]
        for spec in bad:
            with self.subTest(spec=spec), patch.object(core, 'artifacts') as render:
                self.spec.write_bytes(core.canonical(spec))
                with self.assertRaises(ValueError):
                    edit.edit_bundle(self.bundle, self.spec, self.out)
                render.assert_not_called()
                self.assertFalse(self.out.exists())
        for raw in (b'{"version":1,"version":1}', b'{"x":NaN}', b'{"x":Infinity}',
                    b'\xff', b'['*2000+b']'*2000, b'{', b'x'*(edit.MAX_EDIT+1)):
            self.spec.write_bytes(raw)
            with self.assertRaises(ValueError):
                edit.edit_bundle(self.bundle, self.spec, self.out)

    def test_malformed_scores_even_with_valid_hashes(self):
        mutations = [lambda s: s.update(version=3), lambda s: s.update(version=True),
                     lambda s: s.update(extra='field'), lambda s: s.update(events=[]),
                     lambda s: s.update(events=s['events']*50), lambda s: s.update(sample_count=10**12),
                     lambda s: s.update(sample_rate=48000), lambda s: s.update(tempo_us=1),
                     lambda s: s.update(input_sha256='x'), lambda s: s.update(width=core.MAX_PIXELS+1),
                     lambda s: s.update(width=1), lambda s: s.update(input_name='a'*1025),
                     lambda s: s.update(settings=[]), lambda s: s['settings'].update(scale=[]),
                     lambda s: s['settings'].update(columns=0),
                     lambda s: s['events'][0].update(index=1), lambda s: s['events'][0].update(region=[0,0,7,2]),
                     lambda s: s['events'][0].update(start_tick=10), lambda s: s['events'][0].update(duration_ticks=0),
                     lambda s: s['events'][0].update(start_sample=-1), lambda s: s['events'][0].update(end_sample=1),
                     lambda s: s['events'][0].update(pitch=120), lambda s: s['events'][0].update(velocity=128),
                     lambda s: s['events'][0].update(rgb=[1,2]), lambda s: s['events'][0].update(brightness=256),
                     lambda s: s['events'][0].update(region=[False,0,2,1])]
        for mutate in mutations:
            score = copy.deepcopy(self.score)
            mutate(score)
            self.rewrite(score)
            with self.subTest(score=score), patch.object(core, 'artifacts') as render:
                with self.assertRaises(ValueError):
                    edit.edit_bundle(self.bundle, self.spec, self.out)
                render.assert_not_called()
        # Valid structure but mismatched RGB/image evidence.
        upgraded = edit.apply_edits(self.score, EMPTY, '0'*64)
        upgraded['events'][0].update(rgb=[0,0,0], brightness=0)
        self.rewrite(upgraded)
        with self.assertRaisesRegex(ValueError, 'RGB does not match'):
            edit.load_bundle(self.bundle)
        for change in [dict(id='n001'), dict(muted=1)]:
            score = edit.apply_edits(self.score, EMPTY, '0'*64)
            score['events'][0].update(change)
            with self.assertRaises(ValueError):
                edit.validate_score(score)

    def test_import_files_hashes_images_and_caps(self):
        original = {p.name: p.read_bytes() for p in self.bundle.iterdir()}
        def restore():
            for p in self.bundle.iterdir():
                p.unlink()
            for name, raw in original.items():
                (self.bundle/name).write_bytes(raw)
        for name in edit.BUNDLE_FILES:
            (self.bundle/name).unlink()
            with self.assertRaises(ValueError): edit.load_bundle(self.bundle)
            restore()
        (self.bundle/'extra').write_bytes(b'')
        with self.assertRaises(ValueError): edit.load_bundle(self.bundle)
        restore()
        for name in edit.ASSETS:
            (self.bundle/name).write_bytes(b'changed')
            with self.assertRaises(ValueError): edit.load_bundle(self.bundle)
            restore()
        self.rewrite(**{'image.png': b'bad PNG'})
        with self.assertRaises((ValueError, OSError)): edit.load_bundle(self.bundle)
        restore()
        for size, mode in [((1,1), 'RGB'), ((6,2), 'RGBA')]:
            data = io.BytesIO(); Image.new(mode, size).save(data, format='PNG')
            self.rewrite(**{'image.png': data.getvalue()})
            with self.assertRaises(ValueError): edit.load_bundle(self.bundle)
            restore()
        self.rewrite(**{'score.json': b'{"version":1,"version":1}'})
        with self.assertRaises(ValueError): edit.load_bundle(self.bundle)
        restore()
        for module, name, value in [(edit, 'MAX_JSON', 1), (core, 'MAX_FILE', 1),
                                    (core, 'MAX_BUNDLE', 100), (core, 'MAX_PIXELS', 1),
                                    (core, 'MAX_NOTES', 1), (core, 'MAX_DURATION', 1)]:
            with patch.object(module, name, value), self.assertRaises(ValueError):
                edit.load_bundle(self.bundle)
        for name in ('score.json', 'image.png'):
            (self.bundle/name).unlink(); (self.bundle/name).symlink_to(self.source)
            with self.assertRaises(ValueError): edit.load_bundle(self.bundle)
            restore()
        link = self.root/'link'; link.symlink_to(self.bundle, target_is_directory=True)
        with self.assertRaises(ValueError): edit.load_bundle(link)
        if hasattr(os, 'mkfifo'):
            fifo = self.root/'fifo'; os.mkfifo(fifo)
            with self.assertRaises(ValueError): edit.read_bounded(fifo, 100)
        # Path traversal is rejected by the exact manifest schema.
        (self.bundle/'checksums.json').write_bytes(core.canonical({'../outside': '0'*64}))
        with self.assertRaises(ValueError): edit.load_bundle(self.bundle)

    def test_duration_and_output_preflight(self):
        Image.new('RGB', (61,1), 'white').save(self.source)
        score, image = core.score_image(self.source, columns=61, rows=1, tempo=240)
        with self.assertRaisesRegex(ValueError, 'duration'):
            edit.apply_edits(score, dict(EMPTY, tempo=30), '0'*64)
        for limit in ('MAX_FILE', 'MAX_BUNDLE'):
            with patch.object(core, limit, 1), patch.object(core, 'wav_bytes') as render:
                with self.assertRaisesRegex(ValueError, 'before audio rendering'):
                    core.artifacts(score, image)
                render.assert_not_called()

    def test_collisions_serialization_cancellation_cleanup(self):
        before = {p.name: p.read_bytes() for p in self.bundle.iterdir()}
        for output in (self.bundle, self.bundle/'nested'):
            with self.assertRaises((ValueError, FileExistsError)):
                edit.edit_bundle(self.bundle, self.spec, output)
        self.out.mkdir()
        with self.assertRaises(FileExistsError): edit.edit_bundle(self.bundle, self.spec, self.out)
        self.out.rmdir(); self.out.symlink_to(self.bundle, target_is_directory=True)
        with self.assertRaises((ValueError, FileExistsError)): edit.edit_bundle(self.bundle, self.spec, self.out)
        self.out.unlink()
        for exception in (ValueError('serialization failed'), KeyboardInterrupt()):
            with patch.object(core, 'artifacts', side_effect=exception), self.assertRaises(type(exception)):
                edit.edit_bundle(self.bundle, self.spec, self.out)
            self.assertFalse(self.out.exists())
        actual_canonical = core.canonical
        def bad_serialization(value):
            if isinstance(value, dict) and value.get('version') == 2:
                raise ValueError('serialization failed')
            return actual_canonical(value)
        with patch.object(core, 'canonical', side_effect=bad_serialization), self.assertRaises(ValueError):
            edit.edit_bundle(self.bundle, self.spec, self.out)
        for exception in (OSError('disk full'), KeyboardInterrupt()):
            original_open = Path.open
            def fail(path, *args, **kwargs):
                if path.name == 'score.mid' and args == ('xb',): raise exception
                return original_open(path, *args, **kwargs)
            with patch.object(Path, 'open', fail), self.assertRaises(type(exception)):
                edit.edit_bundle(self.bundle, self.spec, self.out)
            self.assertFalse(self.out.exists())
            self.assertFalse(list(self.root.glob('.image-score-*')))
        self.assertEqual(before, {p.name: p.read_bytes() for p in self.bundle.iterdir()})

    def test_cli_errors_and_real_sigterm(self):
        command = [sys.executable, '-m', 'image_score.cli', 'edit', str(self.bundle), str(self.spec), str(self.out)]
        self.spec.write_text('{"bad":1}')
        failure = subprocess.run(command, capture_output=True, text=True)
        self.assertEqual(failure.returncode, 2)
        self.assertNotIn('Traceback', failure.stderr)
        self.spec.write_bytes(core.canonical(EMPTY))
        program = '''
import os, signal, sys
from pathlib import Path
from unittest.mock import patch
from image_score.cli import edit_main
original = Path.open
def interrupted(path, *args, **kwargs):
    if path.name == 'score.mid' and args == ('xb',): os.kill(os.getpid(), signal.SIGTERM)
    return original(path, *args, **kwargs)
with patch.object(Path, 'open', interrupted):
    sys.exit(edit_main())
'''
        failure = subprocess.run([sys.executable, '-c', program, str(self.bundle), str(self.spec), str(self.out)], capture_output=True, text=True)
        self.assertEqual(failure.returncode, 130)
        self.assertIn('Cancelled', failure.stderr)
        self.assertFalse(self.out.exists())
        self.assertFalse(list(self.root.glob('.image-score-*')))


if __name__ == '__main__':
    unittest.main()
