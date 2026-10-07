"""Editable views of captured configuration bytes; unknown bytes are preserved."""

from dataclasses import dataclass
from enum import IntEnum
from pathlib import Path

from . import protocol


def bounded(value: int, minimum: int, maximum: int) -> int:
    if type(value) is not int or not minimum <= value <= maximum:
        raise ValueError(f"Expected an integer from {minimum} to {maximum}, got {value!r}")
    return value


class PadType(IntEnum):
    NOTE = 0
    CC_TOGGLE = 1
    MOMENTARY = 2
    PROGRAM = 3
    CUSTOM = 4


class Control(IntEnum):
    PAD = 0
    NOTE_REPEAT = 1
    RATE_UP = 2
    RATE_DOWN = 3
    SWING_UP = 4
    SWING_DOWN = 5
    BANK_UP = 6
    BANK_DOWN = 7
    LATCH = 8


class RepeatRate(IntEnum):
    QUARTER = 0
    QUARTER_TRIPLET = 1
    EIGHTH = 2
    EIGHTH_TRIPLET = 3
    SIXTEENTH = 4
    SIXTEENTH_TRIPLET = 5
    THIRTY_SECOND = 6
    THIRTY_SECOND_TRIPLET = 7


@dataclass(frozen=True)
class Color:
    red: int
    green: int
    blue: int

    def __post_init__(self):
        for component in (self.red, self.green, self.blue):
            bounded(component, 0, 255)

    def to_bytes(self) -> bytes:
        return bytes((self.red, self.green, self.blue))

    @classmethod
    def from_hex(cls, value: str) -> "Color":
        value = value.removeprefix("#")
        if len(value) != 6:
            raise ValueError("Expected six hexadecimal digits, such as #ff0000")
        return cls(*bytes.fromhex(value))


class _Numbered:
    """One-based access, with callable compatibility for the original API."""

    def __init__(self, factory, maximum):
        self._factory, self._maximum = factory, maximum

    def __getitem__(self, number):
        return self._factory(bounded(number, 1, self._maximum))

    def __call__(self, number):
        return self[number]

    def __iter__(self):
        return (self[number] for number in range(1, self._maximum + 1))


class _ByteField:
    def __init__(self, offset, minimum=0, maximum=127, adjustment=0, enum=None):
        self.offset, self.minimum, self.maximum = offset, minimum, maximum
        self.adjustment, self.enum = adjustment, enum

    def __get__(self, obj, owner=None):
        if obj is None:
            return self
        value = obj._data[obj._offset + self.offset] + self.adjustment
        return self.enum(value) if self.enum else value

    def __set__(self, obj, value):
        if self.enum:
            value = int(self.enum(value))
        value = bounded(value, self.minimum, self.maximum)
        obj._data[obj._offset + self.offset] = value - self.adjustment


class Pad:
    """A pad view. MIDI channels, bank numbers, and pad numbers are one based."""

    type = _ByteField(0, 0, 4, enum=PadType)
    channel = _ByteField(1, 1, 16, adjustment=1)
    number = _ByteField(2)
    value1 = _ByteField(3)
    value2 = _ByteField(4)
    brightness = _ByteField(8, 0, 255)

    def __init__(self, preset: "Preset", bank: int, number: int):
        self._data = preset._data
        self._offset = ((bank - 1) * 16 + number - 1) * 26

    @property
    def color(self) -> Color:
        return Color(*self._data[self._offset + 5 : self._offset + 8])

    @color.setter
    def color(self, value: Color):
        if not isinstance(value, Color):
            raise TypeError("Use Color(red, green, blue) or Color.from_hex('#rrggbb')")
        self._data[self._offset + 5 : self._offset + 8] = value.to_bytes()

    @property
    def custom_payload(self) -> bytes:
        length = self._data[self._offset + 9]
        if length > 16:
            raise ValueError("Invalid custom payload length in preset")
        return bytes(self._data[self._offset + 10 : self._offset + 10 + length])

    @custom_payload.setter
    def custom_payload(self, payload: bytes):
        payload = bytes(payload)
        bounded(len(payload), 1, 16)
        self._data[self._offset + 9] = len(payload)
        self._data[self._offset + 10 : self._offset + 10 + len(payload)] = payload

    def note(
        self, number: int, *, channel: int = 10, min_velocity: int = 0, max_velocity: int = 127
    ) -> "Pad":
        values = [bounded(v, 0, 127) for v in (number, min_velocity, max_velocity)]
        bounded(channel, 1, 16)
        if min_velocity > max_velocity:
            raise ValueError("Minimum velocity cannot exceed maximum velocity")
        self.type, self.channel = PadType.NOTE, channel
        self.number, self.value1, self.value2 = values
        return self

    def cc(
        self,
        number: int,
        *,
        channel: int = 10,
        on: int = 127,
        off: int = 0,
        momentary: bool = False,
    ) -> "Pad":
        values = [bounded(v, 0, 127) for v in (number, off, on)]
        bounded(channel, 1, 16)
        self.type = PadType.MOMENTARY if momentary else PadType.CC_TOGGLE
        self.channel = channel
        self.number, self.value1, self.value2 = values
        return self

    def program(
        self, number: int, *, channel: int = 10, bank_msb: int = 0, bank_lsb: int = 0
    ) -> "Pad":
        values = [bounded(v, 0, 127) for v in (bank_lsb, bank_msb, number)]
        bounded(channel, 1, 16)
        self.type, self.channel = PadType.PROGRAM, channel
        self.number, self.value1, self.value2 = values
        return self

    def custom(self, payload: bytes, *, channel: int = 10) -> "Pad":
        bounded(channel, 1, 16)
        payload = bytes(payload)
        bounded(len(payload), 1, 16)
        self.custom_payload = payload
        self.type, self.channel = PadType.CUSTOM, channel
        return self


