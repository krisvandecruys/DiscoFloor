"""Bank-frame preservation and validated read/ACK transport without hardware."""

import unittest
from pathlib import Path
from types import SimpleNamespace

from discofloor import Color, Preset
from discofloor.protocol import build_packet, build_read, build_write, decode
from discofloor.transport import Session


class DiscoTests(unittest.TestCase):
    def test_frame_preserves_non_color_settings(self):
        data = (Path(__file__).parent / "fixtures" / "persistence-backup-preset1.spp").read_bytes()[
            832:1248
        ]
        colors = [Color(i, 255 - i, 32) for i in range(16)]
        backing = bytearray(2931)
        backing[832:1248] = data
        preset = Preset(bytes(backing))
        for number, color in enumerate(colors, start=1):
            preset.bank[3].pad[number].color = color
        edited = preset.to_bytes()[832:1248]
        for index in range(416):
            if index % 26 not in (5, 6, 7):
                self.assertEqual(edited[index], data[index])
        packet = decode(build_write(832, edited))
        self.assertEqual(packet["count"], 416)
        self.assertEqual(bytes.fromhex(packet["data"]), edited)

    def test_read_requires_complete_matching_bank(self):
        pending, sent = [], []
        data = bytes(416)
        body = b"\x05" + (832).to_bytes(4, "little") + (416).to_bytes(3, "little") + data
        reply = build_packet(0x23, body)

        class Port:
            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

            def iter_pending(self):
                result = pending.copy()
                pending.clear()
                return iter(result)

            def send(self, message):
                sent.append(message)
                pending.append(SimpleNamespace(type="sysex", bytes=lambda: list(reply)))

        name = "SINCO SMC-PAD Pocket-Private"
        backend = SimpleNamespace(
            get_input_names=lambda: [name],
            get_output_names=lambda: [name],
            open_input=lambda name: Port(),
            open_output=lambda name, autoreset: Port(),
            message_from_bytes=bytes,
        )
        with Session(name, backend) as session:
            self.assertEqual(session.request(build_read(5, 832, 416), read_offset=832), data)
            with self.assertRaises(RuntimeError):
                session.request(build_read(5, 0, 416), read_offset=0)
        self.assertEqual(len(sent), 2)


class RestoreTests(unittest.TestCase):
    def test_restores_snapshot_on_completion_and_ctrl_c(self):
        from unittest.mock import MagicMock, patch

        from discofloor import Pocket
        from discofloor.disco import saved_settings

        args = SimpleNamespace(preset=2, bank=3)
        original = bytes(i % 256 for i in range(416))
        for interrupted in (False, True):
            device = Pocket()
            device.send = MagicMock()
            with patch("discofloor.transport.Session") as cls:
                cls.return_value.__enter__.return_value.request.side_effect = [
                    original,
                    b"\x05",
                    bytes.fromhex("a0 00 33 01 00 01 00 03"),
                ]
                try:
                    with saved_settings(args, device):
                        device.send.assert_not_called()
                        if interrupted:
                            raise KeyboardInterrupt
                except KeyboardInterrupt:
                    pass
            writes = [decode(call.args[0][0]) for call in device.send.call_args_list]
            self.assertEqual(
                [(p["register"], p["offset"], p["count"]) for p in writes],
                [(5, 2931 + 832, 416), (5, 2931 + 2928, 1), (4, 7, 1)],
            )
            self.assertEqual(bytes.fromhex(writes[0]["data"]), original)
            self.assertEqual(writes[1]["data"], "05")
            self.assertEqual(writes[2]["data"], "03")

    def test_failed_snapshot_sends_no_writes(self):
        from unittest.mock import MagicMock, patch

        from discofloor import Pocket
        from discofloor.disco import saved_settings

        device = Pocket()
        device.send = MagicMock()
        with patch("discofloor.transport.Session") as cls:
            cls.return_value.__enter__.return_value.request.side_effect = TimeoutError
            with self.assertRaises(TimeoutError):
                with saved_settings(SimpleNamespace(preset=1, bank=1), device):
                    self.fail("Snapshot failed: demo must not start")
        device.send.assert_not_called()
