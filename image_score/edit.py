"""Strict saved-bundle import and versioned edits. Imported HTML is never executed."""
import copy
import hashlib
import io
import json
import os
from pathlib import Path
import re
import stat
import warnings

from PIL import Image
from . import core

MAX_JSON = 256 * 1024
MAX_EDIT = 32 * 1024
MAX_PITCH = 119  # The highest MIDI fundamental below the 16 kHz preview's Nyquist limit.
ASSETS = frozenset(('score.json', 'score.mid', 'preview.wav', 'image.png', 'report.html'))
BUNDLE_FILES = ASSETS | {'checksums.json'}
SCORE_KEYS = frozenset(('format', 'version', 'input_sha256', 'input_name', 'width', 'height',
                        'pillow', 'settings', 'tempo_us', 'ticks_per_beat', 'sample_rate',
                        'sample_count', 'events'))
EVENT_KEYS = frozenset(('index', 'region', 'rgb', 'brightness', 'pitch', 'velocity',
                        'start_tick', 'duration_ticks', 'start_sample', 'end_sample'))


def require(condition, message):
    if not condition:
        raise ValueError(message)


def integer(value, low, high, label):
    require(type(value) is int and low <= value <= high,
            f'{label} must be an integer from {low} to {high}')
    return value


def keys(value, expected, label):
    require(type(value) is dict and value.keys() == expected, f'invalid {label} fields')


def digest(value):
    require(type(value) is str and re.fullmatch('[0-9a-f]{64}', value) is not None,
            'invalid SHA-256')


def read_bounded(path, limit):
    """Bound actual reads and reject special files, including symlinks and FIFOs."""
    path = Path(path)
    require(not path.is_symlink(), 'symlink inputs are unsupported')
    fd = os.open(path, os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0) | getattr(os, 'O_NONBLOCK', 0))
    with os.fdopen(fd, 'rb') as stream:
        info = os.fstat(stream.fileno())
        require(stat.S_ISREG(info.st_mode), 'input must be a regular file')
        require(info.st_size <= limit, f'{path.name} exceeds {limit} bytes')
        raw = stream.read(limit + 1)
    require(len(raw) <= limit, f'{path.name} exceeds {limit} bytes')
    return raw


def parse_json(raw):
    def pairs(items):
        result = {}
        for key, value in items:
            require(key not in result, 'duplicate JSON key')
            result[key] = value
        return result
    def constant(value):
        raise ValueError('non-finite JSON number')
    try:
        return json.loads(raw.decode('utf-8'), object_pairs_hook=pairs, parse_constant=constant)
    except (UnicodeError, RecursionError) as error:
        raise ValueError('invalid or excessively nested JSON') from error


def validate_edits(spec, identifiers):
    require(type(spec) is dict, 'edit specification must be an object')
    require({'format', 'version'} <= spec.keys() <=
            {'format', 'version', 'tempo', 'transpose', 'velocity', 'mute'}, 'invalid edit fields')
    require(spec['format'] == 'image-score-edit', 'unsupported edit format')
    integer(spec['version'], 1, 1, 'edit version')
    normalized = {'format': 'image-score-edit', 'version': 1,
                  'transpose': spec.get('transpose', 0),
                  'velocity': spec.get('velocity', {}), 'mute': spec.get('mute', {})}
    if 'tempo' in spec:
        normalized['tempo'] = integer(spec['tempo'], 30, 240, 'tempo')
    integer(normalized['transpose'], -MAX_PITCH, MAX_PITCH, 'transpose')
    for field in ('velocity', 'mute'):
        mapping = normalized[field]
        require(type(mapping) is dict and len(mapping) <= core.MAX_NOTES, f'invalid {field} map')
        for name, value in mapping.items():
            require(name in identifiers, f'unknown note identifier: {name!r}')
            if field == 'velocity':
                integer(value, 0, 127, 'velocity')
            else:
                require(type(value) is bool, 'mute values must be booleans')
    return copy.deepcopy(normalized)


