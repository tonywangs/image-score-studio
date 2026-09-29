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
