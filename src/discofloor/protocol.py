"""SMC PAD Pocket protocol tools. Dry-run unless --send is explicitly supplied."""

import argparse
import json
import plistlib
from pathlib import Path
from typing import Any

from .selections import pad_range

RECORD_SIZE = 26
PRESET_SIZE = 2931
PAD_TYPES = {"note": 0, "cc_toggle": 1, "momentary": 2, "program": 3, "custom": 4}
CONTROL_FUNCTIONS = {
    "pad": 0,
    "note_repeat": 1,
    "rate_up": 2,
    "rate_down": 3,
    "swing_up": 4,
    "swing_down": 5,
    "bank_up": 6,
    "bank_down": 7,
    "latch": 8,
}
REPEAT_RATES = {
    "1/4": 0,
    "1/4T": 1,
    "1/8": 2,
    "1/8T": 3,
    "1/16": 4,
    "1/16T": 5,
    "1/32": 6,
    "1/32T": 7,
}
PAD_FIELDS = {
    "type": (0, 0, 4),
    "channel": (1, 0, 15),
    "number": (2, 0, 127),
    "value1": (3, 0, 127),
    "value2": (4, 0, 127),
    "led": (8, 0, 255),
}


def build_packet(operation: int, body: bytes = b"") -> bytes:
    raw = b"\x00\x59" + bytes([operation]) + len(body).to_bytes(2, "little") + b"\x00"
    raw += body + bytes([(255 - sum(body)) & 255])
    return b"\xf0" + pack(raw) + b"\xf7"


def pack(raw: bytes) -> bytes:
    number = int.from_bytes(raw, "little")
    return bytes((number >> shift) & 127 for shift in range(0, len(raw) * 8, 7))


def unpack(packed: bytes) -> bytes:
    if any(value > 127 for value in packed):
        raise ValueError("SysEx body must contain seven-bit bytes")
    number = sum(value << (7 * index) for index, value in enumerate(packed))
    size = len(packed) * 7 // 8
    if number >> (size * 8):
        raise ValueError("Nonzero padding bits")
    return number.to_bytes(size, "little")


def build_write(offset: int, data: bytes, register: int = 5) -> bytes:
    if not 0 <= offset < 2**32 or not 0 <= len(data) <= 1024:
        raise ValueError("Invalid offset or payload size")
    body = bytes([register]) + offset.to_bytes(4, "little") + len(data).to_bytes(3, "little") + data
    return build_packet(0x22, body)


def build_read(register: int, offset: int, count: int) -> bytes:
    if register not in (3, 4, 5) or not 0 <= offset < 2**32 or not 1 <= count <= 1009:
        raise ValueError("Read only known registers, at most 1009 bytes at a time")
    body = bytes([register]) + offset.to_bytes(4, "little") + count.to_bytes(3, "little")
    return build_packet(0x23, body)


def preset_offset(preset: int) -> int:
    if not 1 <= preset <= 4:
        raise ValueError("Preset must be 1–4")
    return (preset - 1) * PRESET_SIZE


def pad_offset(preset: int, bank: int, pad: int) -> int:
    return preset_offset(preset) + color_offset(bank, pad) - 5


def color_offset(bank: int, pad: int) -> int:
    if not 1 <= bank <= 7 or not 1 <= pad <= 16:
        raise ValueError("Bank must be 1–7; pad must be 1–16")
    return ((bank - 1) * 16 + pad - 1) * RECORD_SIZE + 5


def color_message(bank: int, pad: int, rgb: tuple[int, int, int], preset: int = 1) -> bytes:
    if len(rgb) != 3:
        raise ValueError("RGB must have exactly three components")
    return build_write(pad_offset(preset, bank, pad) + 5, bytes(rgb))


def pad_field_message(preset: int, bank: int, pad: int, field: str, value: int) -> bytes:
    offset, minimum, maximum = PAD_FIELDS[field]
    if not minimum <= value <= maximum:
        raise ValueError(f"{field} must be {minimum}–{maximum}; channel is zero-based")
    return build_write(pad_offset(preset, bank, pad) + offset, bytes([value]))


def custom_message(preset: int, bank: int, pad: int, data: bytes) -> bytes:
    if not 1 <= len(data) <= 16:
        raise ValueError("Custom payload must contain 1–16 bytes")
    return build_write(pad_offset(preset, bank, pad) + 9, bytes([len(data)]) + data)


