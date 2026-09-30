# Saved-bundle editing

`image-score-edit BUNDLE EDITS.json OUTPUT` is available after installing version
1.1.0. From an editable checkout, the equivalent is
`python -m image_score.cli edit BUNDLE EDITS.json OUTPUT`. `image-score IMAGE OUTPUT`
retains the original conversion interface and emits the original version 1 format.
The original conversion template and historical workload hashes remain unchanged.

## Edit specification version 1

Required fields are `format: "image-score-edit"` and integer `version: 1`.
Optional fields are:

| Field | Value | Meaning |
| --- | --- | --- |
| `tempo` | Integer 30–240 | Absolute beats per minute for the entire score |
| `transpose` | Integer −119–119, default 0 | Relative semitones added to every pitch, including rests and muted notes |
| `velocity` | Object mapping note IDs to integer 0–127, default `{}` | Absolute stored velocity assignments; zero is silent |
| `mute` | Object mapping note IDs to booleans, default `{}` | Absolute mute assignments; `false` unmutes |

IDs are `n000`, `n001`, …, `n255`, assigned from the immutable row-major event
index. Only IDs that exist in this score are accepted. No insertion, deletion,
reordering, region editing, duration editing, tempo maps, or per-note pitch editing
is supported. Transposition can move pitches outside the original scale's root;
`settings.scale` records the **source mapping palette**, not a promise about the
edited notes' root. RGB and brightness remain image evidence, not a recalculated
explanation of edited pitch or velocity.

Unknown fields, unsupported versions, duplicate keys (at any nesting level),
non-finite numbers, fractional integers, boolean-as-integer values, and unknown
IDs are errors. Every resulting pitch must be 0–119. MIDI supports higher pitches,
but this bounded subset keeps sine fundamentals below the 8 kHz Nyquist limit of
the existing 16 kHz preview. This explicit rejection prevents upper-octave aliasing;
there is no clipping, octave folding, or silent adjustment. Very low pitches may
be inaudible on typical speakers. This is not a perceptual loudness model.

## Operation order and timing

1. Replace tempo if present and compute `tempo_us = round(60000000/tempo)`.
2. Add transposition to every stored pitch and reject any out-of-range result.
3. Assign the specified stored velocities.
4. Assign the specified mute flags. Unspecified mute flags retain their old value.
5. Recompute sample boundaries from the quantized tempo, preserving tick positions.

All checks must pass before publication. Operations are atomic for the whole
bundle. A muted note's effective rendered velocity is zero, but its stored pitch
and velocity are preserved. Setting velocity on a muted note does not unmute it;
unmuting a zero-velocity note still produces silence. Every cell occupies 240 ticks
at 480 PPQN even when silent. MIDI omits silent note messages but retains elapsed
ticks, including trailing silence at end-of-track. WAV contains exact digital
silence in those intervals. The report highlights muted regions on the same clock.

Both exporters use the same integer microseconds per beat. Sample boundary i is
`round(i * tempo_us * 16000 / 2000000)` (ties to even); timing error relative to
MIDI is at most half a sample per boundary. The final sample boundary is the WAV
length and the report duration. Total ideal duration must not exceed 60 seconds,
including silence. Synthesis, envelope, gain, and MIDI conventions are unchanged
from [version 1](format.md).

An identity edit (`{"format":"image-score-edit","version":1}`) preserves MIDI,
WAV and processed PNG bytes in the pinned environment. JSON changes to version 2
and records provenance; HTML and checksums therefore change too. For sequential
edits, relative transposition accumulates: `+7`, then `−2`, yields `+5`. Absolute
tempo, velocity and mute assignments use the latest assigned value. Validation
occurs after each generation, so an out-of-range intermediate edit is rejected even
if a hypothetical later inverse would return it to range. Undo requires an explicit
inverse specification or the saved parent bundle; no implicit undo stack exists.

## Score version 2 and provenance

Version 2 retains every version 1 field, adding `id` and boolean `muted` to every
event, plus top-level:

```json
{
  "provenance": {
    "parent_bundle_sha256": "64 lowercase hexadecimal characters",
    "edits": {
      "format": "image-score-edit", "version": 1,
      "transpose": 0, "velocity": {}, "mute": {}
    }
  }
}
```

