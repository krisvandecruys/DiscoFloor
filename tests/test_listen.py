"""Performance-event identification without physical MIDI hardware."""

import io
import unittest
from contextlib import redirect_stdout
from unittest.mock import MagicMock, patch

import mido

from discofloor.listen import choose_input, describe_event, main, matches
from discofloor.ports import MidiPortUnavailable


class ListenTests(unittest.TestCase):
    def records(self):
        data = bytearray(416)
        for index in range(16):
            data[index * 26 : index * 26 + 5] = bytes([0, 9, 36 + index, 1, 127])
        return data

    def test_note_strike_release_and_duplicate_candidates(self):
        records = self.records()
        note = mido.Message("note_on", channel=9, note=36, velocity=100)
        self.assertEqual(matches(note, records, bytes(16)), [1])
        self.assertIn("Pad 1 struck", describe_event(note, records, bytes(16)))
        note.velocity = 0
        self.assertIn("released", describe_event(note, records, bytes(16)))
        records[26:52] = records[:26]
        self.assertIn("Pad 1, 2 (ambiguous)", describe_event(note, records, bytes(16)))
        self.assertEqual(matches(note, records, bytes([1, 1] + [0] * 14)), [])

    def test_channel_cc_and_custom_matching(self):
        records = self.records()
        records[:5] = bytes([2, 3, 74, 0, 127])
        cc = mido.Message("control_change", channel=3, control=74, value=127)
        self.assertEqual(matches(cc, records, bytes(16)), [1])
        cc.channel = 4
        self.assertEqual(matches(cc, records, bytes(16)), [])
        payload = bytes.fromhex("f0 7d 11 22 f7")
        records[:26] = bytes([4] + [0] * 8 + [5]) + payload + bytes(11)
        self.assertEqual(matches(mido.Message.from_bytes(payload), records, bytes(16)), [1])

    def test_performance_port_selection(self):
        bluetooth = "SMC-PAD Pocket Bluetooth"
        usb = "SINCO SMC-PAD Pocket-Master"
        self.assertEqual(choose_input([bluetooth, usb]), usb)
        self.assertEqual(choose_input([bluetooth]), bluetooth)
        with self.assertRaises(MidiPortUnavailable):
            choose_input([bluetooth], usb)

    def test_bluetooth_listening_sends_no_sysex(self):
        backend = MagicMock()
        backend.get_input_names.return_value = ["SMC-PAD Pocket Bluetooth"]
        incoming = backend.open_input.return_value.__enter__.return_value
        incoming.iter_pending.side_effect = [
            [mido.Message("note_on", note=60, velocity=100)],
            KeyboardInterrupt,
        ]
        with patch("mido.Backend", return_value=backend), redirect_stdout(io.StringIO()) as output:
            main([])
        backend.open_output.assert_not_called()
        self.assertIn("Pad unknown struck", output.getvalue())
