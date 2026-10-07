"""App-shaped live settings views. Reading and editing explicitly use MIDI."""

from contextlib import contextmanager
from dataclasses import dataclass, replace

from .model import Calibration, Preset, RepeatRate, Runtime, _Numbered, bounded
from .protocol import REPEAT_RATES, build_read, build_write, preset_offset
from .transport import Session


def read(device, register, offset, count):
    with Session(device.port, device._backend) as session:
        return session.request(
            build_read(register, offset, count),
            read_offset=offset,
            read_register=register,
            read_count=count,
        )


def active_preset(device):
    return Runtime.from_bytes(read(device, 4, 0, 8)).active_preset


@dataclass
class NoteRepeatSettings:
    tempo: int
    time: RepeatRate | str
    swing: int
    sync: bool
    latch: bool

    def apply_to(self, runtime):
        bounded(self.tempo, 1, 65535)
        bounded(self.swing, 0, 100)
        if type(self.sync) is not bool or type(self.latch) is not bool:
            raise TypeError("Sync and latch must be Boolean")
        if type(self.time) is bool:
            raise TypeError("Time must be a subdivision string or RepeatRate")
        if isinstance(self.time, str) and self.time not in REPEAT_RATES:
            raise ValueError("Unknown note-repeat subdivision")
        rate = (
            RepeatRate(REPEAT_RATES[self.time])
            if isinstance(self.time, str)
            else RepeatRate(self.time)
        )
        return replace(
            runtime,
            tempo=self.tempo,
            repeat_rate=rate,
            swing=self.swing,
            sync=self.sync,
            latch=self.latch,
        )


def live_field(name):
    def get(view):
        return getattr(view.read(), name)

    def set(view, value):
        view.configure(**{name: value})

    return property(get, set)


class NoteRepeat:
    tempo = live_field("tempo")
    time = live_field("time")
    swing = live_field("swing")
    sync = live_field("sync")
    latch = live_field("latch")

    def __init__(self, device):
        self.device = device

    def read(self) -> NoteRepeatSettings:
        runtime = Runtime.from_bytes(read(self.device, 4, 0, 8))
        return self._settings(runtime)

    @staticmethod
    def _settings(runtime):
        return NoteRepeatSettings(
            runtime.tempo, runtime.repeat_rate, runtime.swing, runtime.sync, runtime.latch
        )

    @contextmanager
    def edit(self):
        """Read once, stage changes, write once on successful exit. Never Save."""
        with Session(self.device.port, self.device._backend) as session:
            runtime = Runtime.from_bytes(
                session.request(build_read(4, 0, 8), read_register=4, read_offset=0, read_count=8)
            )
            settings = self._settings(runtime)
            yield settings
            updated = settings.apply_to(runtime)
            if updated != runtime:
                session.request(updated.message())

    def configure(self, **changes):
        if set(changes) - {"tempo", "time", "swing", "sync", "latch"}:
            raise TypeError("Unknown Note Repeat setting")
        with self.edit() as settings:
            for key, value in changes.items():
                setattr(settings, key, value)


@dataclass
class GlobeSettings:
    bank: int
    curve: int
    aftertouch: bool

    def to_bytes(self):
        bounded(self.bank, 1, 7)
        bounded(self.curve, 1, 4)
        if type(self.aftertouch) is not bool:
            raise TypeError("Aftertouch must be Boolean")
        return bytes((self.bank - 1, self.curve - 1, int(self.aftertouch)))


class Globe:
    bank = live_field("bank")
    curve = live_field("curve")
    aftertouch = live_field("aftertouch")

    def __init__(self, device, preset=None):
        self.device, self.preset = device, preset

    def __getitem__(self, preset):
        return Globe(self.device, bounded(preset, 1, 4))

    def _number(self):
        return active_preset(self.device) if self.preset is None else self.preset

    @staticmethod
    def _settings(data):
        settings = GlobeSettings(data[0] + 1, data[1] + 1, bool(data[2]))
        settings.to_bytes()
        if data[2] > 1:
            raise ValueError("Invalid aftertouch Boolean")
        return settings

    def read(self) -> GlobeSettings:
        return self._settings(read(self.device, 5, preset_offset(self._number()) + 2928, 3))

    @contextmanager
    def edit(self):
        offset = preset_offset(self._number()) + 2928
        with Session(self.device.port, self.device._backend) as session:
            original = session.request(build_read(5, offset, 3), read_offset=offset, read_count=3)
            settings = self._settings(original)
            yield settings
            updated = settings.to_bytes()
            if updated != original:
                session.request(build_write(offset, updated))

    def configure(self, **changes):
        if set(changes) - {"bank", "curve", "aftertouch"}:
            raise TypeError("Unknown Globe setting")
        with self.edit() as settings:
            for key, value in changes.items():
                setattr(settings, key, value)

    def calibrate(self, pads, *, level):
        """Physical pads, independent of this view's preset. Persists immediately."""
        bounded(level, 1, 8)
        pads = [pads] if type(pads) is int else list(pads)
        for pad in pads:
            bounded(pad, 1, 16)
        for pad in dict.fromkeys(pads):
            self.device.calibrate(pad, level=level)

    def calibration(self, pad) -> Calibration:
        return Calibration.from_bytes(read(self.device, 3, (bounded(pad, 1, 16) - 1) * 10, 10))

    def snapshot(self, *, bank=None):
        number = self._number()
        bank = Globe(self.device, number).bank if bank is None else bounded(bank, 1, 7)
        return self.device.preset[number].bank[bank].snapshot()


class Presets(_Numbered):
    def __init__(self, device, factory):
        super().__init__(factory, 4)
        self.device = device

    @property
    def active(self):
        return active_preset(self.device)

    @active.setter
    def active(self, number):
        self.device.select_preset(bounded(number, 1, 4))

    def save(self):
        """Persist preset configuration across all slots."""
        self.device.save()

    def reset_all(self):
        from .defaults import reset_all_messages

        self.device.send(reset_all_messages())

    def export(self, path):
        self[self.active].export(path)

    def import_file(self, path):
        incoming = Preset.load(path)
        self[self.active].upload(incoming)

    def reset(self):
        self[self.active].reset()
