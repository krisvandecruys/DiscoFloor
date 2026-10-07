"""CLI scope, byte preservation, and validation without hardware access."""

import io
import json
import tempfile
import unittest
from contextlib import contextmanager, redirect_stderr, redirect_stdout
from pathlib import Path
from unittest.mock import patch

from discofloor import commands
from discofloor.model import Preset
from discofloor.protocol import decode


class SettingsTests(unittest.TestCase):
    def test_runtime_preserves_active_preset_and_unknown_byte(self):
        with patch("discofloor.commands.Session") as cls, redirect_stdout(io.StringIO()):
            session = cls.return_value.__enter__.return_value
            session.request.side_effect = [bytes.fromhex("a000330100017e02"), b""]
            commands.main(["note-repeat", "--tempo", "110", "--time", "1/16T", "--sync"])
            packet = decode(session.request.call_args_list[1].args[0])
            self.assertEqual(
                (packet["register"], packet["offset"], packet["data"]),
                (4, 0, "6e 00 33 05 01 01 7e 02"),
            )

    def test_runtime_read_only_and_noop_do_not_write(self):
        for argv in ([], ["--tempo", "160"]):
            with patch("discofloor.commands.Session") as cls, redirect_stdout(io.StringIO()):
                session = cls.return_value.__enter__.return_value
                session.request.return_value = bytes.fromhex("a000330100010000")
                commands.note_repeat(argv)
                self.assertEqual(session.request.call_count, 1)

    def test_globe_curve_and_pad_control_addresses(self):
        with (
            patch("discofloor.commands.Session") as cls,
            patch("discofloor.commands.Pocket") as pocket,
            redirect_stdout(io.StringIO()),
        ):
            cls.return_value.__enter__.return_value.request.side_effect = [bytes(3)]
            commands.globe(["--preset", "2", "--curve", "4"])
            commands.pad(["9-10", "--preset", "2", "--control", "note-repeat"])
            commands.pad(["16", "--preset", "2", "--mode", "pad"])
            packets = [
                decode(packet)
                for call in pocket.return_value.send.call_args_list
                for packet in call.args[0]
            ]
            self.assertEqual(
                [(p["offset"], p["data"]) for p in packets],
                [
                    (2931 + 2929, "03"),
                    (2931 + 2920, "01"),
                    (2931 + 2921, "01"),
                    (2931 + 2927, "00"),
                ],
            )
            pocket.return_value.save.assert_not_called()

    def test_pad_modes_preserve_channel_colors_and_unused_bytes(self):
        for flags, prefix in (
            (["--cc", "74", "--momentary", "--off", "1", "--on", "99"], bytes([2, 9, 74, 1, 99])),
            (["--program", "7", "--bank-msb", "2", "--bank-lsb", "3"], bytes([3, 9, 3, 2, 7])),
            (
                ["--note", "60", "--min-velocity", "10", "--max-velocity", "100"],
                bytes([0, 9, 60, 10, 100]),
            ),
        ):
            data = bytearray(2931)
            data[:26] = bytes([0, 9, 36, 0, 127, 10, 20, 30, 255, 0]) + bytes(range(16))
            model = Preset(bytes(data))
            with patch("discofloor.commands.Pocket") as pocket, redirect_stdout(io.StringIO()):
                bank = pocket.return_value.preset.__getitem__.return_value.bank.__getitem__.return_value
                bank.edit.return_value.__enter__.return_value = model.bank[1]
                commands.pad(["1", "--preset", "1", "--bank", "1", *flags])
                self.assertEqual(model.to_bytes()[:5], prefix)
                self.assertEqual(model.to_bytes()[5:26], data[5:26])

    def test_custom_payload(self):
        model = Preset(bytes(2931))
        with patch("discofloor.commands.Pocket") as pocket, redirect_stdout(io.StringIO()):
            pocket.return_value.preset.__getitem__.return_value.bank.__getitem__.return_value.edit.return_value.__enter__.return_value = model.bank[
                1
            ]
            commands.pad(["2", "--preset", "1", "--bank", "1", "--custom", "f0 7d 01 f7"])
        self.assertEqual(model.bank[1].pad[2].custom_payload, bytes.fromhex("f0 7d 01 f7"))

    def test_invalid_inputs_never_open_ports(self):
        cases = [
            ["pad", "1", "--note", "60", "--cc", "74"],
            ["pad", "1", "--on", "127"],
            ["pad", "1", "--custom", "ff " * 17],
            ["globe", "--pads", "1", "--calibration", "3", "--bank", "2"],
            ["pad", "1", "--min-velocity", "100", "--max-velocity", "10"],
            ["note-repeat", "--swing", "101"],
            ["pad", "1-2", "--control", "bad"],
        ]
        for args in cases:
            with (
                patch("discofloor.commands.Session") as session,
                patch("discofloor.commands.Pocket") as pocket,
                redirect_stderr(io.StringIO()),
                self.assertRaises(SystemExit),
            ):
                commands.main(args)
            session.assert_not_called()
            pocket.assert_not_called()

    def test_invalid_existing_velocity_aborts_bank_transaction(self):
        model = Preset(bytes(2931))
        committed = []

        @contextmanager
        def edit():
            yield model.bank[1]
            committed.append(model.to_bytes())

        with (
            patch("discofloor.commands.Pocket") as pocket,
            redirect_stderr(io.StringIO()),
            self.assertRaises(SystemExit),
        ):
            pocket.return_value.preset.__getitem__.return_value.bank.__getitem__.return_value.edit.side_effect = edit
            commands.pad(["1", "--preset", "1", "--bank", "1", "--min-velocity", "100"])
        self.assertEqual(committed, [])

    def test_calibration_only_targets_physical_pads(self):
        with (
            patch("discofloor.commands.Pocket") as pocket,
            patch("discofloor.commands.Session") as session,
            redirect_stdout(io.StringIO()) as out,
        ):
            commands.globe(["--pads", "1-2", "--calibration", "3"])
            self.assertEqual(
                [c.args for c in pocket.return_value.calibrate.call_args_list], [(1,), (2,)]
            )
            self.assertTrue(
                all(c.kwargs == {"level": 3} for c in pocket.return_value.calibrate.call_args_list)
            )
            session.assert_not_called()
            self.assertIn("persisted immediately", out.getvalue())

    def test_preset_export_chunks_and_import(self):
        data = bytes(i % 256 for i in range(2931))
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "preset.spp"
            with (
                patch("discofloor.commands.Session") as cls,
                patch("discofloor.commands.Pocket") as pocket,
                redirect_stdout(io.StringIO()),
            ):
                session = cls.return_value.__enter__.return_value
                session.request.side_effect = [bytes(8), data[:1009], data[1009:2018], data[2018:]]
                commands.preset(["--export", str(path)])
                self.assertEqual(path.read_bytes(), data)
                reads = [decode(c.args[0]) for c in session.request.call_args_list[1:]]
                self.assertEqual(
                    [(r["offset"], r["count"]) for r in reads],
                    [(0, 1009), (1009, 1009), (2018, 913)],
                )
                pocket.return_value.send.assert_not_called()
            with (
                patch("discofloor.commands.Session") as cls,
                patch("discofloor.commands.Pocket") as pocket,
                redirect_stdout(io.StringIO()),
            ):
                cls.return_value.__enter__.return_value.request.return_value = bytes(8)
                commands.preset(["--import", str(path)])
                self.assertEqual(pocket.return_value.upload.call_args.args[0].to_bytes(), data)
                pocket.return_value.save.assert_not_called()

    def test_bank_snapshot_roundtrip(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "bank.json"
            with (
                patch("discofloor.commands.Session") as cls,
                patch("discofloor.commands.Pocket") as pocket,
                redirect_stdout(io.StringIO()),
            ):
                cls.return_value.__enter__.return_value.request.side_effect = [bytes(8), b"\x02"]
                snapshot = pocket.return_value.preset.__getitem__.return_value.bank.__getitem__.return_value.snapshot.return_value
                snapshot.data, snapshot.selected_bank, snapshot.active_preset = (
                    bytes(416),
                    b"\x02",
                    b"\x00",
                )
                commands.globe(["--snapshot", str(path)])
                saved = json.loads(path.read_text())
                self.assertEqual((saved["preset"], saved["bank"]), (1, 3))
                pocket.return_value.send.assert_not_called()
            with (
                patch("discofloor.commands.Pocket") as pocket,
                patch("discofloor.commands.Session") as session,
                redirect_stdout(io.StringIO()),
            ):
                commands.globe(["--restore", str(path)])
                packets = [decode(c.args[0][0]) for c in pocket.return_value.send.call_args_list]
                self.assertEqual(
                    [(p["register"], p["offset"], p["count"]) for p in packets],
                    [(5, 832, 416), (5, 2928, 1), (4, 7, 1)],
                )
                session.assert_not_called()
                pocket.return_value.save.assert_not_called()


class AppOrganizationTests(unittest.TestCase):
    def test_negative_toggles_and_omitted_values(self):
        with patch("discofloor.commands.Session") as cls, redirect_stdout(io.StringIO()):
            session = cls.return_value.__enter__.return_value
            session.request.side_effect = [bytes.fromhex("a000330101017e02"), b""]
            commands.main(["note-repeat", "--no-sync", "--no-latch"])
            packet = decode(session.request.call_args_list[1].args[0])
            self.assertEqual(packet["data"], "a0 00 33 01 00 00 7e 02")
        for flag, expected in (("--aftertouch", "01"), ("--no-aftertouch", "00")):
            with (
                patch("discofloor.commands.Session") as cls,
                patch("discofloor.commands.Pocket") as pocket,
                redirect_stdout(io.StringIO()),
            ):
                cls.return_value.__enter__.return_value.request.return_value = bytes([2, 1, 1])
                commands.main(["globe", "--preset", "2", flag])
                pocket.return_value.select_preset.assert_not_called()
                packet = decode(pocket.return_value.send.call_args.args[0][0])
                self.assertEqual((packet["offset"], packet["data"]), (2931 + 2930, expected))

    def test_full_reset_matches_every_captured_app_write(self):
        from discofloor.defaults import factory_preset, reset_all_messages

        frames = json.loads(
            (Path(__file__).parent / "fixtures" / "factory-reset-capture.json").read_text()
        )
        writes = [f for f in frames if f.get("type") == 34]
        self.assertEqual(reset_all_messages(), [bytes.fromhex(f["sysex"]) for f in writes])
        original = b"".join(bytes.fromhex(f["data"]) for f in writes[:12])
        for slot in range(1, 5):
            self.assertEqual(
                factory_preset(slot).to_bytes(), original[(slot - 1) * 2931 : slot * 2931]
            )

    def test_selected_reset_preserves_other_slots_and_note_repeat(self):
        from discofloor.defaults import factory_preset

        with (
            patch("discofloor.commands.Session") as cls,
            patch("discofloor.commands.Pocket") as pocket,
            redirect_stdout(io.StringIO()),
        ):
            cls.return_value.__enter__.return_value.request.return_value = bytes([0] * 7 + [2])
            commands.main(["preset", "--reset"])
            pocket.return_value.upload.assert_called_once()
            self.assertEqual(pocket.return_value.upload.call_args.kwargs, {"slot": 3})
            self.assertEqual(
                pocket.return_value.upload.call_args.args[0].to_bytes(),
                factory_preset(3).to_bytes(),
            )
            pocket.return_value.send.assert_not_called()
            pocket.return_value.save.assert_not_called()
            pocket.return_value.select_preset.assert_not_called()

    def test_save_and_full_reset_without_unnecessary_reads(self):
        from discofloor.defaults import reset_all_messages

        for flag in ("--save", "--reset-all"):
            with (
                patch("discofloor.commands.Session") as session,
                patch("discofloor.commands.Pocket") as pocket,
                redirect_stdout(io.StringIO()),
            ):
                commands.main(["preset", flag])
                session.assert_not_called()
                if flag == "--save":
                    pocket.return_value.save.assert_called_once()
                    pocket.return_value.send.assert_not_called()
                else:
                    pocket.return_value.send.assert_called_once_with(reset_all_messages())
                    pocket.return_value.save.assert_not_called()

    def test_help_and_legacy_commands(self):
        with redirect_stdout(io.StringIO()) as out, self.assertRaises(SystemExit):
            commands.main(["--help"])
        self.assertIn("note-repeat", out.getvalue())
        self.assertIn("globe", out.getvalue())
        for command in ("runtime", "bank", "save"):
            with redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
                commands.main([command])
