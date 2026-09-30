# Mapping and file format, version 1

## Input and sampling

Read the original bytes once, with an 8 MiB limit; SHA-256 records exactly those
bytes. Content must decode as a single-frame PNG or JPEG. Accept Pillow modes 1,
L, LA, P, RGB, RGBA, and CMYK; reject other bit depths/modes. Check the decoded
width × height before loading pixels (maximum 4,000,000). Apply EXIF orientation,
convert using Pillow 11.3.0 to RGBA, alpha-composite against opaque black, then
convert to RGB. Palette transparency is included. Grayscale channels are
replicated. CMYK uses Pillow's conversion, not ICC-managed color. Embedded ICC,
gamma, and chromaticity metadata do not modify encoded channel values; these
are treated as sRGB-like bytes, without linear-light conversion. The exported PNG
contains the exact RGB pixels used by the mapping and strips source metadata.

Partition oriented width W and height H into C columns and R rows. Cell (c,r)
has half-open bounds `[floor(cW/C), floor(rH/R), floor((c+1)W/C), floor((r+1)H/R))`.
Grid dimensions are positive integers, cannot exceed corresponding image
dimensions, and C×R ≤ 256. There are no empty cells, overlaps, skipped pixels,
resizing, or random sampling. Scan rows from top to bottom and columns left to
right. The RGB value is each channel's arithmetic mean over all cell pixels,
rounded down. Alpha compositing uses Pillow's integer rounding before this mean.

Compute integer brightness:

```
B = floor((2126*R + 7152*G + 722*Bchannel) / 10000)
```

This is a weighted encoded-channel brightness heuristic, not calibrated physical
luminance. The scale offsets are major `[0,2,4,5,7,9,11]`, natural minor
`[0,2,3,5,7,8,10]`, or pentatonic `[0,2,4,7,9]`. Concatenate three octaves beginning
at MIDI 48. For palette length N, choose index `floor(brightness*N/256)`.
Velocity is zero for brightness zero (a rest), otherwise
`24 + floor(brightness*103/255)`. Color hue does not independently alter pitch.

## Timing and synthesis

Tempo must be integer 30–240 BPM. Microseconds per beat is
`round(60000000/tempo)` with ties to even. Store this quantized MIDI tempo;
all exports use it. PPQN is 480, every cell occupies 240 ticks. Event i starts
at `240*i`. Reject a duration over 60 seconds before image decoding.

At 16,000 samples/s, sample boundary i is
`round(i*tempo_us*16000/2000000)`, ties to even. Adjacent notes share this boundary
without gaps or overlaps. JSON records start/end sample offsets explicitly.
Total samples is the final end boundary; sample timing differs from ideal MIDI
time by at most half a sample at each boundary.

Each audible cell uses a sine with frequency `440*2**((pitch-69)/12)` and phase
reset to zero at its start. For local sample j in a cell with L samples:

```
envelope = min(1, j/160, (L-1-j)/160)
gain = 0.7 * velocity/127
pcm = round(32767 * gain * envelope * sin(2*pi*frequency*j/16000))
```

The 160-sample (10 ms) ramps suppress hard boundary clicks. The signal is mono,
never overlaps notes, and has peak magnitude ≤ 22937. A zero velocity produces
exact digital silence. The WAV uses RIFF/WAVE with PCM format 1, one channel,
16-bit signed little-endian integers and a 16 kHz sample rate.

MIDI is format 0, one track, division 480, channel 0, program 0 (piano), one tempo
meta event at tick zero, explicit note-on/off pairs, and end-of-track at the final
cell boundary. Silent cells contribute elapsed ticks but no note-on. At equal
ticks, note-off precedes the next note-on. MIDI playback timbre is up to the player;
the sine preview is not a General MIDI piano simulation.

## JSON and reproducibility

`format: "image-score"`, `version: 1`. Top-level fields include original filename
and SHA-256, oriented width/height, Pillow version, settings, quantized tempo,
PPQN, sample rate/count, and `events`. Each event includes index, region bounds,
mean RGB, brightness, pitch, velocity, start tick, duration ticks, and start/end
sample offsets. A rest retains its mapped pitch for inspection, with velocity 0.

These fields suffice to regenerate the musical data without decoding the source
image. The processed image is included for visual verification. No timestamps,
absolute paths, random IDs, or original EXIF metadata enter the artifacts. JSON
uses sorted keys, ASCII escapes, two-space indentation, and a trailing newline.
Repeated bytes and settings, including the same filename, produce byte-identical
artifacts in the pinned environment. Renaming an input changes JSON and report
metadata. Floating-point sine and image codec implementations can vary between
platforms; cross-platform binary identity is not promised.

Limits apply to final artifact bytes, including the checksum manifest. Checksums
cover the five content artifacts but not their own manifest. The HTML contains
base64 data, so filenames cannot break out into markup or JavaScript. User text
is inserted with `textContent`. CSP denies connections and permits only embedded
images, blob audio, and inline script/style. The report does not load external
scripts, fonts, images, or analytics.

## Saved-score editing compatibility

Conversion continues to emit the version 1 format described here. The offline
`image-score-edit` command accepts these bundles and emits score version 2, adding
stable note IDs, explicit mute state, and immediate-parent provenance. See
[the edit specification and version 2 contract](editing.md). The default conversion
artifacts and historical reproducibility references remain unchanged.
