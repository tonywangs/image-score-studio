"""Version 1 mapping and bounded exporters; no network operations."""
import base64
import ctypes
import errno
import hashlib
import io
import json
import math
import os
from pathlib import Path
import shutil
import struct
import tempfile
import sys
import warnings
import wave

from PIL import Image, ImageOps, __version__ as pillow_version

MAX_INPUT = 8 * 1024 * 1024
MAX_PIXELS = 4_000_000
MAX_NOTES = 256
MAX_DURATION = 60
MAX_FILE = 10 * 1024 * 1024
MAX_BUNDLE = 32 * 1024 * 1024
RATE = 16000
SCALES = {"major": [0, 2, 4, 5, 7, 9, 11], "minor": [0, 2, 3, 5, 7, 8, 10], "pentatonic": [0, 2, 4, 7, 9]}


def canonical(value):
    return (json.dumps(value, sort_keys=True, indent=2, ensure_ascii=True) + "\n").encode()


def read_image(path):
    with open(path, "rb") as stream:
        raw = stream.read(MAX_INPUT + 1)
    if len(raw) > MAX_INPUT:
        raise ValueError("input exceeds 8 MiB")
    with warnings.catch_warnings():
        warnings.simplefilter("error", Image.DecompressionBombWarning)
        with Image.open(io.BytesIO(raw)) as source:
            if source.format not in ("PNG", "JPEG"):
                raise ValueError("only PNG and JPEG are supported")
            if source.width * source.height > MAX_PIXELS:
                raise ValueError("decoded image exceeds 4 million pixels")
            if getattr(source, "n_frames", 1) != 1:
                raise ValueError("animated images are not supported")
            if source.mode not in ("1", "L", "LA", "P", "RGB", "RGBA", "CMYK"):
                raise ValueError("unsupported color depth or mode")
            source.load()
            rgba = ImageOps.exif_transpose(source).convert("RGBA")
            image = Image.alpha_composite(Image.new("RGBA", rgba.size, (0, 0, 0, 255)), rgba).convert("RGB")
    return raw, image


