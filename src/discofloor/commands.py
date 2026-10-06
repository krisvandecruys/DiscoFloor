"""Small live examples: bank, preset, color, fill, and explicit Save."""

import argparse
import sys

from . import Color, Pocket
from .protocol import build_read, preset_offset
from .transport import Session


def parser(description, command):
    result = argparse.ArgumentParser(prog=f"discofloor {command}", description=description)
    result.add_argument("--port", default="SINCO SMC-PAD Pocket-Private")
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


def selection(kind, argv=None):
    maximum = 7 if kind == "bank" else 4
    cli = parser(f"Read or select the active {kind}. Close Midi Suite first.", kind)
    cli.add_argument("number", nargs="?", type=int, choices=range(1, maximum + 1))
    args = cli.parse_args(argv)
    try:
        device = Pocket(args.port)
        if kind == "preset":
            if args.number is not None:
                device.select_preset(args.number)
            with Session(args.port) as session:
                print(active_preset(session))
        else:
            with Session(args.port) as session:
                preset = active_preset(session)
            if args.number is not None:
                from .protocol import preset_setting_message

                device.send([preset_setting_message(preset, "bank", args.number)])
            with Session(args.port) as session:
                print(active_bank(session, preset))
    except (ValueError, RuntimeError, TimeoutError, OSError) as error:
        cli.exit(1, f"{error}\n")


def bank(argv=None):
    selection("bank", argv)


def preset(argv=None):
    selection("preset", argv)


def color_command(fill_bank, argv=None):
    cli = parser(
        "Change colors without Save. Defaults to the active preset and bank.",
        "fill" if fill_bank else "color",
    )
    if not fill_bank:
        cli.add_argument("pad", type=int, choices=range(1, 17))
    cli.add_argument("color", help="Six-digit RGB hex, such as ff0044 or '#ff0044'")
    cli.add_argument("--preset", type=int, choices=range(1, 5))
    cli.add_argument("--bank", type=int, choices=range(1, 8))
    args = cli.parse_args(argv)
    try:
        value = Color.from_hex(args.color)
    except ValueError as error:
        cli.error(str(error))
    try:
        device = Pocket(args.port)
        preset, bank = args.preset, args.bank
        if preset is None or bank is None:
            with Session(args.port) as session:
                if preset is None:
                    preset = active_preset(session)
                if bank is None:
                    bank = active_bank(session, preset)
        if fill_bank:
            with device.preset[preset].bank[bank].edit() as edited:
                edited.fill(value)
        else:
            device.pad[preset, bank, args.pad].color = value
        print(f"Preset {preset}, bank {bank}: color applied. No Save sent.")
    except (ValueError, RuntimeError, TimeoutError, OSError) as error:
        cli.exit(1, f"{error}\n")


def color(argv=None):
    color_command(False, argv)


def fill(argv=None):
    color_command(True, argv)


def save(argv=None):
    cli = parser(
        "Persist current preset configuration across slots (not runtime settings).", "save"
    )
    args = cli.parse_args(argv)
    try:
        Pocket(args.port).save()
        print("Preset configuration saved to device.")
    except (ValueError, RuntimeError, TimeoutError, OSError) as error:
        cli.exit(1, f"{error}\n")


def main(argv=None):
    from .disco import main as demo
    from .protocol import main as protocol

    handlers = {
        "demo": demo,
        "bank": bank,
        "preset": preset,
        "color": color,
        "fill": fill,
        "save": save,
        "protocol": protocol,
    }
    cli = argparse.ArgumentParser(
        prog="discofloor", description="DiscoFloor: live Pocket colors and configuration"
    )
    subcommands = cli.add_subparsers(dest="command", required=True)
    descriptions = {
        "demo": "Run the restoring 110 BPM disco demo",
        "bank": "Get or set the active bank",
        "preset": "Get or set the active preset",
        "color": "Set one pad color",
        "fill": "Fill a bank with one color",
        "save": "Persist preset configuration",
        "protocol": "Low-level packet and capture tools",
    }
    for name, description in descriptions.items():
        subcommands.add_parser(name, help=description, add_help=False)
    argv = sys.argv[1:] if argv is None else argv
    # Each command owns its arguments and help; only route the first token here.
    args = cli.parse_args(argv[:1])
    handlers[args.command](argv[1:])
