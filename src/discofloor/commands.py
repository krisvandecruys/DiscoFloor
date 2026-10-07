"""Live pad, preset, bank, and persistence commands."""

import argparse
import json
import sys
from dataclasses import replace
from pathlib import Path

from . import Color, PadType, Pocket, Preset, RepeatRate, Runtime
from .device import BankSnapshot
from .ports import PORT_HELP
from .protocol import (
    CONTROL_FUNCTIONS,
    REPEAT_RATES,
    build_read,
    control_message,
    pad_offset,
    preset_offset,
    preset_setting_message,
)
from .selections import pad_range
from .transport import Session


def parser(description, command):
    result = argparse.ArgumentParser(prog=f"discofloor {command}", description=description)
    result.add_argument("--port", help=PORT_HELP)
    return result


def read(session, register, offset, count):
    return session.request(
        build_read(register, offset, count),
        read_offset=offset,
        read_register=register,
        read_count=count,
    )


def active_preset(session):
    number = read(session, 4, 0, 8)[7] + 1
    if not 1 <= number <= 4:
        raise ValueError("Device returned an invalid preset index")
    return number


def active_bank(session, preset):
    number = read(session, 5, preset_offset(preset) + 2928, 1)[0] + 1
    if not 1 <= number <= 7:
        raise ValueError("Device returned an invalid bank index")
    return number


def globe(argv=None):
    cli = parser("Globe: preset-wide curve, bank, aftertouch; device-wide calibration.", "globe")
    cli.add_argument(
        "--preset",
        type=int,
        choices=range(1, 5),
        help="Address a preset without selecting it; default: active",
    )
    cli.add_argument(
        "--bank", type=int, choices=range(1, 8), help="Select bank within the addressed preset"
    )
    cli.add_argument(
        "--curve", type=int, choices=range(1, 5), help="Velocity curve; 4 = full velocity"
    )
    cli.add_argument(
        "--aftertouch",
        action=argparse.BooleanOptionalAction,
        default=None,
        help="Pressure sensitivity for all pads/banks in the addressed preset",
    )
    operations = cli.add_mutually_exclusive_group()
    operations.add_argument(
        "--calibration",
        type=int,
        choices=range(1, 9),
        help="Physical-pad sensitivity: 1 most sensitive, 8 least; increase to prevent double triggers. Persists immediately",
    )
    operations.add_argument(
        "--snapshot", type=Path, help="Snapshot selected bank and preset/bank selection to JSON"
    )
    operations.add_argument(
        "--restore", type=Path, help="Restore bank snapshot and saved selections"
    )
    cli.add_argument(
        "--pads",
        type=pad_range,
        metavar="RANGE",
        help="Physical pads for --calibration; required for calibration",
    )
    args = cli.parse_args(argv)
    if (args.pads is not None) != (args.calibration is not None):
        cli.error("Use --calibration LEVEL together with --pads RANGE")
    if (args.calibration is not None or args.restore) and any(
        v is not None for v in (args.preset, args.bank, args.curve, args.aftertouch)
    ):
        cli.error("Calibration/restore cannot be combined with preset address or setting flags")
    if args.snapshot and (args.curve is not None or args.aftertouch is not None):
        cli.error("Snapshot cannot be combined with setting edits")
    try:
        device = Pocket(args.port)
        if args.calibration is not None:
            for number in args.pads:
                device.calibrate(number, level=args.calibration)
            print(
                f"Physical pads {','.join(map(str, args.pads))}: calibration level {args.calibration}, persisted immediately."
            )
            return
        if args.restore:
            saved = json.loads(args.restore.read_text())
            if not isinstance(saved, dict) or saved.get("format") != "discofloor-bank-v1":
                raise ValueError("Not a DiscoFloor bank snapshot")
            if type(saved["preset"]) is not int or type(saved["bank"]) is not int:
                raise ValueError("Snapshot addresses must be integers")
            pad_offset(saved["preset"], saved["bank"], 1)
            data, selected, active = (
                bytes.fromhex(saved[key]) for key in ("data", "selected_bank", "active_preset")
            )
            if (
                len(data) != 416
                or len(selected) != 1
                or selected[0] > 6
                or len(active) != 1
                or active[0] > 3
            ):
                raise ValueError("Invalid bank snapshot bytes")
            BankSnapshot(device, saved["preset"], saved["bank"], data, selected, active).restore()
            print("Bank and preset/bank selection restored. No Save sent.")
            return
        with Session(args.port) as session:
            number = args.preset if args.preset is not None else active_preset(session)
            if args.snapshot:
                bank = args.bank if args.bank is not None else active_bank(session, number)
            else:
                settings = read(session, 5, preset_offset(number) + 2928, 3)
        if args.snapshot:
            snapshot = device.preset[number].bank[bank].snapshot()
            args.snapshot.write_text(
                json.dumps(
                    {
                        "format": "discofloor-bank-v1",
                        "preset": number,
                        "bank": bank,
                        "data": snapshot.data.hex(),
                        "selected_bank": snapshot.selected_bank.hex(),
                        "active_preset": snapshot.active_preset.hex(),
                    },
                    indent=2,
                )
                + "\n"
            )
            print(f"Bank snapshot exported to {args.snapshot}. No writes sent.")
            return
        messages = []
        for field, value in (
            ("bank", args.bank),
            ("curve", args.curve),
            ("aftertouch", None if args.aftertouch is None else int(args.aftertouch)),
        ):
            if value is not None:
                messages.append(preset_setting_message(number, field, value))
        if messages:
            device.send(messages)
            print(
                f"Globe settings updated for preset {number}, across all pads/banks. No Save sent."
            )
        else:
            print(
                f"Preset {number}; bank {settings[0] + 1}; curve {settings[1] + 1}; aftertouch {'on' if settings[2] else 'off'}"
            )
    except (
        ValueError,
        KeyError,
        TypeError,
        RuntimeError,
        TimeoutError,
        OSError,
        ExceptionGroup,
    ) as error:
        cli.exit(1, f"{error}\n")


