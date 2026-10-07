"""Pad-range validation and aftertouch read/write addressing."""

import argparse
import io
import unittest
from contextlib import redirect_stdout
from unittest.mock import patch

from discofloor import commands
from discofloor.protocol import decode
from discofloor.selections import pad_range


class SelectionTests(unittest.TestCase):
    def test_inclusive_ranges_and_deduplication(self):
        self.assertEqual(pad_range("1-4,7,9-12"), [1, 2, 3, 4, 7, 9, 10, 11, 12])
        self.assertEqual(pad_range(" 3-5,4, 1 "), [3, 4, 5, 1])
        for value in ("", "0", "17", "4-1", "1,", "a", "1-4-7"):
            with self.assertRaises(argparse.ArgumentTypeError):
                pad_range(value)

    def test_aftertouch_active_slot_write(self):
        with (
            patch("discofloor.commands.Session") as cls,
            patch("discofloor.commands.Pocket") as device,
            redirect_stdout(io.StringIO()) as out,
        ):
            cls.return_value.__enter__.return_value.request.side_effect = [
                bytes([0] * 7 + [2]),
                bytes([2, 0, 0]),
            ]
            commands.main(["globe", "--aftertouch"])
            packet = decode(device.return_value.send.call_args.args[0][0])
            self.assertEqual(
                (packet["type"], packet["offset"], packet["data"]), (34, 2 * 2931 + 2930, "01")
            )
            self.assertIn("preset 3, across all pads/banks", out.getvalue())

    def test_range_color_is_staged_in_one_edit_block(self):
        with patch("discofloor.commands.Pocket") as cls, redirect_stdout(io.StringIO()):
            commands.pad(["1-4,7,9-12", "--color", "ff0044", "--preset", "2", "--bank", "3"])
            bank = cls.return_value.preset.__getitem__.return_value.bank.__getitem__.return_value
            bank.edit.assert_called_once()
            edited = bank.edit.return_value.__enter__.return_value
            numbers = [call.args[0] for call in edited.pad.__getitem__.call_args_list]
            self.assertEqual(numbers, [1, 2, 3, 4, 7, 9, 10, 11, 12])
            cls.return_value.save.assert_not_called()
