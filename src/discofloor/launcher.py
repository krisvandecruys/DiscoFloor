"""Run chosen commands on matching incoming performance MIDI; sends no MIDI."""

import argparse
import math
import subprocess
import time
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

from .listen import choose_input
from .model import bounded


@dataclass(frozen=True)
class Trigger:
    kind: str
    number: int
    channel: int | None = None
    velocity: int | None = None
    min_velocity: int = 0
    max_velocity: int = 127
    value: int | None = None
    event: str = "note-on"

    def __post_init__(self):
        if self.kind not in ("note", "cc", "program"):
            raise ValueError("Trigger must be note, cc or program")
        if self.event not in ("note-on", "note-off", "both"):
            raise ValueError("Event must be note-on, note-off or both")
        if self.kind != "note" and self.event != "note-on":
            raise ValueError("Event filter applies only to notes")
        bounded(self.number, 0, 127)
        if self.channel is not None:
            bounded(self.channel, 1, 16)
        bounded(self.min_velocity, 0, 127)
        bounded(self.max_velocity, 0, 127)
        if self.min_velocity > self.max_velocity:
            raise ValueError("Minimum velocity exceeds maximum velocity")
        if self.velocity is not None:
            bounded(self.velocity, 0, 127)
            if not self.min_velocity <= self.velocity <= self.max_velocity:
                raise ValueError("Exact velocity falls outside velocity bounds")
        if self.kind != "note" and (
            self.velocity is not None or self.min_velocity != 0 or self.max_velocity != 127
        ):
            raise ValueError("Velocity filters apply only to notes")
        if self.value is not None:
            bounded(self.value, 0, 127)
            if self.kind != "cc":
                raise ValueError("Value filter requires a CC trigger")

    def matches(self, message):
        if self.channel is not None and getattr(message, "channel", -1) + 1 != self.channel:
            return False
        if self.kind == "note":
            if message.type not in ("note_on", "note_off"):
                return False
            event = "note-on" if message.type == "note_on" and message.velocity > 0 else "note-off"
            return (
                (self.event == "both" or self.event == event)
                and message.note == self.number
                and self.min_velocity <= message.velocity <= self.max_velocity
                and (self.velocity is None or message.velocity == self.velocity)
            )
        if self.kind == "cc":
            return (
                message.type == "control_change"
                and message.control == self.number
                and (self.value is None or message.value == self.value)
            )
        return message.type == "program_change" and message.program == self.number


@dataclass(frozen=True)
class Binding:
    trigger: Trigger
    command: tuple[str, ...]
    cooldown: float = 0.0
    allow_overlap: bool = False
    cwd: Path | None = None

    def __post_init__(self):
        if (
            not isinstance(self.command, (list, tuple))
            or not self.command
            or any(not isinstance(arg, str) or "\x00" in arg for arg in self.command)
            or not self.command[0]
        ):
            raise ValueError("Command must be a string array with a nonempty executable")
        object.__setattr__(self, "command", tuple(self.command))
        if (
            type(self.cooldown) not in (int, float)
            or not math.isfinite(self.cooldown)
            or self.cooldown < 0
        ):
            raise ValueError("Cooldown must be finite and nonnegative")
        if type(self.allow_overlap) is not bool:
            raise TypeError("allow_overlap must be Boolean")
        if self.cwd is not None and not self.cwd.is_dir():
            raise ValueError(f"Working directory does not exist: {self.cwd}")

    def arguments(self, message):
        """Substitute MIDI fields inside individual arguments; never parse a shell."""
        values = {
            name: str(getattr(message, name, 0))
            for name in ("note", "velocity", "control", "value", "program")
        }
        values["channel"] = str(getattr(message, "channel", -1) + 1)
        values["event"] = (
            "note-off"
            if message.type == "note_off" or (message.type == "note_on" and message.velocity == 0)
            else "note-on"
            if message.type == "note_on"
            else message.type
        )
        result = []
        for argument in self.command:
            for key, value in values.items():
                argument = argument.replace("{" + key + "}", value)
            result.append(argument)
        return result