def preset(argv=None):
    cli = parser(
        "Select, save, export, import or reset presets. Save commits across all slots.", "preset"
    )
    cli.add_argument(
        "number",
        nargs="?",
        type=int,
        choices=range(1, 5),
        help="Select this preset; default: active",
    )
    actions = cli.add_mutually_exclusive_group()
    actions.add_argument(
        "--save", action="store_true", help="Persist preset configuration across all four slots"
    )
    actions.add_argument("--export", type=Path, help="Export complete active preset as .spp")
    actions.add_argument(
        "--import", dest="import_file", type=Path, help="Upload .spp to active preset; no Save"
    )
    actions.add_argument(
        "--reset",
        action="store_true",
        help="Restore active preset's factory configuration; no Save",
    )
    actions.add_argument(
        "--reset-all",
        action="store_true",
        help="App-style reset of all presets and Note Repeat settings; no Save",
    )
    args = cli.parse_args(argv)
    if args.reset_all and args.number is not None:
        cli.error("--reset-all selects preset 1; omit the preset number")
    try:
        incoming = Preset.load(args.import_file) if args.import_file else None
        device = Pocket(args.port)
        if args.number is not None:
            device.select_preset(args.number)
        if args.save:
            device.save()
            print(
                "Preset configuration saved across ALL slots; Note Repeat settings are not persisted."
            )
            return
        if args.reset_all:
            from .defaults import reset_all_messages

            device.send(reset_all_messages())
            print("All presets and Note Repeat settings reset; preset 1 selected. No Save sent.")
            return
        with Session(args.port) as session:
            number = active_preset(session)
            if args.export:
                base = preset_offset(number)
                data = b"".join(
                    read(session, 5, base + offset, min(1009, 2931 - offset))
                    for offset in range(0, 2931, 1009)
                )
        if args.export:
            Preset(data).export(args.export)
            print(f"Preset {number} exported to {args.export}.")
        elif incoming is not None:
            device.upload(incoming, slot=number)
            print(f"Preset {number} uploaded. No Save sent.")
        elif args.reset:
            from .defaults import factory_preset

            device.upload(factory_preset(number), slot=number)
            print(
                f"Preset {number} reset. Other presets and Note Repeat settings preserved. No Save sent."
            )
        else:
            print(number)
    except (ValueError, RuntimeError, TimeoutError, OSError) as error:
        cli.exit(1, f"{error}\n")


def tempo_value(value):
    number = int(value)
    if not 1 <= number <= 65535:
        raise argparse.ArgumentTypeError("Tempo must be 1–65535 BPM")
    return number