def validate_score(score):
    require(type(score) is dict, 'score must be an object')
    version = integer(score.get('version'), 1, 2, 'score version')
    keys(score, SCORE_KEYS | ({'provenance'} if version == 2 else set()), 'score')
    require(score['format'] == 'image-score', 'unsupported score format')
    digest(score['input_sha256'])
    for key, cap in [('input_name', 1024), ('pillow', 64)]:
        require(type(score[key]) is str and 1 <= len(score[key]) <= cap, f'invalid {key}')
    width = integer(score['width'], 1, core.MAX_PIXELS, 'width')
    height = integer(score['height'], 1, core.MAX_PIXELS, 'height')
    require(width * height <= core.MAX_PIXELS, 'image exceeds pixel limit')
    settings = score['settings']
    keys(settings, {'tempo', 'scale', 'columns', 'rows'}, 'settings')
    tempo = integer(settings['tempo'], 30, 240, 'tempo')
    require(type(settings['scale']) is str and settings['scale'] in core.SCALES, 'unknown scale')
    columns = integer(settings['columns'], 1, width, 'columns')
    rows = integer(settings['rows'], 1, height, 'rows')
    count = columns * rows
    require(1 <= count <= core.MAX_NOTES, 'note count limit exceeded')
    tempo_us = round(60_000_000 / tempo)
    require(count * tempo_us <= core.MAX_DURATION * 2_000_000, 'score exceeds duration limit')
    for name, expected in [('tempo_us', tempo_us), ('ticks_per_beat', 480),
                           ('sample_rate', core.RATE),
                           ('sample_count', round(count * tempo_us * core.RATE / 2_000_000))]:
        integer(score[name], expected, expected, name)
    events = score['events']
    require(type(events) is list and len(events) == count, 'event count does not match grid')
    for index, event in enumerate(events):
        keys(event, EVENT_KEYS | ({'id', 'muted'} if version == 2 else set()), 'event')
        integer(event['index'], index, index, 'event index')
        row, col = divmod(index, columns)
        box = [col * width // columns, row * height // rows,
               (col + 1) * width // columns, (row + 1) * height // rows]
        require(type(event['region']) is list and len(event['region']) == 4 and
                all(type(v) is int for v in event['region']) and event['region'] == box,
                'invalid image-region reference')
        require(type(event['rgb']) is list and len(event['rgb']) == 3, 'invalid RGB')
        for value in event['rgb']:
            integer(value, 0, 255, 'RGB channel')
        brightness = sum(a*b for a, b in zip(event['rgb'], (2126, 7152, 722))) // 10000
        integer(event['brightness'], brightness, brightness, 'brightness')
        integer(event['pitch'], 0, MAX_PITCH, 'pitch')
        integer(event['velocity'], 0, 127, 'velocity')
        for key, expected in [('start_tick', index * 240), ('duration_ticks', 240),
                              ('start_sample', round(index * tempo_us * core.RATE / 2_000_000)),
                              ('end_sample', round((index+1) * tempo_us * core.RATE / 2_000_000))]:
            integer(event[key], expected, expected, key)
        if version == 2:
            require(event['id'] == f'n{index:03d}', 'invalid stable note identifier')
            require(type(event['muted']) is bool, 'muted must be boolean')
        else:
            palette = [48 + 12*octave + degree for octave in range(3) for degree in core.SCALES[settings['scale']]]
            require(event['pitch'] == palette[brightness * len(palette) // 256] and
                    event['velocity'] == (0 if brightness == 0 else 24 + brightness * 103 // 255),
                    'version 1 mapping is inconsistent')
    if version == 2:
        provenance = score['provenance']
        keys(provenance, {'parent_bundle_sha256', 'edits'}, 'provenance')
        digest(provenance['parent_bundle_sha256'])
        validate_edits(provenance['edits'], {event['id'] for event in events})


def load_bundle(path):
    path = Path(path)
    require(path.is_dir() and not path.is_symlink(), 'bundle must be a real directory')
    names = set()
    with os.scandir(path) as entries:
        for entry in entries:
            names.add(entry.name)
            require(len(names) <= len(BUNDLE_FILES), 'unexpected bundle files')
    require(names == BUNDLE_FILES, 'bundle must contain exactly the six exported files')
    manifest_raw = read_bounded(path / 'checksums.json', MAX_JSON)
    manifest = parse_json(manifest_raw)
    keys(manifest, ASSETS, 'checksum manifest')
    for value in manifest.values():
        digest(value)
    files = {}
    total = len(manifest_raw)
    for name in sorted(ASSETS):
        cap = MAX_JSON if name == 'score.json' else core.MAX_FILE
        raw = read_bounded(path / name, min(cap, core.MAX_BUNDLE - total))
        total += len(raw)
        require(hashlib.sha256(raw).hexdigest() == manifest[name], f'checksum mismatch: {name}')
        # Never parse imported MIDI, WAV, or HTML. They are integrity-checked, then discarded.
        if name in ('score.json', 'image.png'):
            files[name] = raw
    score = parse_json(files['score.json'])
    validate_score(score)
    with warnings.catch_warnings():
        warnings.simplefilter('error', Image.DecompressionBombWarning)
        with Image.open(io.BytesIO(files['image.png'])) as source:
            require(source.format == 'PNG' and source.mode == 'RGB' and
                    getattr(source, 'n_frames', 1) == 1, 'bundle image must be a single RGB PNG')
            require(source.size == (score['width'], score['height']), 'image dimensions do not match score')
            source.load()
            image = Image.frombytes('RGB', source.size, source.tobytes())  # Discard metadata.
    for event in score['events']:
        cell = image.crop(event['region'])
        histogram = cell.histogram()
        rgb = [sum(v * histogram[c*256+v] for v in range(256)) // (cell.width * cell.height) for c in range(3)]
        require(event['rgb'] == rgb, 'region RGB does not match image')
    parent = hashlib.sha256(core.canonical(manifest)).hexdigest()
    return score, image, parent


def apply_edits(score, spec, parent):
    """Pure transformation: tempo, transpose, velocity, then mute. Never clip."""
    validate_score(score)
    digest(parent)
    ids = {f'n{i:03d}' for i in range(len(score['events']))}
    edits = validate_edits(spec, ids)
    result = copy.deepcopy(score)
    result['version'] = 2
    result['provenance'] = {'parent_bundle_sha256': parent, 'edits': edits}
    tempo = edits.get('tempo', result['settings']['tempo'])
    result['settings']['tempo'] = tempo
    result['tempo_us'] = round(60_000_000 / tempo)
    require(len(result['events']) * result['tempo_us'] <= core.MAX_DURATION * 2_000_000,
            'edited score exceeds duration limit')
    for index, event in enumerate(result['events']):
        name = event['id'] = f'n{index:03d}'
        event['pitch'] = integer(event['pitch'] + edits['transpose'], 0, MAX_PITCH, 'transposed pitch')
        event['velocity'] = edits['velocity'].get(name, event['velocity'])
        event['muted'] = edits['mute'].get(name, event.get('muted', False))
        event['start_sample'] = round(index * result['tempo_us'] * core.RATE / 2_000_000)
        event['end_sample'] = round((index+1) * result['tempo_us'] * core.RATE / 2_000_000)
    result['sample_count'] = result['events'][-1]['end_sample']
    validate_score(result)
    return result


def edit_bundle(bundle, specification, destination):
    destination = Path(destination).absolute()
    if os.path.lexists(destination):
        raise FileExistsError(f'output already exists: {destination}')
    require(not destination.resolve().is_relative_to(Path(bundle).resolve()), 'output cannot be inside input bundle')
    spec = parse_json(read_bounded(specification, MAX_EDIT))
    score, image, parent = load_bundle(bundle)
    edited = apply_edits(score, spec, parent)
    core.publish(core.artifacts(edited, image), destination)
    return edited
