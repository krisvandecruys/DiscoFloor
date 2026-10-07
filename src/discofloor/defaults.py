"""Factory configuration reconstructed from the validated Midi Suite reset capture."""

from .model import Color, Control, Preset
from .protocol import build_write

BANK_COLORS = (
    (0, 250, 250),
    (250, 0, 0),
    (250, 250, 0),
    (0, 0, 250),
    (250, 125, 125),
    (0, 250, 0),
    (250, 125, 190),
)
SPECIAL_NOTES = (
    (37, 36, 42, 54, 40, 38, 46, 44, 48, 47, 45, 43, 49, 55, 51, 53),
    (49, 35, 36, 51, 37, 38, 40, 39, 42, 46, 44, 54, 41, 45, 48, 52),
)
SPECIAL_COLORS = (
    (
        (58, 229, 255),
        (250, 5, 5),
        (0, 250, 21),
        (250, 250, 0),
        (58, 229, 255),
        (58, 229, 255),
        (0, 250, 21),
        (0, 250, 21),
        (250, 250, 0),
        (250, 250, 0),
        (250, 250, 0),
        (250, 250, 0),
        (58, 250, 0),
        (58, 250, 0),
        (55, 250, 78),
        (55, 250, 78),
    ),
    (
        (0, 250, 179),
        (250, 175, 0),
        (250, 175, 0),
        (0, 250, 179),
        (168, 110, 250),
        (250, 175, 0),
        (250, 175, 0),
        (168, 110, 250),
        (250, 175, 0),
        (250, 175, 0),
        (250, 175, 0),
        (250, 175, 0),
        (250, 0, 208),
        (250, 0, 208),
        (250, 0, 208),
        (0, 250, 179),
    ),
)


def factory_preset(number: int) -> Preset:
    if type(number) is not int or not 1 <= number <= 4:
        raise ValueError("Preset must be 1–4")
    preset = Preset(bytes(2931))
    for bank in range(1, 8):
        for pad in range(1, 17):
            selected = preset.bank[bank].pad[pad]
            selected.note(4 + (bank - 1) * 16 + pad - 1, channel=10)
            selected.color = Color(*BANK_COLORS[bank - 1])
            selected.brightness = 255
    if number in (2, 3):
        for pad in range(1, 17):
            selected = preset.bank[3].pad[pad]
            selected.number = SPECIAL_NOTES[number - 2][pad - 1]
            selected.color = Color(*SPECIAL_COLORS[number - 2][pad - 1])
    if number == 4:
        for pad, control in enumerate((5, 4, 7, 6, 1, 8, 3, 2), start=9):
            preset.assign_control(pad, Control(control))
    preset.active_bank = 3
    preset.velocity_curve = 2
    preset.aftertouch = True
    return preset


def reset_all_messages() -> list[bytes]:
    data = b"".join(factory_preset(number).to_bytes() for number in range(1, 5))
    return [
        build_write(offset, data[offset : offset + 1024]) for offset in range(0, len(data), 1024)
    ] + [build_write(0, bytes.fromhex("78 00 32 04 00 00 00 00"), 4)]
