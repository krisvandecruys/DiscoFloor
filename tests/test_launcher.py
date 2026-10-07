"""Matching, process dispatch and config validation; no MIDI or real commands."""

import io
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest.mock import MagicMock, patch

import mido

from discofloor import commands
from discofloor.launcher import Binding, Launcher, RunningBinding, Trigger, load_config


class LauncherTests(unittest.TestCase):
    def test_note_on_off_and_velocity_zero_normalization(self):
        on = mido.Message("note_on", channel=9, note=36, velocity=100)
        off = mido.Message("note_off", channel=9, note=36, velocity=64)
        zero = mido.Message("note_on", channel=9, note=36, velocity=0)
        for event, expected in (
            ("note-on", [True, False, False]),
            ("note-off", [False, True, True]),
            ("both", [True, True, True]),
        ):
            trigger = Trigger("note", 36, channel=10, event=event)
            self.assertEqual([trigger.matches(message) for message in (on, off, zero)], expected)
        self.assertFalse(Trigger("note", 37).matches(on))
        self.assertFalse(Trigger("note", 36, channel=1).matches(on))
        self.assertTrue(Trigger("note", 36, event="note-off", velocity=0).matches(zero))

    def test_velocity_cc_and_program_filters(self):
        message = mido.Message("note_on", note=60, velocity=100)
        self.assertTrue(Trigger("note", 60, min_velocity=90, max_velocity=110).matches(message))
        self.assertFalse(Trigger("note", 60, velocity=99).matches(message))
        cc = mido.Message("control_change", control=74, value=127)
        self.assertTrue(Trigger("cc", 74, value=127).matches(cc))
        self.assertFalse(Trigger("cc", 74, value=0).matches(cc))
        self.assertTrue(Trigger("program", 7).matches(mido.Message("program_change", program=7)))
        self.assertFalse(Trigger("note", 60, event="both").matches(mido.Message("clock")))

    def test_no_shell_and_arguments_and_event_placeholders(self):
        message = mido.Message("note_on", note=36, velocity=0, channel=9)
        binding = Binding(
            Trigger("note", 36, event="note-off"),
            ("echo", "{note}", "{velocity}", "{channel}", "{event}", "literal; $(touch x)", ""),
        )
        process = MagicMock(pid=42)
        with (
            patch("discofloor.launcher.subprocess.Popen", return_value=process) as spawn,
            redirect_stdout(io.StringIO()),
        ):
            RunningBinding(binding).receive(message, 0)
        self.assertEqual(
            spawn.call_args.args[0],
            ["echo", "36", "0", "10", "note-off", "literal; $(touch x)", ""],
        )
        self.assertNotIn("shell", spawn.call_args.kwargs)
        self.assertTrue(spawn.call_args.kwargs["start_new_session"])

    def test_busy_cooldown_and_overlap(self):
        message = mido.Message("note_on", note=36, velocity=100)
        process = MagicMock(pid=42)
        process.poll.return_value = None
        with (
            patch("discofloor.launcher.subprocess.Popen", return_value=process) as spawn,
            redirect_stdout(io.StringIO()),
        ):
            state = RunningBinding(Binding(Trigger("note", 36), ("echo", "x"), cooldown=1))
            state.receive(message, 0)
            state.receive(message, 2)  # Busy, even after cooldown.
            self.assertEqual(spawn.call_count, 1)
            process.poll.return_value = 0
            state.receive(message, 0.5)  # Completed but cooldown active.
            self.assertEqual(spawn.call_count, 1)
            state.receive(message, 1.1)
            self.assertEqual(spawn.call_count, 2)
            process.poll.return_value = None
            overlap = RunningBinding(
                Binding(Trigger("note", 36, event="both"), ("echo", "x"), allow_overlap=True)
            )
            overlap.receive(message, 2)
            overlap.receive(mido.Message("note_off", note=36, velocity=0), 2.05)
            self.assertEqual(spawn.call_count, 4)

    def test_spawn_failure_does_not_mark_binding_busy(self):
        state = RunningBinding(Binding(Trigger("note", 36), ("missing", "command")))
        with (
            patch("discofloor.launcher.subprocess.Popen", side_effect=FileNotFoundError("missing")),
            redirect_stdout(io.StringIO()) as out,
        ):
            state.receive(mido.Message("note_on", note=36, velocity=100), 1)
        self.assertEqual(state.processes, [])
        self.assertIsNone(state.last_started)
        self.assertIn("Could not launch", out.getvalue())

    def test_toml_on_off_bindings_and_relative_cwd(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "launcher.toml"
            path.write_text("""port = "input"
[[binding]]
note = 36
event = "note-on"
channel = 10
min_velocity = 90
command = ["echo", "press {velocity}"]
cwd = "."
[[binding]]
note = 36
event = "note-off"
velocity = 0
command = ["echo", "release"]
""")
            bindings, port = load_config(path)
            self.assertEqual(port, "input")
            self.assertEqual(len(bindings), 2)
            self.assertEqual(bindings[0].cwd, Path(tmp))
            self.assertTrue(
                bindings[1].trigger.matches(mido.Message("note_on", note=36, velocity=0))
            )
            path.write_text('[[binding]]\nnote=36\ncommand="echo wrong"\n')
            with self.assertRaises(ValueError):
                load_config(path)

    def test_cli_dispatch_preserves_child_options(self):
        with patch("discofloor.launcher.Launcher.run") as run:
            commands.main(
                [
                    "launcher",
                    "--note",
                    "36",
                    "--event",
                    "note-off",
                    "--velocity",
                    "0",
                    "--",
                    "python",
                    "script.py",
                    "--port",
                    "child-port",
                ]
            )
            run.assert_called_once()
        with patch("discofloor.launcher.Launcher") as cls:
            commands.main(
                [
                    "--port",
                    "midi-input",
                    "launcher",
                    "--note",
                    "36",
                    "--event",
                    "both",
                    "--",
                    "echo",
                    "{event}",
                ]
            )
            args = cls.call_args
            self.assertEqual(args.kwargs["port"], "midi-input")
            self.assertEqual(args.args[0][0].trigger.event, "both")
            self.assertEqual(args.args[0][0].command, ("echo", "{event}"))

    def test_invalid_filters_never_open_midi_or_run_commands(self):
        cases = [
            ["--note", "128", "--", "echo"],
            ["--cc", "1", "--event", "note-off", "--", "echo"],
            ["--note", "1", "--velocity", "128", "--", "echo"],
            ["--note", "1", "--min-velocity", "100", "--max-velocity", "90", "--", "echo"],
            ["--note", "1", "--cooldown", "nan", "--", "echo"],
            ["--note", "1"],
        ]
        for argv in cases:
            with (
                patch("mido.Backend") as backend,
                patch("discofloor.launcher.subprocess.Popen") as spawn,
                redirect_stderr(io.StringIO()),
                self.assertRaises(SystemExit),
            ):
                commands.main(["launcher", *argv])
            backend.assert_not_called()
            spawn.assert_not_called()

    def test_listening_opens_input_only_and_dispatches_both_events(self):
        backend = MagicMock()
        backend.get_input_names.return_value = ["SMC-PAD Pocket Bluetooth"]
        incoming = backend.open_input.return_value.__enter__.return_value
        incoming.iter_pending.side_effect = [
            [
                mido.Message("note_on", note=36, velocity=100),
                mido.Message("note_on", note=36, velocity=0),
            ],
            KeyboardInterrupt,
        ]
        process = MagicMock(pid=42)
        process.poll.return_value = None
        bindings = [
            Binding(Trigger("note", 36, event="note-on"), ("echo", "press")),
            Binding(Trigger("note", 36, event="note-off"), ("echo", "release")),
        ]
        with (
            patch("discofloor.launcher.subprocess.Popen", return_value=process) as spawn,
            redirect_stdout(io.StringIO()),
            self.assertRaises(KeyboardInterrupt),
        ):
            Launcher(bindings, backend=backend).run()
        self.assertEqual(
            [call.args[0] for call in spawn.call_args_list],
            [["echo", "press"], ["echo", "release"]],
        )
        backend.open_output.assert_not_called()
        process.terminate.assert_not_called()
        process.kill.assert_not_called()

    def test_dry_run_dispatches_without_processes(self):
        binding = Binding(Trigger("note", 36, event="both"), ("echo", "{event}"))
        state = RunningBinding(binding, dry_run=True)
        with (
            patch("discofloor.launcher.subprocess.Popen") as spawn,
            redirect_stdout(io.StringIO()) as out,
        ):
            state.receive(mido.Message("note_on", note=36, velocity=100), 0)
            state.receive(mido.Message("note_off", note=36, velocity=0), 0.01)
        spawn.assert_not_called()
        self.assertEqual(out.getvalue().count("Would launch:"), 2)
        self.assertIn("note-off", out.getvalue())