def control_message(preset: int, pad: int, function: str) -> bytes:
    if not 1 <= pad <= 16:
        raise ValueError("Pad must be 1–16")
    return build_write(preset_offset(preset) + 2912 + pad - 1, bytes([CONTROL_FUNCTIONS[function]]))


def preset_setting_message(preset: int, setting: str, value: int) -> bytes:
    fields = {"bank": (2928, 1, 7), "curve": (2929, 1, 4), "aftertouch": (2930, 0, 1)}
    offset, minimum, maximum = fields[setting]
    if not minimum <= value <= maximum:
        raise ValueError(f"{setting} must be {minimum}–{maximum}")
    wire_value = value if setting == "aftertouch" else value - 1
    return build_write(preset_offset(preset) + offset, bytes([wire_value]))


def runtime_message(setting: str, value: int | str) -> bytes:
    fields = {
        "tempo": (0, 2, 1, 65535),
        "swing": (2, 1, 0, 255),
        "rate": (3, 1, 0, 7),
        "sync": (4, 1, 0, 1),
        "latch": (5, 1, 0, 1),
        "preset": (7, 1, 1, 4),
    }
    offset, width, minimum, maximum = fields[setting]
    if setting == "rate" and isinstance(value, str):
        value = REPEAT_RATES[value]
    if not isinstance(value, int) or not minimum <= value <= maximum:
        raise ValueError(f"{setting} must be {minimum}–{maximum}")
    if setting == "preset":
        value -= 1
    return build_write(offset, value.to_bytes(width, "little"), 4)


def calibration_messages(pad: int, level: int) -> list[bytes]:
    if not 1 <= pad <= 16 or not 1 <= level <= 8:
        raise ValueError("Pad must be 1–16 and calibration level 1–8")
    threshold = 50 + (level - 1) * 100
    return [build_write((pad - 1) * 10, threshold.to_bytes(2, "little"), 3), build_write(0, b"", 3)]


def config_messages(data: bytes, preset: int = 1) -> list[bytes]:
    if len(data) != PRESET_SIZE:
        raise ValueError(f"Expected a {PRESET_SIZE}-byte .spp preset")
    base = preset_offset(preset)
    return [
        build_write(base + offset, data[offset : offset + 1024])
        for offset in range(0, len(data), 1024)
    ]


def decode(message: bytes) -> dict:
    if message[:1] != b"\xf0" or message[-1:] != b"\xf7":
        raise ValueError("Missing SysEx delimiters")
    raw = unpack(message[1:-1])
    if raw[:2] != b"\x00\x59" or len(raw) < 7:
        raise ValueError("Unknown packet signature")
    length = int.from_bytes(raw[3:5], "little")
    if len(raw) != length + 7 or raw[5] != 0:
        raise ValueError("Invalid packet length or reserved byte")
    if sum(raw[6:]) & 255 != 255:
        raise ValueError("Checksum mismatch")
    result = {"type": raw[2], "length": length, "raw": raw.hex(" ")}
    if raw[2] == 0x11:
        result["identity"] = raw[6:-1].split(b"\x00", 1)[0].decode("ascii", errors="replace")
    if len(raw) >= 15 and raw[2] in (0x22, 0x23):
        result.update(
            register=raw[6],
            offset=int.from_bytes(raw[7:11], "little"),
            count=int.from_bytes(raw[11:14], "little"),
            data=raw[14:-1].hex(" "),
        )
    return result


