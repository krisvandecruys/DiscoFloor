"""Explicit transmission of validated writes; constructing objects sends nothing."""

from contextlib import contextmanager
from dataclasses import dataclass

from . import protocol
from .model import Color, Preset, RepeatRate, _Numbered, bounded


@dataclass(frozen=True)
class PadId:
    """Complete, one-based device address."""

    preset: int
    bank: int
    pad: int

    def __post_init__(self):
        bounded(self.preset, 1, 4)
        bounded(self.bank, 1, 7)
        bounded(self.pad, 1, 16)


class LivePad:
    def __init__(self, device, address: PadId):
        self.device, self.id = device, address

    @property
    def color(self):
        raise AttributeError("Live color is write-only; the client does not read device state")

    @color.setter
    def color(self, value: Color):
        if not isinstance(value, Color):
            raise TypeError("Expected Color")
        self.device.set_color(value, preset=self.id.preset, bank=self.id.bank, pad=self.id.pad)


@dataclass(frozen=True)
class BankSnapshot:
    """An in-memory bank/selection backup. restore() never persists to flash."""

    device: "Pocket"
    preset: int
    bank: int
    data: bytes
    selected_bank: bytes
    active_preset: bytes

    def restore(self):
        failures = []
        for message in (
            protocol.build_write(protocol.pad_offset(self.preset, self.bank, 1), self.data),
            protocol.build_write(protocol.preset_offset(self.preset) + 2928, self.selected_bank),
            protocol.build_write(7, self.active_preset, register=4),
        ):
            try:
                self.device.send([message])
            except Exception as error:
                failures.append(error)
        if failures:
            raise ExceptionGroup("Could not fully restore bank snapshot", failures)


class LiveBank:
    def __init__(self, device, preset, number):
        self.device, self.preset, self.number = device, preset, number
        self.pad = _Numbered(lambda pad: LivePad(device, PadId(preset, number, pad)), 16)

    def fill(self, color: Color):
        self.device.fill_bank(color, preset=self.preset, bank=self.number)

    def snapshot(self) -> BankSnapshot:
        """Read this bank and its bank selection, plus the active preset."""
        from .transport import Session

        offset = protocol.pad_offset(self.preset, self.number, 1)
        selection = protocol.preset_offset(self.preset) + 2928
        with Session(self.device.port, self.device._backend) as session:
            data = session.request(protocol.build_read(5, offset, 416), read_offset=offset)
            selected = session.request(
                protocol.build_read(5, selection, 1), read_offset=selection, read_count=1
            )
            runtime = session.request(
                protocol.build_read(4, 0, 8), read_offset=0, read_register=4, read_count=8
            )
        return BankSnapshot(self.device, self.preset, self.number, data, selected, runtime[7:8])

    @contextmanager
    def edit(self):
        """Read this bank; push a single 416-byte write on successful exit.

        No write occurs if the block raises or leaves the bank unchanged.
        Preset-wide settings and Save are outside this bank transaction.
        """
        from .transport import Session

        offset = protocol.pad_offset(self.preset, self.number, 1)
        with Session(self.device.port, self.device._backend) as session:
            original = session.request(protocol.build_read(5, offset, 416), read_offset=offset)
            # A local preset hosts the bank view. Only its bank records are sent.
            backing = bytearray(protocol.PRESET_SIZE)
            start = (self.number - 1) * 416
            backing[start : start + 416] = original
            model = Preset(bytes(backing))
            yield model.bank[self.number]
            updated = model.to_bytes()[start : start + 416]
            if updated != original:
                session.request(protocol.build_write(offset, updated))


class LivePreset:
    def __init__(self, device, number):
        self.device, self.number = device, number
        self.bank = _Numbered(lambda bank: LiveBank(device, number, bank), 7)


class _LivePads:
    def __init__(self, device):
        self.device = device

    def __getitem__(self, address):
        if isinstance(address, tuple):
            address = PadId(*address)
        if not isinstance(address, PadId):
            raise TypeError("Use PadId(preset, bank, pad) or a (preset, bank, pad) tuple")
        return LivePad(self.device, address)


class Pocket:
    """Write-only device client. Close Midi Suite before transmitting.

    Construction does not open ports. Each method resolves the USB Private or Bluetooth input/
    output pair (or the explicitly specified name) and waits for ACKs. No retries or implicit preset Save occur.
    """

    def __init__(self, port: str | None = None, *, backend=None):
        self.port, self._backend = port, backend
        self.preset = _Numbered(lambda number: LivePreset(self, number), 4)
        self.pad = _LivePads(self)

    def send(self, messages: list[bytes]):
        protocol.send_messages(messages, self.port, backend=self._backend)

    def set_color(self, color: Color, *, preset: int, bank: int, pad: int):
        self.send([protocol.color_message(bank, pad, (color.red, color.green, color.blue), preset)])

    def fill_bank(self, color: Color, *, preset: int, bank: int):
        self.send(
            [
                protocol.color_message(bank, pad, (color.red, color.green, color.blue), preset)
                for pad in range(1, 17)
            ]
        )

    def upload(self, preset: Preset, *, slot: int):
        """Upload the complete preset, regardless of the model's edit baseline."""
        self.send(preset.messages(slot=slot, changes_only=False))

    def apply_changes(self, preset: Preset, *, slot: int):
        """Apply differences from the loaded file. Assumes the device shares that baseline.

        The baseline is not advanced after sending: a partial failure does not
        silently discard pending changes. Use upload when device state is unknown.
        """
        messages = preset.messages(slot=slot)
        if messages:
            self.send(messages)

    def save(self):
        """Persist preset changes across slots. Does not persist runtime settings."""
        self.send([protocol.build_write(0, b"")])

    def select_preset(self, number: int):
        self.send([protocol.runtime_message("preset", number)])

    def set_repeat_rate(self, rate: RepeatRate):
        self.send([protocol.runtime_message("rate", int(RepeatRate(rate)))])

    def calibrate(self, pad: int, *, level: int):
        """Set physical-pad sensitivity: 1 most sensitive, 8 least.

        Higher levels help prevent double triggers. Persists immediately."""
        self.send(protocol.calibration_messages(pad, level))
