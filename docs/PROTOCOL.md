# SMC PAD Pocket SysEx reference

Observed with Midi Suite 1.15.0, hardware identity `SMC-PAD Pocket_004`.
Offsets below refer to decoded bytes, not positions in the MIDI wire packet.

## Envelope and encoding

Concatenate raw bytes `00 59 OP LENGTH_LO LENGTH_HI 00 BODY CHECKSUM`.
Length is the body size. Checksum is `(255 - sum(BODY)) & 255`.
Encode the entire raw packet as an LSB-first bit stream, dividing into seven-bit
values and zero-padding the final value. Add `F0` and `F7` delimiters.
The apparent prefix `00 32` is packed signature data, not a separate header.

Operations: `11` discovery, `22` write, `23` read request/reply, `00` ACK.
Discovery has an empty request body:
`F0 00 32 45 00 00 00 40 7F F7`.
Its reply body is a 27-byte, null-padded ASCII identity.
Success ACK raw bytes: `00 59 00 01 00 00 00 FF`;
wire: `F0 00 32 01 08 00 00 00 00 7F 01 F7`.
ACKs have no transaction identifier. Serialize writes and wait for each ACK.
Error codes have not been elicited.

For read/write operations, BODY is:

| Body offset | Width | Meaning |
| --- | --- | --- |
| 0 | 1 | Register |
| 1 | 4 | Offset, unsigned little endian |
| 5 | 3 | Byte count, unsigned little endian |
| 8 | variable | Data (absent in read requests) |

App writes use at most 1,024 data bytes; read replies use at most 1,009.
Read and write replies are distinguishable by direction and presence of data.
Use the device's Private MIDI input/output pair.

## Register 5: presets

11,724 bytes, comprising four 2,931-byte slots. Slot base is
`(preset - 1) * 2931`, with user-facing preset numbers 1–4.
Each slot starts with 112 pad records: seven banks of sixteen pads.
Record address is `slot_base + ((bank - 1) * 16 + pad - 1) * 26`.
Bank addressing was checked in all seven banks. Full app imports and readback
were checked in all four slots.

| Record offset | Width | Meaning |
| --- | --- | --- |
| 0 | 1 | Type: 0 Note, 1 CC Toggle, 2 Momentary, 3 Program, 4 Custom |
| 1 | 1 | MIDI channel, zero based (0–15) |
| 2 | 1 | Note number / CC number / Program LSB |
| 3 | 1 | Note minimum velocity / CC Value 1 / Momentary Up / Program MSB |
| 4 | 1 | Note maximum velocity / CC Value 2 / Momentary Down / Program number |
| 5 | 3 | Red, green, blue (each 0–255) |
| 8 | 1 | LED level, 0–255 |
| 9 | 1 | Custom message length |
| 10 | 16 | Custom message storage |

Custom changes write the length followed by the payload, starting at offset 9.
A 16-byte and a 5-byte payload were captured. Short writes need not clear the
remaining storage; interpret only the declared length. Selecting Custom is a
separate type write. The payload can include `F0`/`F7`; outer packing allows all
eight-bit values. Do not assume this is a streaming arbitrary-length MIDI port.

| Slot offset | Width | Meaning |
| --- | --- | --- |
| 2912 | 16 | One control assignment per physical pad, shared across banks |
| 2928 | 1 | Selected bank, zero based (0–6) |
| 2929 | 1 | Velocity curve, zero based (UI 1–4) |
| 2930 | 1 | Aftertouch enabled, Boolean |

Control assignments: 0 ordinary pad, 1 Note Repeat, 2 Rate Up, 3 Rate Down,
4 Swing Up, 5 Swing Down, 6 Bank Up, 7 Bank Down, 8 Latch.
These are separate from each bank's pad message type. All assignment values,
curves, and bank indices were captured.

Import writes 1,024, 1,024, and 883 bytes at slot-base offsets 0, 1024, and 2048.
An `.spp` export is exactly one raw slot, without the SysEx envelope.
RGB writes update configuration immediately; they do not require Save to appear.

## Save / persistence

Midi Suite Save writes register 5, offset 0, count 0, with no data:

`F0 00 32 09 41 00 00 40 02 00 00 00 00 00 00 00 00 7A 01 F7`

The same message was captured with each of the four presets selected. There is
no preset selector in this command. The user confirmed Save persists through
power cycles; this investigation did not perform an additional power cycle.
A subsequent power-cycle test changed pad LED levels in presets 1 and 2,
then saved only while preset 2 was selected. Both changes survived. Save therefore
commits changes across preset slots; it is not limited to the selected preset.
All-region atomicity has not been measured. Keep Save separate from temporary color writes.

## Register 4: runtime settings

