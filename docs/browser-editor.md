# Offline browser editor

Version 1.2 adds an editor generated from a **complete validated saved bundle**.
Keep the six-file source bundle for CLI rendering; the original photograph is not
needed. The generated editor contains a sanitized image, source score, bundle
fingerprint, JavaScript and styles. It makes no external requests.

```sh
.venv/bin/image-score-editor /tmp/my-image-score /tmp/my-score-editor
# Equivalent from the checkout:
.venv/bin/python -m image_score.cli editor /tmp/my-image-score /tmp/my-score-editor
```

Open `/tmp/my-score-editor/editor.html` directly in Chromium. Change tempo or
transpose, select a region and adjust velocity or mute, then press **Play**.
Fields commit on Enter or change/blur. Invalid changes announce an error and
restore the accepted controls without altering edits or undo history. Selecting a
region, seeking, or focusing an edit field pauses playback. Arrow keys, Home and End move among notes;
the selected note is a single Tab stop. Controls have visible keyboard focus.

**Download edits.json**, then render the exact events through the existing CLI:

```sh
.venv/bin/image-score-edit /tmp/my-image-score /path/to/edits.json /tmp/rendered-score
.venv/bin/image-score-editor /tmp/rendered-score /tmp/next-editor
```

The rendered directory contains JSON, MIDI, WAV and the existing offline report.
Its new editor starts a new editing generation. Existing conversion and CLI editing
semantics and both historical report templates remain unchanged.

## State and operation order

The [version 1 edit specification](editing.md) is used without new fields. Tempo
is absolute BPM (30–240). Transpose is an integer offset **from the source bundle**,
not an incremental button action: changing +7 to +2 yields source pitch +2.
Velocity is an absolute integer 0–127; mute is a separate boolean. IDs are the
existing row-major `n000` through `n255`. All resulting pitches, including muted
notes, must remain 0–119. Muting preserves velocity and occupies the same time slot.

For every accepted state, start from the immutable source, replace tempo, add
transpose, assign velocities, then assign mute flags. Recompute sample boundaries
with the CLI's integer microsecond tempo and ties-to-even rounding. No note is
inserted, removed, reordered, or shortened. Palette metadata still describes the
source image mapping. This is a bounded editor, not a general MIDI sequencer.

Exports include `format`, `version`, `transpose`, `velocity`, and `mute`; `tempo`
appears only after it is assigned or imported. Explicit no-op assignments are
retained. Sorted keys, two-space indentation and a final newline make repeated
exports of the same state byte-identical and compatible with Python normalization.
Applying the export once to its source reproduces the displayed score. Applying it
again to the result adds transposition again, as the existing CLI specifies.

Import replaces the entire current edit specification, not a patch against it.
Omitted tempo/velocity/mute values therefore revert to source values. Import, reset
and individual field changes participate in the same undo history. At most 50 prior
states and 50 redo states are retained; a new edit clears redo. Reloading loses
history and unsaved progress. Reset restores the source and itself can be undone.

## Sessions and trust

**Download session.json** saves current edits with this wrapper:

```json
{
  "format": "image-score-session",
  "version": 1,
  "source_bundle_sha256": "64 lowercase hexadecimal characters",
  "edits": {"format": "image-score-edit", "version": 1}
}
```

Reopen the same editor and import that session. The fingerprint uses the existing
canonical source checksum manifest definition in [editing.md](editing.md).
A session for a different source bundle is rejected without changing progress.
Sessions contain edits, not the image, undo history, selected note or play position.
They are not accepted directly by `image-score-edit`; download `edits.json` from
the restored editor for the CLI. There is no autosave or browser storage dependency.

Plain edit JSON deliberately has no identity field to preserve CLI compatibility.
It can be imported into another bundle with matching IDs; the UI states this
explicitly. Use sessions when source identity matters. Fingerprints detect mismatch,
not authenticity. The HTML itself is executable software and is not a signed or
sandboxed container; do not execute untrusted modified HTML.

The importer checks UTF-8, byte limits, exact schemas, duplicate keys, numeric syntax,
IDs, types, pitch and duration before committing. Numeric values must use integer
JSON syntax: `1.0`, `1e0`, booleans and null are rejected for integer fields, matching
the Python importer. Unknown fields and excessively nested input fail. Imported
strings are never inserted as HTML. A pending file read cannot overwrite edits
made after it began; import again if that race is reported.

## Preview and limits

Preview uses a mono 16 kHz Web Audio buffer synthesized on Play. It uses sine tones
and short ramps with velocity-scaled gain. This is **browser synthesis**, not the
CLI WAV renderer; waveform samples, envelope, gain, resampling and output hardware
may differ. Event pitch, velocity, mute, ordering and sample timing match the CLI.
The audio context clock drives the seek display and image highlight. Pause retains
position; seek pauses; Restart returns to zero. Accepted edits, reset, import and
history navigation stop the old source and discard its buffer. Pending audio starts
are invalidated. No audio starts on load. If Web Audio is unavailable or denied,
an announced error leaves editing and downloads usable.

The existing importer enforces 32 MiB input bundle, 10 MiB assets, 256 KiB score JSON,
4 million image pixels, 256 notes and 60 seconds. Editor HTML is capped at 10 MiB;
generation fails before embedding an oversized final payload, and publication uses
the existing atomic fresh-directory mechanism. Base64 encoding can make a large
textured image exceed this limit even when its bundle is valid. Source image and
score are embedded, not imported media or HTML.

Plain edit JSON is capped at 32 KiB, sessions at 40 KiB, JSON nesting at eight levels,
and downloads at 40 KiB. Rendering mounts at most 256 note buttons and a single
selected-note control panel. Audio allocates at most 960,000 Float32 samples
(3,840,000 bytes), one cached buffer and one playing source. History stores bounded
edit maps, not images or audio. Download object URLs replace the previous URL.

## Verification

Run `.venv/bin/python scripts/verify.py`. It retains all historical checks and adds
240 seeded browser/Python/exact-rational comparisons, actual offline downloads,
CLI regeneration and reopen, installed wheel assets/entry point checks, invalid
imports, source mismatch, keyboard/focus checks, unavailable audio, resource bounds,
and bounded generation/load/DOM measurements. See `results/editor-benchmark.json`
for measured samples, seeds in `tests/test_editor.py`, and workloads in
`scripts/editor_benchmark.py`. Timing measurements are descriptive, not universal
performance promises. The browser test runs with network access blocked.

Linux and bundled Chromium are tested. No human listening, screen-reader session,
physical audio latency measurement, mobile testing, or Safari/Firefox evaluation
has been performed. Automated roles, error announcements and focus assertions do
not establish full accessibility conformance. Preview buffers are checked for
signal and muted silence; physical speaker output is not measured.
