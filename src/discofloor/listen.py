"""Listen to performance MIDI and match it to pad configuration when available."""

import argparse
import time
from pathlib import Path

from .commands import active_preset, read
from .model import Preset
from .ports import MidiPortUnavailable
from .protocol import pad_offset, preset_offset
from .transport import Session

USB_INPUT = "SINCO SMC-PAD Pocket-Master"
USB_CONFIG = "SINCO SMC-PAD Pocket-Private"


def choose_input(names, explicit=None):
    candidates = (
        (explicit,) if explicit is not None else (USB_INPUT, "SMC-PAD Pocket Bluetooth", USB_CONFIG)
    )
    for name in candidates:
        if name in names:
            return name
    raise MidiPortUnavailable(
        "MIDI input unavailable. Found MIDI inputs:\n"
        + "\n".join(f"  {name}" for name in names or ["(none)"])
    )


def matches(message, records, controls):
    """All candidates; incoming MIDI carries no physical pad identifier."""
    found = []
    for index in range(16):
        if controls[index]:
            continue
        record = records[index * 26 : (index + 1) * 26]
        kind, channel, number, value1, value2 = record[:5]
        custom = (
            kind == 4 and record[9] <= 16 and bytes(message.bytes()) == record[10 : 10 + record[9]]
        )
        if custom:
            found.append(index + 1)
        elif getattr(message, "channel", None) == channel:
            if kind == 0 and message.type in ("note_on", "note_off") and message.note == number:
                found.append(index + 1)
            elif kind in (1, 2) and message.type == "control_change" and message.control == number:
                if message.value in (value1, value2):
                    found.append(index + 1)
            elif kind == 3 and message.type == "program_change" and message.program == value2:
                found.append(index + 1)
    return found


def describe_event(message, records=None, controls=None):
    pads = matches(message, records, controls) if records is not None else []
    label = "Pad " + ", ".join(map(str, pads)) if pads else "Pad unknown"
    if len(pads) > 1:
        label += " (ambiguous)"
    if message.type in ("note_on", "note_off"):
        action = "struck" if message.type == "note_on" and message.velocity > 0 else "released"
        return f"{label} {action}: channel {message.channel + 1}, note {message.note}, velocity {message.velocity}"
    if message.type == "control_change":
        return f"{label}: channel {message.channel + 1}, CC {message.control} = {message.value}"
    if message.type == "program_change":
        return (
            f"{label}: channel {message.channel + 1}, program {message.program} (bank not inferred)"
        )
    return f"{label}: {message}"


def main(argv=None):
    cli = argparse.ArgumentParser(prog="discofloor listen", description=__doc__)
    cli.add_argument("--port", help="Performance input; default: USB Master, then Pocket Bluetooth")
    cli.add_argument(
        "--preset-file",
        type=Path,
        help="Use an exported .spp to identify pads without configuration reads",
    )
    cli.add_argument(
        "--bank",
        type=int,
        choices=range(1, 8),
        help="Bank in --preset-file; defaults to its selected bank",
    )
    args = cli.parse_args(argv)
    if args.bank is not None and args.preset_file is None:
        cli.error("--bank requires --preset-file")
    try:
        import mido

        backend = mido.Backend("mido.backends.rtmidi")
        name = choose_input(backend.get_input_names(), args.port)
        records = controls = None
        mapping = (
            "No pad mapping: showing raw messages; Bluetooth configuration access is unverified."
        )
        if args.preset_file:
            preset = Preset.load(args.preset_file)
            bank = args.bank or preset.active_bank
            data = preset.to_bytes()
            records = data[(bank - 1) * 416 : bank * 416]
            controls = data[2912:2928]
            mapping = (
                f"Mapping from {args.preset_file}, bank {bank}; must match hardware configuration."
            )
        elif (
            name == USB_INPUT
            and USB_CONFIG in backend.get_input_names()
            and USB_CONFIG in backend.get_output_names()
        ):
            try:
                with Session(USB_CONFIG, backend) as session:
                    slot = active_preset(session)
                    settings = read(session, 5, preset_offset(slot) + 2912, 19)
                    bank = settings[16] + 1
                    records = read(session, 5, pad_offset(slot, bank, 1), 416)
                    controls = settings[:16]
                mapping = f"Mapping preset {slot}, bank {bank}. Restart listen after changing selections/configuration."
            except (ValueError, RuntimeError, TimeoutError, OSError) as error:
                mapping = f"Could not read pad mapping ({error}); showing raw messages."
        print(f"Listening on {name}. Ctrl-C to stop.\n{mapping}", flush=True)
        with backend.open_input(name) as incoming:
            while True:
                for message in incoming.iter_pending():
                    if message.type not in ("clock", "active_sensing"):
                        print(describe_event(message, records, controls), flush=True)
                time.sleep(0.005)
    except KeyboardInterrupt:
        print("\nStopped listening.")
    except (ValueError, RuntimeError, TimeoutError, OSError) as error:
        cli.exit(1, f"{error}\n")