The parent fingerprint is SHA-256 of the **canonical parsed parent checksums map**:
sorted keys, two-space indentation, ASCII escaping, trailing newline, UTF-8 bytes.
The map covers exactly `score.json`, `score.mid`, `preview.wav`, `image.png`, and
`report.html`. Its fingerprint is independent of the manifest's whitespace or key
order but commits to every asset hash. It is not the hash of the directory name,
original image, or raw manifest bytes. Normalized edits always include transpose,
velocity and mute defaults; tempo is present only if explicitly supplied.

Only immediate-parent provenance is copied into the new score. Keep the immutable
parent bundles to reconstruct a longer chain. Identity edits on successive bundles
produce the same musical data but distinct provenance. No timestamp or absolute
path is included. Hashes detect inconsistent assets; they do **not** authenticate
a sender or prove history when an attacker can rewrite assets and manifests.

## Import and resource boundaries

Imports accept only complete version 1 or version 2 directories with exactly six
regular, non-symlink files: the five named assets and `checksums.json`. Unknown,
missing, duplicate-manifest, traversal, symlink, and special-file inputs fail. The
output must be a fresh directory outside the input bundle, with an existing parent.
All six input files are read with bounded reads; the image and score snapshots are
retained for validation. No original filename is opened or interpreted as a path.

| Limit | Import or output rule |
| --- | --- |
| Edit JSON | 32 KiB |
| Score JSON / checksum JSON | 256 KiB each |
| Imported asset | 10 MiB per file |
| Imported bundle | 32 MiB total including checksums |
| Image | Single-frame RGB PNG, at most 4,000,000 pixels |
| Score | 1–256 notes; integer BPM 30–240; at most 60 seconds |
| Metadata strings | Input name 1–1024 characters, Pillow version 1–64 |
| Output | 10 MiB per file, 32 MiB for the six-file bundle |

Score fields and nested schemas are exact. Import checks grid/count consistency,
event indices/IDs, integer ranges, fixed ticks and derived sample boundaries,
image dimensions, exact grid-region boxes, RGB means recomputed from those image
regions, and brightness. Version 1 also checks its original pitch/velocity mapping;
version 2 permits its bounded edited pitch/velocity values and validates provenance.
PNG metadata is removed. The PNG's dimensions are checked before pixel decoding.

Imported MIDI, WAV and HTML are **hashed but not parsed, played, or executed** by
the importer. They are discarded after integrity checking; the authoritative score
and processed image regenerate all outputs. This is not a general MIDI/WAV validator,
an HTML sanitizer, or an import path for a manually edited external MIDI file.
A checksummed but otherwise nonsensical media file can be imported and replaced.
New HTML uses the installed template, base64 payloads, `textContent` for imported
text, and a restrictive CSP. The report has no network dependencies.

Output byte sizes are computed before WAV synthesis and base64/HTML allocation,
using the actual encoded PNG, score, MIDI, template and exact PCM length. Final
sizes are checked again. PNG encoding itself is bounded by the validated pixel
count. Publication stages siblings and atomically renames without replacement;
exceptions, Ctrl-C and SIGTERM clean up staging. Concurrent destination creation
causes failure instead of replacement. Forced kill/power loss may leave staging;
there is no crash-durability guarantee. Inputs should not be concurrently modified
by another process; this is not a hostile-filesystem sandbox.

A lone downloaded HTML is self-contained for viewing, but is not a complete import
bundle. Browser-serialized HTML may differ from the original byte hash; keep the
CLI directory intact for subsequent editing. Applications that understand only
score version 1 should reject version 2; MIDI and WAV remain standard media files.

## Verification and limits of evaluation

Run `.venv/bin/python scripts/verify.py` with the pinned development dependencies
and Chromium installed. It retains the original 240 mapping cases and historical
hash check, adds 240 seeded edit-oracle cases, independent MIDI/WAV analysis,
malformed-import/resource/failure tests, offline edited playback and downloads,
and isolated installed conversion→edit→re-edit without the source image. See
[validation](validation.md) and `results/edit-benchmark.json` for actual measurements.

Linux and Chromium are the exercised platform/browser. No human listening,
physical audio latency, screen-reader usability, blinded musical-quality study,
or cross-platform binary reproducibility has been evaluated for this milestone.
Bounds and negative tests do not constitute exhaustive codec or filesystem fuzzing.
