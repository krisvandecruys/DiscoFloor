"""Grouped Python API behavior without MIDI hardware."""

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from discofloor import Color, Control, Pocket
from discofloor.defaults import factory_preset, reset_all_messages
from discofloor.protocol import decode


class ApiTests(unittest.TestCase):
    def test_note_repeat_context_batches_and_preserves_unknowns(self):
        with patch("discofloor.api.Session") as cls:
            session = cls.return_value.__enter__.return_value
            session.request.side_effect = [bytes.fromhex("a000330100017e02"), b""]
            device = Pocket()
            with device.note_repeat.edit() as settings:
                settings.tempo = 110
                settings.time = "1/16"
                settings.sync = True
                settings.latch = False
                self.assertEqual(session.request.call_count, 1)
            packet = decode(session.request.call_args.args[0])
            self.assertEqual((packet["register"], packet["data"]), (4, "6e 00 33 04 01 00 7e 02"))

    def test_note_repeat_exception_invalid_and_noop_do_not_write(self):
        for action in ("noop", "raise", "invalid"):
            with patch("discofloor.api.Session") as cls:
                session = cls.return_value.__enter__.return_value
                session.request.return_value = bytes.fromhex("a000330100010000")
                device = Pocket()
                try:
                    with device.note_repeat.edit() as settings:
                        if action == "raise":
                            settings.tempo = 110
                            raise RuntimeError("cancel")
                        if action == "invalid":
                            settings.sync = 1
                except RuntimeError, TypeError:
                    pass
                self.assertEqual(session.request.call_count, 1)

    def test_live_note_repeat_property_uses_validated_edit(self):
        with patch("discofloor.api.Session") as cls:
            session = cls.return_value.__enter__.return_value
            session.request.side_effect = [bytes.fromhex("a000330100010000"), b""]
            Pocket().note_repeat.sync = True
            self.assertEqual(
                decode(session.request.call_args.args[0])["data"], "a0 00 33 01 01 01 00 00"
            )

    def test_globe_explicit_address_batches_three_fields(self):
        with patch("discofloor.api.Session") as cls:
            session = cls.return_value.__enter__.return_value
            session.request.side_effect = [bytes([2, 1, 1]), b""]
            with Pocket().globe[2].edit() as globe:
                globe.bank = 7
                globe.curve = 4
                globe.aftertouch = False
            packet = decode(session.request.call_args.args[0])
            self.assertEqual(
                (packet["register"], packet["offset"], packet["data"]), (5, 2931 + 2928, "06 03 00")
            )
            self.assertEqual(session.request.call_count, 2)

    def test_globe_active_default_and_bool_validation(self):
        with patch("discofloor.api.Session") as cls:
            session = cls.return_value.__enter__.return_value
            session.request.side_effect = [bytes.fromhex("a000330100010002"), bytes([2, 1, 1]), b""]
            Pocket().globe.aftertouch = False
            self.assertEqual(decode(session.request.call_args.args[0])["offset"], 2 * 2931 + 2928)
        with patch("discofloor.api.Session") as cls:
            session = cls.return_value.__enter__.return_value
            session.request.return_value = bytes([2, 1, 1])
            with self.assertRaises(TypeError):
                Pocket().globe[1].aftertouch = 0
            self.assertEqual(session.request.call_count, 1)

    def test_local_globe_changes_are_lossless_and_local(self):
        preset = factory_preset(1)
        original = preset.to_bytes()
        preset.globe.bank = 7
        preset.globe.curve = 4
        preset.globe.aftertouch = False
        self.assertEqual(preset.to_bytes()[:-3], original[:-3])
        self.assertEqual(preset.to_bytes()[-3:], bytes([6, 3, 0]))

    def test_pad_methods_and_control_mode_scope(self):
        with patch("discofloor.transport.Session") as cls:
            session = cls.return_value.__enter__.return_value
            session.request.return_value = factory_preset(1).to_bytes()[:416]
            device = Pocket()
            device.pad[1, 1, 2].note(60, channel=16)
            packet = decode(session.request.call_args.args[0])
            data = bytes.fromhex(packet["data"])
            self.assertEqual(data[26:29], bytes([0, 15, 60]))
        with patch("discofloor.device.protocol.send_messages") as send:
            pad = Pocket().pad[2, 7, 9]
            pad.control = Control.NOTE_REPEAT
            packet = decode(send.call_args.args[0][0])
            self.assertEqual((packet["offset"], packet["data"]), (2931 + 2920, "01"))
            pad.mode = "pad"
            self.assertEqual(decode(send.call_args.args[0][0])["data"], "00")

    def test_pad_edit_pushes_one_bank(self):
        with patch("discofloor.transport.Session") as cls:
            session = cls.return_value.__enter__.return_value
            session.request.return_value = bytes(416)
            with Pocket().pad[3, 2, 4].edit() as pad:
                pad.color = Color(1, 2, 3)
                pad.note(60, channel=10)
            self.assertEqual(session.request.call_count, 2)
            self.assertEqual(decode(session.request.call_args.args[0])["offset"], 2 * 2931 + 416)

    def test_globe_calibration_prevalidates_entire_range(self):
        with patch("discofloor.device.protocol.send_messages") as send:
            device = Pocket()
            with self.assertRaises(ValueError):
                device.globe.calibrate([1, 17], level=3)
            send.assert_not_called()
            device.globe.calibrate([1, 2, 1], level=3)
            self.assertEqual(send.call_count, 2)
            for call in send.call_args_list:
                packets = [decode(m) for m in call.args[0]]
                self.assertEqual(packets[0]["register"], 3)
                self.assertEqual(packets[1]["count"], 0)

    def test_preset_selection_reset_and_save_views(self):
        with patch("discofloor.device.protocol.send_messages") as send:
            device = Pocket()
            device.preset.active = 2
            self.assertEqual(decode(send.call_args.args[0][0])["data"], "01")
            device.preset[3].reset()
            self.assertEqual(
                [decode(m)["offset"] for m in send.call_args.args[0]],
                [2 * 2931, 2 * 2931 + 1024, 2 * 2931 + 2048],
            )
            device.preset.save()
            self.assertEqual(decode(send.call_args.args[0][0])["count"], 0)
            device.preset.reset_all()
            self.assertEqual(send.call_args.args[0], reset_all_messages())

    def test_preset_read_export_and_import(self):
        data = factory_preset(3).to_bytes()
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "preset.spp"
            with patch("discofloor.api.Session") as cls:
                cls.return_value.__enter__.return_value.request.side_effect = [
                    data[:1009],
                    data[1009:2018],
                    data[2018:],
                ]
                Pocket().preset[3].export(path)
                self.assertEqual(path.read_bytes(), data)
            with patch("discofloor.device.protocol.send_messages") as send:
                Pocket().preset[2].import_file(path)
                packets = [decode(m) for m in send.call_args.args[0]]
                self.assertEqual(b"".join(bytes.fromhex(p["data"]) for p in packets), data)
                self.assertTrue(all(p["register"] == 5 and p["count"] > 0 for p in packets))
