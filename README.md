# Image Score Studio

Turn a local PNG or JPEG into an editable MIDI melody, a playable WAV, and an
interactive explanation. Open the generated HTML directly from disk: inspect
notes, seek through the image, and download the embedded artifacts without a
server or network connection.

This is a deliberately simple artistic instrument. Brightness controls pitch and
velocity; the grid determines order and timing. It does not recognize objects or
infer what an image “sounds like.” Black regions are silent.

## Setup and first score

Python 3.12+ with venv support is required. Linux is the validated platform.
Initial dependency installation needs a package index or a preloaded wheelhouse;
conversion and viewing work offline.

```sh
python3 -m venv .venv
.venv/bin/python -m pip install .
.venv/bin/python scripts/fixtures.py /tmp/image-score-inputs
.venv/bin/image-score /tmp/image-score-inputs/gradient.png /tmp/my-image-score \
  --tempo 120 --scale pentatonic --columns 8 --rows 4
```

Open `/tmp/my-image-score/report.html` in Chromium. Press **Play**; pause with the
same button, drag the position slider, or select a note. Arrow keys, Home, and End
navigate note buttons. Playback follows the exported WAV; it does not rely on an
external synthesizer. The output directory must not exist and its parent must
already exist. Use a different output name for each run.

Available scales are `major`, `minor`, and `pentatonic`, rooted at MIDI 48 (C3),
spanning three octaves. Each region lasts an eighth note. Tempo is integer BPM
30–240. The default grid is 8 columns × 4 rows; smaller images need a smaller grid.

The bundle contains:

| File | Purpose |
| --- | --- |
| `score.json` | Versioned mapping, source hash, settings, region colors, and events |
| `score.mid` | Standard MIDI format 0, one track, piano program |
| `preview.wav` | Mono 16-bit PCM, 16 kHz, synthesized sine notes |
| `image.png` | Oriented image composited onto black, metadata removed |
| `report.html` | Self-contained viewer with image, JSON, MIDI, and WAV embedded |
| `checksums.json` | SHA-256 of the other five generated files |

Browser downloads include the five content artifacts; the external checksum
manifest is available in the CLI bundle. A browser-saved HTML is serialized by the
browser and need not have the original HTML's byte hash.

## Edit a saved score offline

Keep the complete six-file bundle. The original image file is no longer needed.
Create a JSON edit specification, or use [`examples/edits.json`](examples/edits.json):

```json
{
  "format": "image-score-edit",
  "version": 1,
  "tempo": 90,
  "transpose": 7,
  "velocity": {"n001": 40},
  "mute": {"n000": true}
}
```

```sh
.venv/bin/image-score-edit /tmp/my-image-score examples/edits.json /tmp/my-edited-score
```

Open `/tmp/my-edited-score/report.html` to play, seek, inspect the edited notes,
and download synchronized JSON, MIDI and WAV. The CLI emits a fresh complete bundle,
including checksums. It validates the saved score, image regions and asset hashes
before export. Imported HTML is discarded and rebuilt from the installed template.
No source image lookup, inference service, or network connection is needed.

IDs start at `n000` in row-major order and remain stable across edits. Version 1
conversion bundles receive IDs on import; edited scores use score format version 2.
Tempo is an absolute integer BPM (30–240), transpose is a relative integer semitone
offset, velocity is absolute (0–127), and mute is an absolute boolean. Muting keeps
the stored velocity and the note's duration; use `false` in a later edit to unmute.
Omitted values remain unchanged. An empty edit uses just `format` and `version`.
All pitches must remain 0–119 for the 16 kHz preview; invalid values fail, never clip.

Reapply `image-score-edit` to the edited bundle for another generation. Transposition
accumulates, while tempo, velocity and mute assignments replace previous values.
Every generation records its immediate parent bundle fingerprint and normalized
edits. Keep parent bundles to retain the full history. See [edit format and import
limits](docs/editing.md) for exact semantics, compatibility, and trust boundaries.
This workflow does not import MIDI changes made in other editors.

## Verify everything

Provision the test dependencies and Chromium once:

```sh
.venv/bin/python -m pip install -r requirements-dev.txt
.venv/bin/python -m pip install --no-build-isolation -e .
.venv/bin/python -m playwright install chromium
```

Then one command runs the independent mapping/export checks, offline Chromium
interaction and decoder tests, isolated installed-entry-point check, and workloads:

```sh
.venv/bin/python scripts/verify.py
```

The test runner also recognizes a browser installation at
`/tmp/image-score-browsers`; otherwise Playwright's standard location applies.
All verification stages require preinstalled dependencies and run without fetching
packages. Chromium may require host libraries supplied by your OS.

See [the format and limits](docs/format.md), [prior work and specifications](docs/sources.md),
and [validation and measured results](docs/validation.md).

## Limits

At most 8 MiB input, 4 million decoded pixels, 256 regions, and 60 seconds of audio.
Each output file must fit within 10 MiB and the bundle within 32 MiB. Highly textured
images can exceed the output limit even when the input fits; reduce image size.
Animated and high-bit-depth images are rejected. ICC profiles are not applied.
The output contains your processed image, original filename, and source hash;
share it only when you intend to share that information.

Complete bundles are staged beside the destination and published atomically with
no replacement. Existing files, directories, and symlinks are never overwritten.
Ctrl-C, SIGTERM, and caught write errors remove staging files. Forced process kill
or power failure can leave a hidden `.image-score-*` staging directory; remove it
only after confirming no conversion is running. Disk durability across power loss
is not guaranteed. Atomic publication supports Linux `renameat2`, macOS
`renamex_np`, and Windows rename; only Linux has been exercised here. Unsupported
filesystems/platforms fail rather than fall back to overwriting.

Musical quality and accessibility with a screen reader have not been evaluated by
people. Automated browser verification covers Chromium, not Safari or Firefox.
Timing reflects the media clock; audible device latency and physical listening
quality are not measured.