def note_repeat(argv=None):
    cli = parser(
        "Note Repeat: tempo, time, swing, sync, latch. Independent of presets; volatile.",
        "note-repeat",
    )
    cli.add_argument(
        "--tempo",
        type=tempo_value,
        metavar="BPM",
        help="Positive 16-bit BPM; firmware limits unmeasured",
    )
    cli.add_argument(
        "--swing", type=int, choices=range(101), metavar="PERCENT", help="Swing 0–100%%"
    )
    cli.add_argument(
        "--time", choices=REPEAT_RATES, help="Note-repeat subdivision; T means triplet"
    )
    cli.add_argument(
        "--sync",
        action=argparse.BooleanOptionalAction,
        default=None,
        help="External MIDI clock synchronization",
    )
    cli.add_argument(
        "--latch",
        action=argparse.BooleanOptionalAction,
        default=None,
        help="Continue repeating after release",
    )
    args = cli.parse_args(argv)
    try:
        with Session(args.port) as session:
            current = Runtime.from_bytes(read(session, 4, 0, 8))
            changes = {
                name: getattr(args, name)
                for name in ("tempo", "swing")
                if getattr(args, name) is not None
            }
            if args.time is not None:
                changes["repeat_rate"] = RepeatRate(REPEAT_RATES[args.time])
            for name in ("sync", "latch"):
                if getattr(args, name) is not None:
                    changes[name] = getattr(args, name)
            updated = replace(current, **changes)
            if updated != current:
                session.request(updated.message())
        rate = next(k for k, v in REPEAT_RATES.items() if v == updated.repeat_rate)
        print(
            f"Tempo {updated.tempo} BPM; swing {updated.swing}%; rate {rate}; sync {'on' if updated.sync else 'off'}; latch {'on' if updated.latch else 'off'}"
        )
        if changes:
            print("Runtime settings are volatile. No Save sent.")
    except (ValueError, RuntimeError, TimeoutError, OSError) as error:
        cli.exit(1, f"{error}\n")


def describe_pad(record, control):
    if control:
        names = {value: name.replace("_", " ").title() for name, value in CONTROL_FUNCTIONS.items()}
        return f"Control: {names.get(control, f'unknown {control}')} (pad message inactive)"
    kind, channel, number, value1, value2 = record[:5]
    channel += 1
    prefix = f"Channel {channel:2}: "
    if kind == 0:
        return prefix + f"Note {number}; velocity {value1}–{value2}; Note Off on release"
    if kind == 1:
        return prefix + f"CC {number}; toggle values {value1} / {value2}"
    if kind == 2:
        return prefix + f"CC {number}; press {value2}, release {value1}"
    if kind == 3:
        return prefix + f"Program {value2}; bank MSB {value1}, LSB {number}"
    if kind == 4:
        length = record[9]
        if length > 16:
            return prefix + f"Custom: invalid payload length {length}"
        return prefix + "Custom: " + (record[10 : 10 + length].hex(" ").upper() or "(empty)")
    return prefix + f"Unknown pad type {kind}"


def show(argv=None):
    cli = parser("Show the active preset/bank's configured pad messages. Reads only.", "show")
    args = cli.parse_args(argv)
    try:
        with Session(args.port) as session:
            preset = active_preset(session)
            settings = read(session, 5, preset_offset(preset) + 2912, 19)
            bank = settings[16] + 1
            records = read(session, 5, pad_offset(preset, bank, 1), 416)
        print(f"Preset {preset}, bank {bank} — configured pad messages")
        for index in range(16):
            record = records[index * 26 : (index + 1) * 26]
            print(f"Pad {index + 1:2}: {describe_pad(record, settings[index])}")
        print("Values describe configuration, not a live capture of pad output.")
    except (ValueError, RuntimeError, TimeoutError, OSError) as error:
        cli.exit(1, f"{error}\n")