def send_messages(messages: list[bytes], port_name: str | None, *, backend: Any = None) -> None:
    """Wait for the captured success ACK after each write; never retry automatically."""
    import time

    if backend is None:
        import mido

        backend = mido.Backend("mido.backends.rtmidi")
    for message in messages:
        packet = decode(message)
        if packet["type"] != 34 or packet.get("register") not in (3, 4, 5):
            raise ValueError("Only captured register-3/4/5 writes are supported")
    from .ports import require_ports

    port_name = require_ports(backend, port_name)
    with (
        backend.open_input(port_name) as incoming,
        backend.open_output(port_name, autoreset=False) as outgoing,
    ):
        for message in messages:
            list(incoming.iter_pending())
            # The backend provides port classes; Message is independent of the backend.
            factory = getattr(backend, "message_from_bytes", None)
            if callable(factory):
                midi_message = factory(message)
            else:
                import mido

                midi_message = mido.Message.from_bytes(list(message))
            outgoing.send(midi_message)
            deadline = time.monotonic() + 2
            acknowledged = False
            while time.monotonic() < deadline:
                for reply in incoming.iter_pending():
                    if reply.type != "sysex":
                        continue
                    try:
                        result = decode(bytes(reply.bytes()))
                    except ValueError:
                        continue
                    if result["type"] == 0:
                        if result["raw"] != "00 59 00 01 00 00 00 ff":
                            raise RuntimeError("Unexpected acknowledgement; upload stopped")
                        acknowledged = True
                        break
                if acknowledged:
                    break
                time.sleep(0.005)
            if not acknowledged:
                raise TimeoutError("No success ACK within 2 seconds; upload stopped")


def capture_frames(path: Path) -> list[dict]:
    archive = plistlib.loads(plistlib.loads(path.read_bytes())["messageData"])
    objects = archive["$objects"]
    frames = []
    for item in objects:
        if not isinstance(item, dict) or item.get("statusByte") != 240:
            continue
        message = b"\xf0" + objects[item["data"].data]
        if item.get("wasReceivedWithEOX"):
            message += b"\xf7"
        try:
            packet = decode(message)
        except ValueError as error:
            packet = {"decode_error": str(error)}
        frames.append(
            {
                "origin": objects[item["originatingEndpoint"].data],
                "timestamp": item.get("clockTimeStamp"),
                "sysex": message.hex(" "),
                **packet,
            }
        )
    return frames


def main(argv=None) -> None:
    parser = argparse.ArgumentParser(prog="discofloor protocol", description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    color = commands.add_parser("color", help="Generate a color SysEx; sends nothing")
    color.add_argument("pad", type=pad_range, help="Pad selection, e.g. 1-4,7,9-12")
    color.add_argument("rgb", type=int, nargs=3)
    color.add_argument("--bank", type=int, default=3)
    color.add_argument("--preset", type=int, default=1)
    color.add_argument("--output", type=Path)
    capture = commands.add_parser("capture", help="Decode MIDI Monitor .mmon")
    capture.add_argument("path", type=Path)
    upload = commands.add_parser("config", help="Generate candidate config writes; sends nothing")
    upload.add_argument("path", type=Path)
    upload.add_argument("--output", type=Path, required=True)
    upload.add_argument("--preset", type=int, default=1)
    save = commands.add_parser("save", help="Generate the captured persistence command")
    commands.add_parser("ports", help="List MIDI port names; sends nothing")
    for command in (color, upload, save):
        command.add_argument("--send", action="store_true")
        command.add_argument("--port", help="Exact Private input/output port name")
    args = parser.parse_args(argv)
    if args.command == "ports":
        import mido

        backend = mido.Backend("mido.backends.rtmidi")
        print(
            json.dumps(
                {"inputs": backend.get_input_names(), "outputs": backend.get_output_names()},
                indent=2,
            )
        )
        return
    if getattr(args, "send", False) and not args.port:
        parser.error("--send requires an explicit --port")
    if args.command == "color":
        messages = [color_message(args.bank, pad, tuple(args.rgb), args.preset) for pad in args.pad]
        for message in messages:
            print(message.hex(" ").upper())
        if args.output:
            args.output.write_bytes(b"".join(messages))
    elif args.command == "capture":
        print(json.dumps(capture_frames(args.path), indent=2))
        return
    elif args.command == "config":
        data = args.path.read_bytes()
        if len(data) != PRESET_SIZE:
            parser.error(f"Expected a {PRESET_SIZE}-byte .spp preset")
        messages = config_messages(data, args.preset)
        args.output.write_bytes(b"".join(messages))
        print("Generated three configuration writes.")
    else:
        messages = [build_write(0, b"")]
        print(messages[0].hex(" ").upper())
    if args.send:
        from .ports import MidiPortUnavailable

        try:
            send_messages(messages, args.port)
        except MidiPortUnavailable as error:
            parser.exit(1, f"{error}\n")
        print(f"Device acknowledged {len(messages)} write(s).")
    else:
        print("Dry run: no MIDI ports opened, no messages sent.")


if __name__ == "__main__":
    main()
