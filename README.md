# DiscoFloor

A Python library for configuring the **M-Vave SMC PAD Pocket** and changing its
pad colors live. Includes a disco-floor example that changes alternate halves
of the pads at 110 BPM and restores their original settings when it exits.

Developed against Pocket firmware v4 and Midi Suite 1.15.0 using captured SysEx.
See [the protocol reference](docs/PROTOCOL.md) for mappings, validation, and unknowns.
The larger SMC PAD model has not been tested.

## Install

Requires Python 3.14 or newer. From this checkout:

```sh
uv sync --extra midi
uv run discofloor protocol ports
```

To install the library into another environment from a local checkout:

```sh
uv pip install '/path/to/discofloor[midi]'
```

The core models and encoders have no runtime dependencies. Local `uv run`/`uv sync`
include the MIDI development group by default, so `uv run discofloor demo` works directly. The `midi` extra adds
Mido and python-rtmidi for device access. No PyPI release is assumed.

## Live colors

Close Midi Suite before transmitting. `Pocket()` tries `SINCO SMC-PAD Pocket-Private` first, then
`SMC-PAD Pocket Bluetooth`. A candidate must exist as both an input and output.
An explicit port name disables fallback.
Numbers for presets, banks, pads, and MIDI channels are one based.

```python
from discofloor import Color, Pocket

device = Pocket()
device.pad[1, 3, 5].color = Color.from_hex("#ff0044")

with device.preset[1].bank[3].edit() as bank:
    bank.fill(Color(0, 0, 255))
    bank.pad[1].color = Color(255, 0, 0)
    bank.pad[1].note(60, channel=10)
```

Direct assignment sends immediately. The `edit()` context reads the bank, stages
changes locally, and sends one 416-byte bank write on successful exit. An exception
or unchanged block sends no write. Unknown fields are preserved. One packet
reduces the visible sweep; simultaneous firmware LED refresh is not guaranteed.
The addressed preset/bank must be active to see its colors.

```python
snapshot = device.preset[1].bank[3].snapshot()
try:
    # Temporary animation or configuration changes.
    device.pad[1, 3, 1].color = Color(0, 255, 0)
finally:
    snapshot.restore()
```

Snapshots include the bank's pad records and original preset/bank selection.
Restoration requires the device to remain connected. An abrupt process kill or
power loss cannot execute cleanup.

## Preset files

```python
from discofloor import Color, Control, Pocket, Preset

preset = Preset.load("my-preset.spp")
preset.bank[3].pad[1].color = Color.from_hex("#ff8000")
preset.bank[3].pad[1].note(36, channel=10)
preset.bank[3].pad[2].cc(74, momentary=True)
preset.bank[3].pad[3].program(12, bank_msb=0, bank_lsb=1)
preset.bank[3].pad[4].custom(bytes.fromhex("f0 7d 11 22 f7"))
preset.assign_control(16, Control.NOTE_REPEAT)
preset.active_bank = 3
preset.export("edited.spp")

packets = preset.messages(slot=2)  # Generate changed-byte writes; sends nothing.
device = Pocket()
device.upload(preset, slot=2)  # Full preset upload; applies immediately.
device.select_preset(2)
device.save()  # Optional explicit persistence through power-off.
```

Local model assignments only edit memory. `apply_changes(preset, slot=2)` sends
changes relative to the originally loaded file; use it only when the device
shares that baseline. `upload` sends the complete preset. Neither automatically
saves. Callable access such as `preset.bank(3).pad(1)` is also supported.

Preset Save commits across slots. In testing it did not persist runtime tempo,
swing, repeat rate, sync, or latch. `calibrate(pad=1, level=4)` sends the app's
separate calibration commit and **does** persist immediately. Runtime byte 6 and
four calibration values remain unexplained and are preserved by the models.

## Python API organization

The app-style sections currently organize the **CLI**. The Python API retains
its existing objects and methods; it does not yet provide `device.note_repeat`
or `device.globe` namespaces.

| CLI section | Current Python API |
| --- | --- |
| Pad | `Preset.bank[bank].pad[pad]`, `Pocket.pad[preset, bank, pad]`, bank `edit()`; `Preset.assign_control()` for Control mode |
| Note Repeat | `Runtime` model (`tempo`, `swing`, `repeat_rate`, `sync`, `latch`); `Runtime.message()` generates a packet; `Pocket.set_repeat_rate()` sends a rate change |
| Globe | `Preset.active_bank`, `Preset.velocity_curve`, `Preset.aftertouch`; `Pocket.calibrate()` for physical-pad calibration |
| Preset | `Preset.load()` / `export()`, `Pocket.upload()` / `apply_changes()` / `select_preset()` / `save()` |

`Runtime.from_bytes()` parses a previously read eight-byte runtime block.
`Runtime.message()` only constructs a packet; `Pocket.send()` transmits it.
Preserve the existing runtime block when changing fields so active preset and
unknown bytes are retained. Similarly, preset property assignments are local
until explicitly uploaded or applied. The live color setter and bank `edit()`
context transmit as described above.

