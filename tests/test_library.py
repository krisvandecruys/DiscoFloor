"""Lossless model editing and addressing against captured device backups."""

import unittest
from pathlib import Path
from unittest.mock import patch

from discofloor import Calibration, Color, Control, PadType, Pocket, Preset, Runtime
from discofloor.protocol import decode

ROOT = Path(__file__).parent / "fixtures"


class LibraryTests(unittest.TestCase):
    def setUp(self):
        self.data = (ROOT / "persistence-backup-preset1.spp").read_bytes()
        self.preset = Preset(self.data)

    def test_lossless_roundtrip_and_no_changes(self):
        self.assertEqual(self.preset.to_bytes(), self.data)
        self.assertEqual(self.preset.messages(slot=1), [])
        calibration = (ROOT / "calibration-persistence-backup.bin").read_bytes()
        for offset in range(0, 160, 10):
            record = Calibration.from_bytes(calibration[offset : offset + 10])
            self.assertEqual(record.to_bytes(), calibration[offset : offset + 10])
        runtime = bytes.fromhex("a0 00 33 01 00 01 7b 00")
        self.assertEqual(Runtime.from_bytes(runtime).to_bytes(), runtime)

    def test_color_edit_preserves_every_other_byte(self):
        color = Color.from_hex("#012345")
        self.preset.bank(3).pad(16).color = color
        expected = bytearray(self.data)
        expected[1227:1230] = bytes.fromhex("01 23 45")
        self.assertEqual(self.preset.to_bytes(), bytes(expected))
        messages = self.preset.messages(slot=4)
        self.assertEqual(len(messages), 1)
        packet = decode(messages[0])
        self.assertEqual(packet["offset"], 3 * 2931 + 1227)
        self.assertEqual(packet["data"], "01 23 45")

    def test_human_channel_and_message_types(self):
        pad = self.preset.bank(7).pad(16)
        pad.note(60, channel=16, min_velocity=20, max_velocity=100)
        self.assertEqual(pad.channel, 16)
        self.assertEqual(pad.type, PadType.NOTE)
        self.assertEqual(self.preset.to_bytes()[2912 - 26 + 1], 15)
        pad.program(7, bank_msb=2, bank_lsb=3)
        self.assertEqual((pad.number, pad.value1, pad.value2), (3, 2, 7))
        pad.custom(bytes.fromhex("f0 7d 11 22 f7"))
        self.assertEqual(pad.custom_payload, bytes.fromhex("f0 7d 11 22 f7"))
        self.assertEqual(pad.type, PadType.CUSTOM)

    def test_validation_is_atomic_for_note(self):
        pad = self.preset.bank(1).pad(1)
        with self.assertRaises(ValueError):
            pad.note(60, min_velocity=100, max_velocity=20)
        self.assertEqual(self.preset.to_bytes(), self.data)
        for bad in (0, 17, True):
            with self.assertRaises(ValueError):
                pad.channel = bad

    def test_bank_and_preset_settings(self):
        self.preset.bank(2).fill(Color(1, 2, 3))
        self.assertEqual([p.color for p in self.preset.bank(2)], [Color(1, 2, 3)] * 16)
        self.preset.active_bank = 7
        self.preset.velocity_curve = 4
        self.preset.aftertouch = False
        self.preset.assign_control(16, Control.LATCH)
        self.assertEqual(self.preset.to_bytes()[2927:], bytes([8, 6, 3, 0]))

    def test_explicit_transport_never_saves_implicitly(self):
        with patch("discofloor.device.protocol.send_messages") as send:
            device = Pocket("SINCO SMC-PAD Pocket-Private")
            send.assert_not_called()
            device.upload(self.preset, slot=2)
            messages = send.call_args.args[0]
            self.assertEqual([decode(m)["count"] for m in messages], [1024, 1024, 883])
            device.save()
            self.assertEqual(decode(send.call_args.args[0][0])["count"], 0)


class BracketTests(unittest.TestCase):
    def test_local_brackets_and_legacy_calls(self):
        preset = Preset.load(ROOT / "persistence-backup-preset1.spp")
        preset.bank[1].pad[16].color = Color(1, 2, 3)
        self.assertEqual(preset.bank(1).pad(16).color, Color(1, 2, 3))
        with self.assertRaises(ValueError):
            preset.bank[0]

    def test_live_full_id_and_brackets(self):
        from discofloor import PadId

        with patch("discofloor.device.protocol.send_messages") as send:
            device = Pocket("SINCO SMC-PAD Pocket-Private")
            bank = device.preset[2].bank
            current_bank = bank[1]
            send.assert_not_called()
            current_bank.pad[16].color = Color(1, 2, 3)
            packet = decode(send.call_args.args[0][0])
            self.assertEqual((packet["offset"], packet["count"]), (2931 + 15 * 26 + 5, 3))
            device.pad[PadId(4, 7, 16)].color = Color(4, 5, 6)
            self.assertEqual(decode(send.call_args.args[0][0])["offset"], 3 * 2931 + 2912 - 26 + 5)
            device.pad[4, 7, 16].color = Color(7, 8, 9)
            self.assertEqual(decode(send.call_args.args[0][0])["data"], "07 08 09")
            for call in send.call_args_list:
                self.assertEqual(len(call.args[0]), 1)
                self.assertEqual(decode(call.args[0][0])["count"], 3)
            with self.assertRaises(AttributeError):
                _ = current_bank.pad[1].color


class EditBlockTests(unittest.TestCase):
    def test_single_write_only_after_successful_exit(self):
        original = (ROOT / "persistence-backup-preset1.spp").read_bytes()[832:1248]
        with patch("discofloor.transport.Session") as session_class:
            session = session_class.return_value.__enter__.return_value
            session.request.return_value = original
            device = Pocket()
            with device.preset[1].bank[3].edit() as bank:
                bank.fill(Color(1, 2, 3))
                bank.pad[1].note(60, channel=16)
                self.assertEqual(session.request.call_count, 1)  # read only inside block
            self.assertEqual(session.request.call_count, 2)
            packet = decode(session.request.call_args.args[0])
            self.assertEqual((packet["offset"], packet["count"]), (832, 416))
            updated = bytes.fromhex(packet["data"])
            self.assertEqual(updated[:3], bytes([0, 15, 60]))
            self.assertEqual(updated[5:8], bytes([1, 2, 3]))
            self.assertEqual(updated[10:26], original[10:26])

    def test_exception_and_unchanged_block_do_not_write(self):
        with patch("discofloor.transport.Session") as session_class:
            session = session_class.return_value.__enter__.return_value
            session.request.return_value = bytes(416)
            device = Pocket()
            with self.assertRaises(RuntimeError):
                with device.preset[1].bank[1].edit() as bank:
                    bank.pad[1].color = Color(1, 2, 3)
                    raise RuntimeError("cancel")
            self.assertEqual(session.request.call_count, 1)
            session.request.reset_mock()
            with device.preset[1].bank[1].edit():
                pass
            self.assertEqual(session.request.call_count, 1)


if __name__ == "__main__":
    unittest.main()
