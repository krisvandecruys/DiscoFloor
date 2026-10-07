"""Missing MIDI ports print useful inventories, without opening hardware."""

import io
import unittest
from contextlib import redirect_stderr
from types import SimpleNamespace
from unittest.mock import patch

from discofloor.disco import main
from discofloor.ports import MidiPortUnavailable, require_ports


class PortTests(unittest.TestCase):
    def test_missing_input_lists_both_directions(self):
        backend = SimpleNamespace(
            get_input_names=lambda: ["Other input"], get_output_names=lambda: ["Pocket output"]
        )
        with self.assertRaises(MidiPortUnavailable) as result:
            require_ports(backend, "Missing")
        self.assertIn("Found MIDI inputs:\n  Other input", str(result.exception))
        self.assertIn("Found MIDI outputs:\n  Pocket output", str(result.exception))

    def test_empty_inventory_and_demo_clean_exit(self):
        backend = SimpleNamespace(get_input_names=lambda: [], get_output_names=lambda: [])
        with self.assertRaises(MidiPortUnavailable) as result:
            require_ports(backend, "Missing")
        self.assertEqual(str(result.exception).count("(none)"), 2)
        with patch("discofloor.disco.Pocket") as pocket, redirect_stderr(io.StringIO()) as output:
            pocket.return_value.preset.__getitem__.return_value.bank.__getitem__.return_value.snapshot.side_effect = result.exception
            with self.assertRaises(SystemExit) as exit_status:
                main([])
        self.assertEqual(exit_status.exception.code, 1)
        self.assertIn("Found MIDI inputs:", output.getvalue())
        self.assertNotIn("Traceback", output.getvalue())


class DefaultPortTests(unittest.TestCase):
    def backend(self, inputs, outputs=None):
        return SimpleNamespace(
            get_input_names=lambda: inputs,
            get_output_names=lambda: inputs if outputs is None else outputs,
        )

    def test_usb_priority_and_bluetooth_fallback(self):
        usb = "SINCO SMC-PAD Pocket-Private"
        bluetooth = "SMC-PAD Pocket Bluetooth"
        self.assertEqual(require_ports(self.backend([bluetooth, usb])), usb)
        self.assertEqual(require_ports(self.backend([bluetooth])), bluetooth)
        self.assertEqual(require_ports(self.backend([usb, bluetooth], [bluetooth])), bluetooth)

    def test_explicit_port_does_not_fall_back(self):
        backend = self.backend(["SMC-PAD Pocket Bluetooth"])
        with self.assertRaises(MidiPortUnavailable):
            require_ports(backend, "SINCO SMC-PAD Pocket-Private")
        self.assertEqual(require_ports(self.backend(["Custom"]), "Custom"), "Custom")

    def test_global_and_subcommand_overrides_are_forwarded(self):
        from discofloor.commands import main

        for arguments in (["--port", "Custom", "demo"], ["demo", "--port", "Custom"]):
            with patch("discofloor.disco.main") as demo:
                main(arguments)
                self.assertEqual(demo.call_args.args[0], ["--port", "Custom"])