Reset reconstruction is currently used by the CLI through the internal
`discofloor.defaults` module, rather than a public `Pocket.reset()` method.

## Disco floor

```sh
uv run discofloor demo
# Preview without opening any MIDI ports:
uv run discofloor demo --dry-run --frames 4
```

At 110 BPM, odd-numbered pads change on beats 1/3 and even-numbered pads on beats
2/4. Each beat uses `with bank.edit()`. Defaults are preset 1, bank 3; change them
with `--preset` and `--bank`. Stop with Ctrl-C. The example snapshots before
changing anything and restores on normal completion, Ctrl-C, or an exception.
It never sends persistent Save. Restoration failures are reported.
Demo options: `--bpm` (default 110), `--preset` (default 1), `--bank` (default 3),
`--port`, `--seed` for reproducible colors, `--frames` for a finite run, and
`--dry-run` for a preview without MIDI access. Demo `--bpm` controls animation
speed; it does not change the device's Note Repeat tempo.

## Small live commands

Close Midi Suite first. Commands try USB Private, then Pocket Bluetooth. Override
with `--port "exact name"` before or after the subcommand. Bluetooth configuration
access has not been verified; listening over Bluetooth does not need SysEx.

The settings commands follow Midi Suite's sections: `pad`, `note-repeat`,
`globe`, and `preset`. Other commands are `demo`, `show`, `listen`, and the
low-level `protocol` tools. Boolean settings use positive/negative flags;
omitting both forms leaves that setting unchanged.

```sh
uv run discofloor pad 1-4,7,9-12                  # Inspect selected pads.
uv run discofloor pad 1-4,7 --color ff0044
uv run discofloor pad 1-16 --color 1020ff --brightness 200
uv run discofloor pad 5 --note 60 --channel 10 --min-velocity 10 --max-velocity 127
uv run discofloor pad 5 --cc 74 --on 127 --off 0   # Toggle.
uv run discofloor pad 5 --cc 74 --momentary --on 127 --off 0
uv run discofloor pad 6 --program 7 --bank-msb 2 --bank-lsb 3
uv run discofloor pad 7 --custom 'f0 7d 01 f7'
uv run discofloor pad 9 --mode control --control note-repeat
uv run discofloor pad 10 --control latch          # Implies Control mode.
uv run discofloor pad 9-10 --mode pad              # Restore normal pad mode.
uv run discofloor note-repeat                    # Inspect tempo/time/swing/sync/latch.
uv run discofloor note-repeat --tempo 110 --swing 50 --time 1/16
uv run discofloor note-repeat --sync --no-latch
uv run discofloor globe                          # Inspect curve/bank/aftertouch.
uv run discofloor globe --bank 3 --curve 4 --no-aftertouch
uv run discofloor globe --preset 2 --aftertouch   # Address slot 2 without selecting it.
uv run discofloor globe --calibration 3 --pads 1-4 # Immediately persists.
uv run discofloor globe --snapshot bank.json     # Read-only bank backup.
uv run discofloor globe --restore bank.json
uv run discofloor preset                         # Read active preset.
uv run discofloor preset 2                       # Select preset 2.
uv run discofloor preset --save                   # Persist changes across ALL slots.
uv run discofloor preset --export backup.spp
uv run discofloor preset 2 --import backup.spp    # Select slot 2 and upload; no Save.
uv run discofloor preset --reset                  # Reset only active preset; no Save.
uv run discofloor preset --reset-all              # App's full reset; no Save.
uv run discofloor show                           # Show active-bank pad messages.
```

### Settings option reference

All commands accept `--help`. `--port` can be supplied before or after the
subcommand and overrides automatic port discovery.

| Command | Options |
| --- | --- |
| `pad RANGE` | `--color`, `--brightness`, `--channel`, `--note`, `--cc`, `--momentary`, `--on`, `--off`, `--program`, `--bank-msb`, `--bank-lsb`, `--custom`, `--min-velocity`, `--max-velocity`, `--mode`, `--control`, `--preset`, `--bank` |
| `note-repeat` | `--tempo`, `--time`, `--swing`, `--sync` / `--no-sync`, `--latch` / `--no-latch` |
| `globe` | `--preset`, `--bank`, `--curve`, `--aftertouch` / `--no-aftertouch`, `--calibration` with `--pads`, `--snapshot`, `--restore` |
| `preset [NUMBER]` | One of `--save`, `--export`, `--import`, `--reset`, `--reset-all` |
| `show` | No settings flags; reads the active preset/bank |
| `listen` | `--preset-file`, `--bank` (requires preset file), `--port` |

### Pad

`pad RANGE` edits selected bank records. Color, message, channel, brightness,
and velocity changes are staged into one bank write. `--preset` and `--bank`
address records without changing the visible selection. Ranges are inclusive,
ascending within each range, and within 1–16; duplicates are removed. Quote
`#`-prefixed color arguments to protect them from shell comment syntax.