@dataclass
class RunningBinding:
    binding: Binding
    processes: list = field(default_factory=list)
    last_started: float | None = None
    dry_run: bool = False

    def reap(self):
        remaining = []
        for process in self.processes:
            status = process.poll()
            if status is None:
                remaining.append(process)
            elif status != 0:
                print(f"Command PID {process.pid} exited with status {status}.", flush=True)
        self.processes = remaining

    def receive(self, message, now):
        self.reap()
        if not self.binding.trigger.matches(message):
            return
        if self.processes and not self.binding.allow_overlap:
            print(
                "Skipped trigger: command still running; use --allow-overlap to launch concurrently.",
                flush=True,
            )
            return
        if self.last_started is not None and now - self.last_started < self.binding.cooldown:
            return
        arguments = self.binding.arguments(message)
        if self.dry_run:
            self.last_started = now
            print(f"Would launch: {arguments!r}", flush=True)
            return
        try:
            process = subprocess.Popen(
                arguments, cwd=self.binding.cwd, stdin=subprocess.DEVNULL, start_new_session=True
            )
        except OSError as error:
            print(f"Could not launch {arguments[0]}: {error}", flush=True)
            return
        self.processes.append(process)
        self.last_started = now
        print(f"Launched PID {process.pid}: {arguments!r}", flush=True)


class Launcher:
    def __init__(self, bindings, *, port=None, backend=None, dry_run=False):
        self.bindings = list(bindings)
        if not self.bindings or any(not isinstance(binding, Binding) for binding in self.bindings):
            raise ValueError("Provide at least one Binding")
        self.port, self.backend = port, backend
        self.dry_run = dry_run

    def run(self):
        backend = self.backend
        if backend is None:
            import mido

            backend = mido.Backend("mido.backends.rtmidi")
        name = choose_input(backend.get_input_names(), self.port)
        running = [RunningBinding(binding, dry_run=self.dry_run) for binding in self.bindings]
        print(
            f"Launcher listening on {name}; {len(running)} binding(s). Ctrl-C stops listening; launched commands keep running.",
            flush=True,
        )
        with backend.open_input(name) as incoming:
            try:
                while True:
                    for state in running:
                        state.reap()
                    for message in incoming.iter_pending():
                        now = time.monotonic()
                        for state in running:
                            state.receive(message, now)
                    time.sleep(0.005)
            finally:
                for state in running:
                    state.reap()


def load_config(path):
    data = tomllib.loads(path.read_text())
    if set(data) - {"port", "binding"}:
        raise ValueError("Config supports only port and [[binding]] entries")
    port = data.get("port")
    if port is not None and (not isinstance(port, str) or not port):
        raise ValueError("Config port must be a nonempty string")
    entries = data.get("binding", [])
    if not isinstance(entries, list) or not entries:
        raise ValueError("Config requires at least one [[binding]]")
    bindings = []
    for entry in entries:
        if not isinstance(entry, dict) or set(entry) - {
            "note",
            "cc",
            "program",
            "channel",
            "velocity",
            "min_velocity",
            "max_velocity",
            "value",
            "event",
            "command",
            "cooldown",
            "allow_overlap",
            "cwd",
        }:
            raise ValueError("Unknown or invalid binding settings")
        kinds = [name for name in ("note", "cc", "program") if name in entry]
        if len(kinds) != 1:
            raise ValueError("Each binding requires exactly one note, cc or program")
        kind = kinds[0]
        if kind != "note" and any(
            key in entry for key in ("velocity", "min_velocity", "max_velocity", "event")
        ):
            raise ValueError("Event and velocity filters require a note binding")
        trigger = Trigger(
            kind,
            entry[kind],
            **{
                name: entry[name]
                for name in (
                    "channel",
                    "velocity",
                    "min_velocity",
                    "max_velocity",
                    "value",
                    "event",
                )
                if name in entry
            },
        )
        cwd = entry.get("cwd")
        if cwd is not None:
            if not isinstance(cwd, str):
                raise ValueError("cwd must be a path string")
            cwd = Path(cwd)
            if not cwd.is_absolute():
                cwd = path.parent / cwd
        bindings.append(
            Binding(
                trigger,
                entry.get("command", ()),
                entry.get("cooldown", 0.0),
                entry.get("allow_overlap", False),
                cwd,
            )
        )
    return bindings, port