def pad(argv=None):
    cli = parser(
        "Pad: MIDI messages, mode, channel, LED and color. No Save sent.",
        "pad",
    )
    cli.add_argument(
        "pads", type=pad_range, metavar="RANGE", help="e.g. 1-4,7,9-12; 1-16 for all pads"
    )
    cli.add_argument("--color", help="RGB hex, e.g. ff0044 or '#ff0044'")
    cli.add_argument(
        "--note", type=int, help="Note number 0–127; switches selected pads to Note type"
    )
    types = cli.add_mutually_exclusive_group()
    types.add_argument(
        "--cc", type=int, choices=range(128), metavar="NUMBER", help="CC number, default toggle"
    )
    types.add_argument(
        "--program", type=int, choices=range(128), metavar="NUMBER", help="Program change, 0–127"
    )
    types.add_argument("--custom", help="Custom MIDI bytes in hex, 1–16 bytes; quote spaces")
    cli.add_argument("--momentary", action="store_true", help="Use momentary CC, requires --cc")
    cli.add_argument(
        "--on",
        type=int,
        choices=range(128),
        metavar="VALUE",
        help="CC press/on value, requires --cc",
    )
    cli.add_argument(
        "--off",
        type=int,
        choices=range(128),
        metavar="VALUE",
        help="CC release/off value, requires --cc",
    )
    cli.add_argument(
        "--bank-msb",
        type=int,
        choices=range(128),
        metavar="VALUE",
        help="Program bank MSB, requires --program",
    )
    cli.add_argument(
        "--bank-lsb",
        type=int,
        choices=range(128),
        metavar="VALUE",
        help="Program bank LSB, requires --program",
    )
    cli.add_argument(
        "--min-velocity",
        type=int,
        choices=range(128),
        metavar="VALUE",
        help="Note minimum velocity",
    )
    cli.add_argument(
        "--max-velocity",
        type=int,
        choices=range(128),
        metavar="VALUE",
        help="Note maximum velocity",
    )
    cli.add_argument(
        "--mode",
        choices=("pad", "control"),
        help="Mode for these physical pads across all banks of the preset",
    )
    cli.add_argument(
        "--control",
        choices=[n.replace("_", "-") for n in CONTROL_FUNCTIONS if n != "pad"],
        help="Control function; implies --mode control; applies across all banks",
    )
    cli.add_argument("--channel", type=int, help="MIDI channel 1–16")
    cli.add_argument("--brightness", type=int, help="LED brightness 0–255")
    cli.add_argument("--preset", type=int, choices=range(1, 5), help="Default: active preset")
    cli.add_argument("--bank", type=int, choices=range(1, 8), help="Default: selected bank")
    args = cli.parse_args(argv)
    for field, minimum, maximum in (("note", 0, 127), ("channel", 1, 16), ("brightness", 0, 255)):
        value = getattr(args, field)
        if value is not None and not minimum <= value <= maximum:
            cli.error(f"--{field} must be {minimum}–{maximum}")
    try:
        color = Color.from_hex(args.color) if args.color is not None else None
    except ValueError as error:
        cli.error(str(error))
    if args.note is not None and any(v is not None for v in (args.cc, args.program, args.custom)):
        cli.error("Choose only one of --note, --cc, --program, --custom")
    if (args.momentary or args.on is not None or args.off is not None) and args.cc is None:
        cli.error("--momentary/--on/--off require --cc")
    if (args.bank_msb is not None or args.bank_lsb is not None) and args.program is None:
        cli.error("--bank-msb/--bank-lsb require --program")
    if any(v is not None for v in (args.min_velocity, args.max_velocity)) and any(
        v is not None for v in (args.cc, args.program, args.custom)
    ):
        cli.error("Velocity bounds only apply to Note type")
    if (
        args.min_velocity is not None
        and args.max_velocity is not None
        and args.min_velocity > args.max_velocity
    ):
        cli.error("Minimum velocity cannot exceed maximum")
    try:
        payload = bytes.fromhex(args.custom) if args.custom is not None else None
        if payload is not None and not 1 <= len(payload) <= 16:
            raise ValueError("Custom payload must contain 1–16 bytes")
    except ValueError as error:
        cli.error(str(error))
    editing_pads = any(
        value is not None
        for value in (
            color,
            args.note,
            args.channel,
            args.brightness,
            args.cc,
            args.program,
            payload,
            args.min_velocity,
            args.max_velocity,
        )
    )
    if args.mode == "pad" and args.control is not None:
        cli.error("--control conflicts with --mode pad")
    if args.mode == "control" and args.control is None:
        cli.error("--mode control requires --control FUNCTION")
    control = "pad" if args.mode == "pad" else args.control
    try:
        device = Pocket(args.port)
        preset, bank = args.preset, args.bank
        if preset is None or (bank is None and (editing_pads or control is None)):
            with Session(args.port) as session:
                if preset is None:
                    preset = active_preset(session)
                if bank is None and (editing_pads or control is None):
                    bank = active_bank(session, preset)
        if editing_pads:
            with device.preset[preset].bank[bank].edit() as edited:
                for number in args.pads:
                    selected = edited.pad[number]
                    if color is not None:
                        selected.color = color
                    if args.note is not None:
                        selected.type = PadType.NOTE
                        selected.number = args.note
                    if args.cc is not None:
                        selected.cc(
                            args.cc,
                            channel=selected.channel,
                            on=127 if args.on is None else args.on,
                            off=0 if args.off is None else args.off,
                            momentary=args.momentary,
                        )
                    if args.program is not None:
                        selected.program(
                            args.program,
                            channel=selected.channel,
                            bank_msb=0 if args.bank_msb is None else args.bank_msb,
                            bank_lsb=0 if args.bank_lsb is None else args.bank_lsb,
                        )
                    if payload is not None:
                        selected.custom(payload, channel=selected.channel)
                    if args.min_velocity is not None or args.max_velocity is not None:
                        if selected.type != PadType.NOTE:
                            raise ValueError(
                                f"Pad {number} is not Note type; use --note to switch it"
                            )
                        low = selected.value1 if args.min_velocity is None else args.min_velocity
                        high = selected.value2 if args.max_velocity is None else args.max_velocity
                        if low > high:
                            raise ValueError(f"Pad {number}: minimum velocity exceeds maximum")
                        selected.value1, selected.value2 = low, high
                    if args.channel is not None:
                        selected.channel = args.channel
                    if args.brightness is not None:
                        selected.brightness = args.brightness
            print(
                f"Preset {preset}, bank {bank}, pads {','.join(map(str, args.pads))}: updated. No Save sent."
            )
        if control is not None:
            device.send(
                [control_message(preset, number, control.replace("-", "_")) for number in args.pads]
            )
            print(
                f"Preset {preset}, physical pads {','.join(map(str, args.pads))}: mode/control {control}, across ALL banks. No Save sent."
            )
        if not editing_pads and control is None:
            with Session(args.port) as session:
                settings = read(session, 5, preset_offset(preset) + 2912, 19)
                records = read(session, 5, pad_offset(preset, bank, 1), 416)
            print(f"Preset {preset}, bank {bank}")
            for number in args.pads:
                record = records[(number - 1) * 26 : number * 26]
                print(
                    f"Pad {number:2}: {describe_pad(record, settings[number - 1])}; "
                    f"color #{record[5:8].hex()}; brightness {record[8]}"
                )
    except (ValueError, RuntimeError, TimeoutError, OSError) as error:
        cli.exit(1, f"{error}\n")