class Bank:
    def __init__(self, preset: "Preset", number: int):
        self.preset, self.number = preset, bounded(number, 1, 7)

    @property
    def pad(self):
        return _Numbered(lambda number: Pad(self.preset, self.number, number), 16)

    def __iter__(self):
        return (self.pad(number) for number in range(1, 17))

    def fill(self, color: Color) -> "Bank":
        for pad in self:
            pad.color = color
        return self


class LocalGlobe:
    """Local preset-wide settings; assignments only change memory."""

    def __init__(self, preset):
        self.preset = preset

    @property
    def bank(self):
        return self.preset.active_bank

    @bank.setter
    def bank(self, value):
        self.preset.active_bank = value

    @property
    def curve(self):
        return self.preset.velocity_curve

    @curve.setter
    def curve(self, value):
        self.preset.velocity_curve = value

    @property
    def aftertouch(self):
        return self.preset.aftertouch

    @aftertouch.setter
    def aftertouch(self, value):
        self.preset.aftertouch = value


class Preset:
    """A lossless .spp model. Editing is local until explicitly uploaded."""

    active_bank = _ByteField(2928, 1, 7, adjustment=1)
    velocity_curve = _ByteField(2929, 1, 4, adjustment=1)
    _offset = 0

    def __init__(self, data: bytes):
        if len(data) != protocol.PRESET_SIZE:
            raise ValueError("A preset must contain exactly 2931 bytes")
        self._data = bytearray(data)
        self._baseline = bytes(data)

    @classmethod
    def load(cls, path: str | Path) -> "Preset":
        return cls(Path(path).read_bytes())

    def export(self, path: str | Path):
        Path(path).write_bytes(self.to_bytes())

    def to_bytes(self) -> bytes:
        return bytes(self._data)

    @property
    def globe(self):
        return LocalGlobe(self)

    def copy(self) -> "Preset":
        return Preset(self.to_bytes())

    @property
    def bank(self):
        return _Numbered(lambda number: Bank(self, number), 7)

    @property
    def aftertouch(self) -> bool:
        return bool(self._data[2930])

    @aftertouch.setter
    def aftertouch(self, enabled: bool):
        if type(enabled) is not bool:
            raise TypeError("Aftertouch must be True or False")
        self._data[2930] = int(enabled)

    def control(self, pad: int) -> Control:
        return Control(self._data[2912 + bounded(pad, 1, 16) - 1])

    def assign_control(self, pad: int, function: Control):
        self._data[2912 + bounded(pad, 1, 16) - 1] = int(Control(function))

    def messages(self, *, slot: int, changes_only: bool = True) -> list[bytes]:
        base = protocol.preset_offset(slot)
        if not changes_only:
            return protocol.config_messages(self.to_bytes(), slot)
        # Contiguous changed bytes become small writes. No Save is added.
        messages = []
        start = 0
        while start < len(self._data):
            if self._data[start] == self._baseline[start]:
                start += 1
                continue
            end = start + 1
            while end < len(self._data) and end - start < 1024:
                if self._data[end] == self._baseline[end]:
                    break
                end += 1
            messages.append(protocol.build_write(base + start, bytes(self._data[start:end])))
            start = end
        return messages


@dataclass(frozen=True)
class Runtime:
    """Volatile settings. Numeric bounds describe storage, not firmware limits."""

    tempo: int
    swing: int
    repeat_rate: RepeatRate
    sync: bool
    latch: bool
    active_preset: int
    unknown: int = 0

    def to_bytes(self) -> bytes:
        bounded(self.tempo, 1, 65535)
        bounded(self.swing, 0, 255)
        bounded(self.active_preset, 1, 4)
        bounded(self.unknown, 0, 255)
        if type(self.sync) is not bool or type(self.latch) is not bool:
            raise TypeError("Sync and latch must be Boolean")
        return self.tempo.to_bytes(2, "little") + bytes(
            (
                self.swing,
                int(RepeatRate(self.repeat_rate)),
                int(self.sync),
                int(self.latch),
                self.unknown,
                self.active_preset - 1,
            )
        )

    @classmethod
    def from_bytes(cls, data: bytes) -> "Runtime":
        if len(data) != 8:
            raise ValueError("Runtime block must contain eight bytes")
        if data[4] > 1 or data[5] > 1:
            raise ValueError("Invalid Boolean in runtime block")
        result = cls(
            int.from_bytes(data[:2], "little"),
            data[2],
            RepeatRate(data[3]),
            bool(data[4]),
            bool(data[5]),
            data[7] + 1,
            data[6],
        )
        result.to_bytes()
        return result

    def message(self) -> bytes:
        return protocol.build_write(0, self.to_bytes(), register=4)


@dataclass(frozen=True)
class Calibration:
    threshold: int
    unknown_values: tuple[int, int, int, int]

    @property
    def level(self) -> int | None:
        if 50 <= self.threshold <= 750 and (self.threshold - 50) % 100 == 0:
            return (self.threshold - 50) // 100 + 1
        return None

    @classmethod
    def from_bytes(cls, data: bytes) -> "Calibration":
        if len(data) != 10:
            raise ValueError("Calibration record must contain ten bytes")
        values = [int.from_bytes(data[i : i + 2], "little") for i in range(0, 10, 2)]
        return cls(values[0], (values[1], values[2], values[3], values[4]))

    def to_bytes(self) -> bytes:
        if len(self.unknown_values) != 4:
            raise ValueError("Preserve all four unknown calibration values")
        return b"".join(
            bounded(v, 0, 65535).to_bytes(2, "little")
            for v in (self.threshold, *self.unknown_values)
        )