| Offset | Width | Meaning |
| --- | --- | --- |
| 0 | 2 | Tempo, unsigned little endian |
| 2 | 1 | Swing |
| 3 | 1 | Repeat rate enumeration |
| 4 | 1 | Sync enabled, Boolean |
| 5 | 1 | Latch enabled, Boolean |
| 6 | 1 | Unexposed byte; observed 0, meaning unknown |
| 7 | 1 | Active preset, zero based (0–3) |

Repeat rates: 0 `1/4`, 1 `1/4T`, 2 `1/8`, 3 `1/8T`, 4 `1/16`,
5 `1/16T`, 6 `1/32`, 7 `1/32T`. All eight were captured.
Tempo 160→161 and swing 51→52 changes establish representation, not firmware
minimum/maximum values. Python's numeric bounds for these fields are storage
bounds; arbitrary extreme values have not been validated. A subsequent test changed tempo, swing, repeat rate, sync, and latch, then
clicked preset Save. After power-off/on, all five returned to their baseline
values. Preset Save did not persist these runtime changes. The active preset
also returned from preset 2 to preset 1.

## Register 3: pad calibration

160 bytes: sixteen 10-byte records, each five unsigned little-endian 16-bit
values. The device owner confirmed that level 1 is most sensitive and level 8
is least sensitive; increasing the level helps prevent unintended double triggers.
The app's pad calibration level (1–8) changes the first value to
`50 + (level - 1) * 100`, at `(pad - 1) * 10`. All eight levels were captured,
as were pad 1 and pad 16 addresses. Each change is followed by a zero-length
write to register 3, offset 0, which is the app's calibration commit command.
The other four values were 550, 720, 850, 950 in the original records;
their individual meanings are unknown and they should be preserved.
A power-cycle test confirmed that pad 1 level 5 (threshold 450) survives
the automatic register-3 commit, without clicking preset Save. Level 4 was
restored afterwards; all 160 calibration bytes matched the backup.

## Reset and boundaries of the evidence

Factory reset was subsequently captured in `factory-reset-capture.json`.
There is no special reset opcode: the app writes its bundled `SPPB.bin` (11,724
bytes) to register 5 at offsets 0, 1024, …, 11264. The first eleven chunks are
1,024 bytes; the last is 460. All twelve match the packets predicted from the
bundled asset before clicking Reset. It then writes register 4, offset 0,
count 8, data `78 00 32 04 00 00 00 00`: tempo 120, swing 50, repeat 1/16,
sync off, latch off, unknown byte zero, preset 1. No register-3 writes or
zero-length Save were observed in the reset sequence. The app instructs the
user to click Save for persistence; reset persistence itself was not tested.
All four presets and the original runtime block were restored after capture.
No bootloader or firmware-update protocol is claimed here.
Curve response shapes, aftertouch message behavior, and custom
payload emission when a pad is pressed are distinct from configuration encoding
and were not measured.

## Evidence and restoration

`spec-field-capture.json` contains the field experiments.
`spec-restoration-capture.json` retains the complete session and all four final
app imports. The twelve import packets match the Python encoder exactly.
The final readback of all 11,724 preset bytes matches `spec-start-device.bin`.
Original calibration levels were restored through the app; calibration contents
were not independently read back after restoration. Runtime readback was
`a0 00 33 01 00 01 00 00` (tempo 160, swing 51, rate 1/4T,
sync off, latch on, preset 1). All four presets were saved through Midi Suite.

The earlier Python live color test received sixteen ACKs and the user's photo
confirmed every physical pad position. Python full uploads and Save have been
validated against vendor captures, but have not themselves been sent live.

## Manual cross-check

The supplied SMC-PAD Pocket user manual, English pages 4–6, confirms swing
as 0–100%, the eight note-repeat subdivisions, control assignment functions,
and that velocity curve 4 produces full velocity. It does not document the
calibration levels or the four other values in each calibration record.
The four other values remain unexplained. The device owner confirmed the
level direction: 1 is most sensitive, 8 is least sensitive, with higher levels
used to prevent unintended double triggers.
Calibration persistence and its threshold mapping come from the captured app
traffic and power-cycle tests, not the manual. CLI calibration writes only the
known threshold and uses the captured register-3 commit sequence.

## CLI organization

CLI sections follow Midi Suite: Pad, Note Repeat, Globe, Preset. The Globe
label does not change storage scope: curve, bank and aftertouch remain per
preset; calibration remains device-wide. Pad control-mode assignments are
per physical pad across all banks of the preset. Note Repeat edits preserve
unknown runtime bytes and remain independent of presets.

`preset --reset-all` reproduces all 13 captured reset writes exactly. The
factory configuration is reconstructed from its known fields in
`defaults.py` and checked against the capture. `preset --reset` uses the same
default bytes for only the active slot, via validated preset-upload chunks;
it preserves other slots and runtime settings. Neither reset implicitly Saves.