def main(argv=None):
    from .disco import main as demo
    from .launcher import main as launcher
    from .listen import main as listen
    from .protocol import main as protocol

    handlers = {
        "demo": demo,
        "preset": preset,
        "note-repeat": note_repeat,
        "globe": globe,
        "pad": pad,
        "show": show,
        "listen": listen,
        "launcher": launcher,
        "protocol": protocol,
    }
    cli = argparse.ArgumentParser(
        prog="discofloor", description="DiscoFloor: live Pocket colors and configuration"
    )
    cli.add_argument("--port", help=PORT_HELP)
    subcommands = cli.add_subparsers(dest="command", required=True)
    descriptions = {
        "demo": "Run the restoring 110 BPM disco demo",
        "preset": "Select/save/export/import/reset presets",
        "note-repeat": "Tempo, time, swing, sync and latch (independent of presets)",
        "globe": "Curve, bank, aftertouch, calibration and bank snapshots",
        "pad": "Inspect/edit a pad range: colors, notes, and settings",
        "show": "Show what the active bank’s pads are configured to send",
        "listen": "Listen for pad strikes and incoming MIDI",
        "launcher": "Run chosen commands on incoming MIDI triggers",
        "protocol": "Low-level packet and capture tools",
    }
    for name, description in descriptions.items():
        subcommands.add_parser(name, help=description, add_help=False)
    argv = sys.argv[1:] if argv is None else argv
    args, remaining = cli.parse_known_args(argv)
    if args.port is not None:
        remaining = ["--port", args.port, *remaining]
    handlers[args.command](remaining)
