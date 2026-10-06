"""Live example behavior, without opening real MIDI ports."""

import io
import unittest
from contextlib import redirect_stdout
from unittest.mock import patch

from discofloor import commands
from discofloor.protocol import decode


class CommandTests(unittest.TestCase):
    def test_bank_set_resolves_active_preset_and_reads_back(self):
        with (
            patch("discofloor.commands.Session") as cls,
            patch("discofloor.commands.Pocket") as pocket,
            redirect_stdout(io.StringIO()) as output,
        ):
            cls.return_value.__enter__.return_value.request.side_effect = [
                bytes.fromhex("a0 00 33 01 00 01 00 01"),
                b"\x02",
            ]
            commands.bank(["3"])
            packet = decode(pocket.return_value.send.call_args.args[0][0])
            self.assertEqual((packet["offset"], packet["data"]), (2931 + 2928, "02"))
            pocket.return_value.save.assert_not_called()
            self.assertEqual(output.getvalue().strip(), "3")

    def test_preset_get_never_writes(self):
        with (
            patch("discofloor.commands.Session") as cls,
            patch("discofloor.commands.Pocket") as pocket,
            redirect_stdout(io.StringIO()) as output,
        ):
            cls.return_value.__enter__.return_value.request.return_value = bytes([0] * 7 + [3])
            commands.preset([])
            pocket.return_value.select_preset.assert_not_called()
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
            commands.fill(["ff0044"])
            pocket.return_value.preset.__getitem__.assert_called_once_with(2)
            live_bank = pocket.return_value.preset.__getitem__.return_value.bank
            live_bank.__getitem__.assert_called_once_with(3)
            edited = live_bank.__getitem__.return_value.edit.return_value.__enter__.return_value
            self.assertEqual(edited.fill.call_args.args[0].to_bytes(), bytes.fromhex("ff0044"))
            pocket.return_value.save.assert_not_called()