def main(argv=None):
    cli = argparse.ArgumentParser(prog="discofloor launcher", description=__doc__)
    cli.add_argument("--port", help="Performance MIDI input; default: USB Master, then Bluetooth")
    triggers = cli.add_mutually_exclusive_group(required=True)
    triggers.add_argument("--note", type=int, metavar="NUMBER", help="MIDI note number 0–127")
    triggers.add_argument("--cc", type=int, metavar="NUMBER", help="CC number 0–127")
    triggers.add_argument("--program", type=int, metavar="NUMBER", help="Program change 0–127")
    triggers.add_argument("--config", type=Path, help="TOML file with multiple [[binding]] entries")
    cli.add_argument(
        "--event",
        choices=("note-on", "note-off", "both"),
        default=None,
        help="Note event; default: note-on. Velocity-zero Note On is note-off",
    )
    cli.add_argument("--channel", type=int, help="MIDI channel 1–16; default: any")
    cli.add_argument(
        "--velocity", type=int, help="Exact note velocity 0–127 (including release velocity)"
    )
    cli.add_argument(
        "--min-velocity", type=int, default=None, help="Minimum note velocity; default 0"
    )
    cli.add_argument(
        "--max-velocity", type=int, default=None, help="Maximum note velocity; default 127"
    )
    cli.add_argument("--value", type=int, help="Exact CC value; default: any")
    cli.add_argument(
        "--cooldown", type=float, default=None, help="Seconds between launches; default 0"
    )
    cli.add_argument(
        "--allow-overlap", action="store_true", help="Allow concurrent runs of the same binding"
    )
    cli.add_argument(
        "--dry-run",
        action="store_true",
        help="Listen and print matched commands without executing them",
    )
    cli.add_argument("command", nargs=argparse.REMAINDER, help="Command and arguments after --")
    args = cli.parse_args(argv)
    command = args.command[1:] if args.command[:1] == ["--"] else args.command
    try:
        if args.config:
            if (
                command
                or args.allow_overlap
                or any(
                    getattr(args, name) is not None
                    for name in (
                        "channel",
                        "velocity",
                        "min_velocity",
                        "max_velocity",
                        "value",
                        "cooldown",
                        "event",
                    )
                )
            ):
                cli.error("--config cannot be combined with trigger filters or a command")
            bindings, port = load_config(args.config)
        else:
            if not command:
                cli.error("Provide a command after --, e.g. -- open -a Music")
            kind = next(
                name for name in ("note", "cc", "program") if getattr(args, name) is not None
            )
            if kind != "note" and any(
                getattr(args, name) is not None
                for name in ("velocity", "min_velocity", "max_velocity")
            ):
                cli.error("Velocity filters require --note")
            if kind != "note" and args.event is not None:
                cli.error("--event requires --note")
            trigger = Trigger(
                kind,
                getattr(args, kind),
                args.channel,
                args.velocity,
                0 if args.min_velocity is None else args.min_velocity,
                127 if args.max_velocity is None else args.max_velocity,
                args.value,
                "note-on" if args.event is None else args.event,
            )
            bindings = [
                Binding(
                    trigger,
                    tuple(command),
                    0.0 if args.cooldown is None else args.cooldown,
                    args.allow_overlap,
                )
            ]
            port = None
        Launcher(
            bindings, port=args.port if args.port is not None else port, dry_run=args.dry_run
        ).run()
    except KeyboardInterrupt:
        print("\nStopped launcher; launched commands keep running.")
    except (ValueError, TypeError, RuntimeError, TimeoutError, OSError) as error:
        cli.exit(1, f"{error}\n")