Message types (`--note`, `--cc`, `--program`, `--custom`) are mutually exclusive.
CC flags require `--cc`; program bank flags require `--program`. CC defaults
are on=127, off=0; program bank MSB/LSB default to 0. Type changes preserve the
channel unless `--channel` is provided. `--note` preserves velocity bounds;
all selected pads receive that same note. Velocity bounds can also edit existing
Note pads; invalid bounds abort the entire bank edit. Custom payloads contain
1–16 bytes; configuring them stores the payload rather than transmitting it
as a standalone message.

`--mode control --control FUNCTION` assigns a physical pad's function across
all seven banks in the addressed preset. `--control` alone implies Control mode;
`--mode pad` removes the control assignment. Available functions are
`note-repeat`, `rate-up`, `rate-down`, `swing-up`, `swing-down`, `bank-up`,
`bank-down`, and `latch`. These assignments override the underlying pad message.
Combining them with bank edits sends separate writes, not an atomic transaction.

### Note Repeat

These settings are independent of presets and volatile, even after
`preset --save`. Edits preserve active preset and the unknown runtime byte.
`--time` accepts `1/4`, `1/4T`, `1/8`, `1/8T`, `1/16`, `1/16T`, `1/32`, `1/32T`;
T means triplet. Swing is 0–100% according to the manual. Tempo accepts the
positive 16-bit storage range; firmware limits are unmeasured. Use `--sync` /
`--no-sync` and `--latch` / `--no-latch`. These configure Note Repeat; there is
no verified CLI command to activate it directly. Assign and use a physical
Note Repeat control pad.

### Globe

The app calls this section Globe, but curve, selected bank, and aftertouch
are stored per preset. They affect all pads in that preset. `--preset` addresses
a slot without selecting it; default is the active preset. Use `--aftertouch`
or `--no-aftertouch`. The manual identifies curve 4 as full velocity; curves
1–3 have not been characterized.

Calibration is device-wide, applying to physical pads independent of presets
and banks. Use `--calibration LEVEL --pads RANGE` without preset/address/edit
flags. Level 1 is most sensitive; level 8 is least sensitive. Increasing the
level helps prevent unintended double triggers, as confirmed by the device
owner. The captured thresholds are 50–750 in steps of 100. Each pad's write
is followed by an immediate calibration commit; no separate Save is needed.
The four unknown calibration values remain untouched. The supplied manual
does not describe calibration.

Bank snapshots contain 416 pad bytes, the addressed preset's selected bank,
and active preset selection. `--snapshot` defaults to the selected bank;
`--bank` addresses another bank for a snapshot without selecting it.
`--restore` uses the file's address and saved selections. Neither implicitly
saves to flash. Snapshots exclude controls, aftertouch, curve, calibration,
and other Note Repeat settings.

### Preset

An optional preset number selects that preset before the requested action.
Preset files are complete 2931-byte `.spp` records. Save commits across all
four slots and does not persist Note Repeat settings. Import and reset send
no implicit Save. `--reset` restores only the active slot's captured factory
configuration, including its Globe settings and control assignments.
`--reset-all` reproduces the captured app reset: all four presets plus default
Note Repeat settings, selecting preset 1. Calibration is unaffected by either
reset. Choose one action per invocation.

The previous top-level `bank`, `runtime`, and `save` commands are now
`globe --bank`, `note-repeat`, and `preset --save`. Missing ports print the
found input/output names.

## Listen to pad strikes

```sh
uv run discofloor listen
uv run discofloor listen --port "SMC-PAD Pocket Bluetooth"
uv run discofloor listen --preset-file my-preset.spp --bank 3
```

Listening prefers the USB **Master** performance input, then Bluetooth. When
USB Private configuration access is available, it reads the active bank once
and identifies matching pads. On Bluetooth it listens without sending SysEx;
use `--preset-file` for a matching mapping, otherwise it reports raw MIDI with
"Pad unknown". Note On velocity zero is treated as release. Duplicate assignments
list every candidate. Program messages cannot distinguish bank selections on
their own. Control-mode pads may change internal state without emitting MIDI.
Restart listening after changing the bank or preset. Ctrl-C stops; no configuration
writes or Save commands are sent.

## CLI and development

```sh
# Dry runs unless --send and --port are supplied:
uv run discofloor protocol color 1 255 0 0 --preset 1 --bank 3
uv run discofloor protocol config my-preset.spp --preset 2 --output upload.syx

uv sync
uv run python -m unittest discover -s tests -v
uv run ruff check .
uv run ruff format --check .
uv build
```

Tests run without hardware. They cover vendor captures, lossless configuration
editing, addressing, ACK handling, bank batching, and cleanup after interruption.
Compact fixtures live in `tests/fixtures`; complete research sessions and personal
backups are archived outside this repository. Generated vendor firmware/default
assets are not distributed. Python color writes were tested live; full Python
upload/Save operations have capture-level validation. App persistence operations
were verified across power cycles.

MIT licensed. Independent project; not affiliated with M-Vave.
