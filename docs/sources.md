# Prior work and format references

Reviewed 2026-09-28. This project claims no novelty for image sonification.

- [Lumena, original implementation and documentation](https://github.com/pixelsncodes/lumena):
  an existing C++ image-to-MIDI library. Its documented pipeline uses image analysis,
  scale selection, and melodic generation. Image Score Studio instead uses a fixed,
  row-major per-cell mapping to make every event directly inspectable. Neither
  that simplification nor image-to-MIDI conversion is claimed as new.
- [MIDI Association: Standard MIDI Files](https://midi.org/standard-midi-files):
  official specification landing page.
- [Standard MIDI File specification, historical text mirror](https://midimusic.github.io/tech/midispec.html):
  specification text attributed to the International MIDI Association. Used for
  header/track chunks, variable-length delta times, PPQN, tempo and end-of-track
  meta events. The writer implements a narrow format-0 subset; Mido independently
  parses every seeded test export.
- [Microsoft: Resource Interchange File Format](https://learn.microsoft.com/en-us/windows/win32/xaudio2/resource-interchange-file-format--riff-):
  primary RIFF documentation describing the WAVE container and its chunks. The
  preview uses Python's standard-library WAV writer and uncompressed integer PCM.

These sources inform the file interoperability and establish existing work. They
do not validate the artistic choices, perceptual usefulness, or musical quality
of this project's brightness-to-pitch mapping.

## Editing milestone references (reviewed 2026-09-30)

- [MIDI Association: Summary of MIDI 1.0 Messages](https://midi.org/summary-of-midi-1-0-messages):
  primary note-on/off and data-byte reference. The editor emits ordinary channel-0
  note pairs and uses an explicitly narrower 0–119 pitch range for its fixed-rate
  audio preview. Stored velocity and a separate application mute flag are distinct.
- [MIDI Association: Standard MIDI Files](https://midi.org/standard-midi-files):
  reviewed the official purpose and format overview for exchanging time-stamped
  musical data. The existing format-0 writer and timing conventions are retained.
- [Mido: Standard MIDI Files](https://mido.readthedocs.io/en/stable/files/midi.html):
  primary implementation documentation for delta ticks, PPQN, microseconds-per-beat
  tempo, end-of-track, and independent parsing. Mido already supports reading,
  modifying, and writing MIDI; this project makes no novelty claim for score editing.
  Its bounded workflow adds image-region traceability and regenerates a complete
  offline bundle from saved score data.

The existing version 1 score, MIDI/WAV exporters, browser template, import-related
limits, and tests were reviewed before designing version 2. The stored format
keeps the original mapping evidence and adds stable IDs, explicit mute state,
and immediate-parent provenance rather than inferring edits from MIDI files.