def score_image(path, *, tempo=120, scale="pentatonic", columns=8, rows=4):
    if type(tempo) is not int or not 30 <= tempo <= 240:
        raise ValueError("tempo must be an integer from 30 to 240")
    if scale not in SCALES:
        raise ValueError("unknown scale")
    if any(type(n) is not int or n < 1 for n in (columns, rows)) or columns * rows > MAX_NOTES:
        raise ValueError("grid must contain 1 to 256 cells")
    tempo_us = round(60_000_000 / tempo)
    duration = columns * rows * tempo_us / 2_000_000
    if duration > MAX_DURATION:
        raise ValueError("score exceeds 60 seconds")
    raw, image = read_image(path)
    width, height = image.size
    if columns > width or rows > height:
        raise ValueError("grid cannot exceed image dimensions")
    palette = [48 + 12 * octave + degree for octave in range(3) for degree in SCALES[scale]]
    events = []
    for row in range(rows):
        for col in range(columns):
            box = [col * width // columns, row * height // rows, (col + 1) * width // columns, (row + 1) * height // rows]
            cell = image.crop(box)
            histogram = cell.histogram()
            count = cell.width * cell.height
            rgb = [sum(v * histogram[c * 256 + v] for v in range(256)) // count for c in range(3)]
            brightness = (2126 * rgb[0] + 7152 * rgb[1] + 722 * rgb[2]) // 10000
            pitch = palette[min(len(palette) - 1, brightness * len(palette) // 256)]
            velocity = 0 if brightness == 0 else 24 + brightness * 103 // 255
            index = len(events)
            events.append(dict(index=index, region=box, rgb=rgb, brightness=brightness, pitch=pitch,
                               velocity=velocity, start_tick=index * 240, duration_ticks=240,
                               start_sample=round(index * tempo_us * RATE / 2_000_000),
                               end_sample=round((index + 1) * tempo_us * RATE / 2_000_000)))
    score = dict(format="image-score", version=1, input_sha256=hashlib.sha256(raw).hexdigest(),
                 input_name=Path(path).name, width=width, height=height, pillow=pillow_version,
                 settings=dict(tempo=tempo, scale=scale, columns=columns, rows=rows),
                 tempo_us=tempo_us, ticks_per_beat=480, sample_rate=RATE,
                 sample_count=events[-1]["end_sample"], events=events)
    return score, image


def vlq(n):
    result = [n & 127]
    while n >> 7:
        n >>= 7
        result.insert(0, (n & 127) | 128)
    return bytes(result)


def midi_bytes(score):
    track = bytearray(b"\x00\xff\x51\x03" + score["tempo_us"].to_bytes(3, "big") + b"\x00\xc0\x00")
    pending = 0
    for event in score["events"]:
        if event["velocity"] and not event.get("muted", False):
            track += vlq(pending) + bytes([0x90, event["pitch"], event["velocity"]])
            track += vlq(240) + bytes([0x80, event["pitch"], 0])
            pending = 0
        else:
            pending += 240
    track += vlq(pending) + b"\xff\x2f\x00"
    return b"MThd" + struct.pack(">IHHH", 6, 0, 1, 480) + b"MTrk" + struct.pack(">I", len(track)) + track


def wav_bytes(score):
    pcm = bytearray(score["sample_count"] * 2)
    for event in score["events"]:
        if event.get("muted", False) or not event["velocity"]:
            continue
        start, end = event["start_sample"], event["end_sample"]
        hz = 440 * 2 ** ((event["pitch"] - 69) / 12)
        gain = 0.7 * event["velocity"] / 127
        for i in range(end - start):
            envelope = min(1, i / 160, (end - start - 1 - i) / 160)
            value = round(32767 * gain * envelope * math.sin(2 * math.pi * hz * i / RATE))
            struct.pack_into("<h", pcm, 2 * (start + i), value)
    stream = io.BytesIO()
    with wave.open(stream, "wb") as output:
        output.setparams((1, 2, RATE, 0, "NONE", "not compressed"))
        output.writeframes(pcm)
    return stream.getvalue()


def artifacts(score, image):
    png = io.BytesIO()
    image.save(png, format="PNG")
    # Calculate exact encoded sizes before allocating/synthesizing the WAV or HTML.
    files = {"score.json": canonical(score), "score.mid": midi_bytes(score), "preview.wav": b"", "image.png": png.getvalue()}
    template_name = "edited-report.html" if score["version"] == 2 else "report.html"
    template = Path(__file__).with_name(template_name).read_text()
    sizes = {name: len(data) for name, data in files.items()}
    sizes["preview.wav"] = 44 + score["sample_count"] * 2
    payload_size = len(json.dumps({name: "" for name in files})) + sum(4 * ((size + 2) // 3) for size in sizes.values())
    sizes["report.html"] = len(template.encode()) - len("__PAYLOAD__") + payload_size
    sizes["checksums.json"] = len(canonical({name: "0" * 64 for name in sizes}))
    if max(sizes.values()) > MAX_FILE or sum(sizes.values()) > MAX_BUNDLE:
        raise ValueError("output size limit exceeded before audio rendering")
    files["preview.wav"] = wav_bytes(score)
    payload = {name: base64.b64encode(data).decode() for name, data in files.items()}
    files["report.html"] = template.replace("__PAYLOAD__", json.dumps(payload)).encode()
    files["checksums.json"] = canonical({name: hashlib.sha256(data).hexdigest() for name, data in files.items()})
    if any(len(data) > MAX_FILE for data in files.values()) or sum(map(len, files.values())) > MAX_BUNDLE:
        raise ValueError("output size limit exceeded")
    return files


def rename_fresh(source, destination):
    """Atomic directory publication without replacing even an empty destination."""
    if os.name == "nt":
        os.rename(source, destination)  # Windows rename never replaces a directory.
        return
    libc = ctypes.CDLL(None, use_errno=True)
    src, dst = os.fsencode(source), os.fsencode(destination)
    if sys.platform.startswith("linux") and hasattr(libc, "renameat2"):
        result = libc.renameat2(-100, ctypes.c_char_p(src), -100, ctypes.c_char_p(dst), 1)
    elif sys.platform == "darwin" and hasattr(libc, "renamex_np"):
        result = libc.renamex_np(ctypes.c_char_p(src), ctypes.c_char_p(dst), 4)
    else:
        raise OSError(errno.ENOTSUP, "atomic no-replace directory rename is unavailable")
    if result:
        code = ctypes.get_errno()
        raise OSError(code, os.strerror(code), str(destination))


def publish(files, destination):
    """Stage siblings, then atomically publish; never replace an existing path."""
    destination = Path(destination).absolute()
    staging = Path(tempfile.mkdtemp(prefix=".image-score-", dir=destination.parent))
    try:
        for name, data in files.items():
            with (staging / name).open("xb") as output:
                output.write(data)
        rename_fresh(staging, destination)
    finally:
        if staging.exists():
            shutil.rmtree(staging)


def convert(path, destination, **settings):
    score, image = score_image(path, **settings)
    publish(artifacts(score, image), destination)
    return score
