"""Live example behavior, without opening real MIDI ports."""

import io
import unittest
from contextlib import redirect_stdout
from unittest.mock import patch

from discofloor import commands
from discofloor.protocol import decode


class CommandTests(unittest.TestCase):
    def test_globe_bank_set_resolves_active_preset(self):
        with (
            patch("discofloor.commands.Session") as cls,
            patch("discofloor.commands.Pocket") as pocket,
            redirect_stdout(io.StringIO()),
        ):
            cls.return_value.__enter__.return_value.request.side_effect = [
                bytes.fromhex("a0 00 33 01 00 01 00 01"),
                bytes([0, 0, 0]),
            ]
            commands.globe(["--bank", "3"])
            packet = decode(pocket.return_value.send.call_args.args[0][0])
            self.assertEqual((packet["offset"], packet["data"]), (2931 + 2928, "02"))
            pocket.return_value.save.assert_not_called()

    def test_preset_get_never_writes(self):
        with (
            patch("discofloor.commands.Session") as cls,
            patch("discofloor.commands.Pocket") as pocket,
            redirect_stdout(io.StringIO()) as output,
        ):
            cls.return_value.__enter__.return_value.request.return_value = bytes([0] * 7 + [3])
            commands.preset([])
            pocket.return_value.select_preset.assert_not_called()
            pocket.return_value.send.assert_not_called()
            self.assertEqual(output.getvalue().strip(), "4")

    def test_fill_uses_edit_context_and_defaults_to_active_selection(self):
        with (
            patch("discofloor.commands.Session") as cls,
            patch("discofloor.commands.Pocket") as pocket,
            redirect_stdout(io.StringIO()),
        ):
            cls.return_value.__enter__.return_value.request.side_effect = [
                bytes([0] * 7 + [1]),
                b"\x02",
            ]
            commands.pad(["1-16", "--color", "ff0044"])
            pocket.return_value.preset.__getitem__.assert_called_once_with(2)
            live_bank = pocket.return_value.preset.__getitem__.return_value.bank
            live_bank.__getitem__.assert_called_once_with(3)
            edited = live_bank.__getitem__.return_value.edit.return_value.__enter__.return_value
            self.assertEqual(edited.pad.__getitem__.call_count, 16)
            pocket.return_value.save.assert_not_called()


class ShowTests(unittest.TestCase):
    def test_show_reads_active_slot_bank_and_control_assignments(self):
        records = bytearray(416)
        for index in range(16):
            records[index * 26 : index * 26 + 5] = bytes([0, 9, 36 + index, 1, 127])
        settings = bytearray(19)
        settings[15], settings[16] = 1, 2
        with patch("discofloor.commands.Session") as cls, redirect_stdout(io.StringIO()) as output:
            session = cls.return_value.__enter__.return_value
            session.request.side_effect = [bytes([0] * 7 + [1]), bytes(settings), bytes(records)]
            commands.main(["show"])
        requests = [decode(call.args[0]) for call in session.request.call_args_list]
        self.assertEqual(
            [(r["type"], r["register"], r["offset"], r["count"]) for r in requests],
            [(35, 4, 0, 8), (35, 5, 2931 + 2912, 19), (35, 5, 2931 + 832, 416)],
        )
        self.assertIn("Preset 2, bank 3", output.getvalue())
        self.assertIn("Channel 10: Note 36", output.getvalue())
        self.assertIn("Pad 16: Control: Note Repeat", output.getvalue())

    def test_type_descriptions(self):
        self.assertIn("toggle values 0 / 127", commands.describe_pad(bytes([1, 0, 74, 0, 127]), 0))
        self.assertIn("press 127, release 0", commands.describe_pad(bytes([2, 0, 74, 0, 127]), 0))
        self.assertIn(
            "Program 7; bank MSB 2, LSB 3", commands.describe_pad(bytes([3, 0, 3, 2, 7]), 0)
        )
        record = bytes([4, 0, 0, 0, 0, 0, 0, 0, 0, 5]) + bytes.fromhex("f0 7d 11 22 f7") + bytes(11)
        self.assertIn("Custom: F0 7D 11 22 F7", commands.describe_pad(record, 0))
