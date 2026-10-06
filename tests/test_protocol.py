"""Validate the encoder using independently captured vendor-app messages."""

import json
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from discofloor.protocol import (
    build_packet,
    build_read,
    build_write,
    color_message,
    config_messages,
    decode,
    send_messages,
)

ROOT = Path(__file__).parent / "fixtures"


class ProtocolTests(unittest.TestCase):
    def test_complete_vendor_capture(self):
        frames = json.loads((ROOT / "spec-restoration-capture.json").read_text())
        checked = 0
        for frame in frames:
            if frame.get("type") == 34:
                self.assertEqual(
                    build_write(frame["offset"], bytes.fromhex(frame["data"]), frame["register"]),
                    bytes.fromhex(frame["sysex"]),
                )
                checked += 1
            elif frame.get("type") == 35 and not frame["data"]:
                self.assertEqual(
                    build_read(frame["register"], frame["offset"], frame["count"]),
                    bytes.fromhex(frame["sysex"]),
                )
        self.assertGreater(checked, 100)

    def test_all_preset_uploads_and_restoration(self):
        frames = json.loads((ROOT / "spec-restoration-capture.json").read_text())
        replies = [
            f for f in frames if f.get("type") == 35 and f.get("register") == 5 and f.get("data")
        ]
        restored = bytearray(11724)
        for frame in replies:
            data = bytes.fromhex(frame["data"])
            restored[frame["offset"] : frame["offset"] + len(data)] = data
        self.assertEqual(bytes(restored), (ROOT / "spec-start-device.bin").read_bytes())
        captured = {f["sysex"] for f in frames if f.get("type") == 34}
        for preset in range(1, 5):
            data = (ROOT / f"spec-backup-preset{preset}.spp").read_bytes()
            for message in config_messages(data, preset):
                self.assertIn(message.hex(" "), captured)

    def test_factory_reset_prediction(self):
        frames = json.loads((ROOT / "factory-reset-capture.json").read_text())
        predictions = json.loads((ROOT / "factory-reset-predictions.json").read_text())
        writes = [f for f in frames if f.get("type") == 34]
        self.assertEqual(len(writes), 13)
        self.assertEqual([f["sysex"] for f in writes[:12]], predictions)
        self.assertEqual(
            build_write(0, bytes.fromhex("78 00 32 04 00 00 00 00"), 4),
            bytes.fromhex(writes[12]["sysex"]),
        )

    def test_discovery(self):
        self.assertEqual(build_packet(0x11), bytes.fromhex("F0 00 32 45 00 00 00 40 7F F7"))

    def test_vendor_color_messages(self):
        frames = json.loads((ROOT / "validated-color-capture.json").read_text())
        self.assertGreaterEqual(len(frames), 17)
        for frame in frames:
            pad = (frame["offset"] - 837) // 26 + 1
            message = color_message(3, pad, tuple(bytes.fromhex(frame["data"])))
            self.assertEqual(message, bytes.fromhex(frame["sysex"]))

    def test_vendor_config_messages(self):
        frames = json.loads((ROOT / "import-capture.json").read_text())[-3:]
        data = (ROOT / "baseline-preset1.spp").read_bytes()
        self.assertEqual(
            [(f["offset"], f["count"]) for f in frames], [(0, 1024), (1024, 1024), (2048, 883)]
        )
        for frame in frames:
            chunk = data[frame["offset"] : frame["offset"] + frame["count"]]
            self.assertEqual(build_write(frame["offset"], chunk), bytes.fromhex(frame["sysex"]))

    def test_prediction_made_before_ui_change(self):
        self.assertEqual(
            color_message(3, 16, (254, 27, 1)), (ROOT / "prediction-pad16-red.syx").read_bytes()
        )

    def test_save_message(self):
        captured = "F0 00 32 09 41 00 00 40 02 00 00 00 00 00 00 00 00 7A 01 F7"
        self.assertEqual(build_write(0, b""), bytes.fromhex(captured))

    def test_corrupted_message_rejected(self):
        message = bytearray(color_message(3, 1, (254, 27, 1)))
        message[-3] ^= 1
        with self.assertRaises(ValueError):
            decode(bytes(message))

    def test_sender_waits_for_acknowledgements(self):
        port = "SINCO SMC-PAD Pocket-Private"
        pending = []
        sent = []
        ack = bytes.fromhex("F0 00 32 01 08 00 00 00 00 7F 01 F7")

        class FakePort:
            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

            def iter_pending(self):
                replies = pending.copy()
                pending.clear()
                return iter(replies)

            def send(self, message):
                sent.append(message)
                pending.append(SimpleNamespace(type="sysex", bytes=lambda: list(ack)))

        backend = SimpleNamespace(
            get_input_names=lambda: [port],
            get_output_names=lambda: [port],
            open_input=lambda name: FakePort(),
            open_output=lambda name, autoreset: FakePort(),
            message_from_bytes=bytes,
        )
        messages = [color_message(3, 1, (255, 0, 0)), build_write(0, b"")]
        send_messages(messages, port, backend=backend)
        self.assertEqual(sent, messages)
        self.assertFalse(pending)

        # No ACK means stop after the first write, with no automatic retry.
        class NoAck(FakePort):
            def send(self, message):
                sent.append(message)

        backend.open_output = lambda name, autoreset: NoAck()
        sent.clear()
        with patch("time.monotonic", side_effect=[0, 3]):
            with self.assertRaises(TimeoutError):
                send_messages(messages, port, backend=backend)
        self.assertEqual(sent, messages[:1])


if __name__ == "__main__":
    unittest.main()
