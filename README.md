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

Close Midi Suite before transmitting. `Pocket()` defaults to
`SINCO SMC-PAD Pocket-Private`; pass another exact port name if necessary.
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

## Small live commands

Close Midi Suite first. These commands use the default Private port; override it
with `--port "exact port name"`. Read commands print a one-based number.

```sh
uv run discofloor bank               # Read the active preset's selected bank.
uv run discofloor bank 3             # Select bank 3 in the active preset; read it back.
uv run discofloor preset             # Read the active preset.
uv run discofloor preset 2           # Select preset 2; read it back.
uv run discofloor color 5 ff0044      # Set pad 5 in the active preset/bank.
uv run discofloor fill 1020ff         # Set all sixteen pads using one bank write.
uv run discofloor color 5 ff0044 --preset 2 --bank 3
uv run discofloor save               # Explicitly persist preset changes across slots.
```

`color` and `fill` resolve unspecified addresses from the device. Explicit
`--preset`/`--bank` addresses do not switch the visible selection. No command
implicitly saves; only `save` sends the persistent commit. For `#`-prefixed
colors, quote the argument to protect it from shell comment syntax.

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
