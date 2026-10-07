"""Pattern geometry, musical duration, and bank preservation without hardware."""

import io
import random
import unittest
from contextlib import redirect_stdout
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from discofloor.animations import (
    ANIMATIONS,
    BEATS_PER_PATTERN,
    BLACK,
    animation_at,
    render_frame,
)
from discofloor.defaults import factory_preset
from discofloor.disco import animate


class AnimationTests(unittest.TestCase):
    def test_seven_patterns_each_occupy_sixteen_beats_then_loop(self):
        self.assertEqual(len(ANIMATIONS), 7)
        self.assertEqual(BEATS_PER_PATTERN, 16)
        for beat in range(224):
            self.assertEqual(animation_at(beat), ((beat // 16) % 7, beat % 16))
        self.assertEqual(animation_at(112), (0, 0))

    def test_rendered_frames_are_valid_distinct_and_repeatable(self):
        signatures = []
        for index in range(7):
            rng = random.Random(110)
            previous = [BLACK] * 16
            sequence = []
            for step in range(16):
                previous = render_frame(index * 16 + step, rng, previous)
                self.assertEqual(len(previous), 16)
                self.assertTrue(all(len(color.to_bytes()) == 3 for color in previous))
                sequence.append(tuple(previous))
            self.assertGreater(len(set(sequence)), 1)
            signatures.append(tuple(sequence))
        self.assertEqual(len(set(signatures)), 7)
        self.assertEqual(
            render_frame(0, random.Random(110), [BLACK] * 16),
            render_frame(0, random.Random(110), [BLACK] * 16),
        )

    def test_shuffle_retains_alternating_half_and_checkerboard_geometry(self):
        previous = [BLACK] * 16
        first = render_frame(0, random.Random(1), previous)
        self.assertEqual(
            [i + 1 for i in range(16) if first[i] != previous[i]], list(range(1, 17, 2))
        )
        second = render_frame(1, random.Random(2), first)
        self.assertEqual([i + 1 for i in range(16) if second[i] != first[i]], list(range(2, 17, 2)))
        checker = render_frame(16, random.Random(1), previous)
        for i in range(16):
            if i % 4 < 3:
                self.assertNotEqual(checker[i], checker[i + 1])
            if i < 12:
                self.assertNotEqual(checker[i], checker[i + 4])

    def test_row_sweep_and_center_pulse_geometry(self):
        for step in range(4):
            colors = render_frame(3 * 16 + step, random.Random(1), [BLACK] * 16)
            brightest = max(sum(c.to_bytes()) for c in colors)
            self.assertEqual(
                [i // 4 for i, c in enumerate(colors) if sum(c.to_bytes()) == brightest], [step] * 4
            )
        pulse = render_frame(6 * 16, random.Random(1), [BLACK] * 16)
        self.assertEqual(len({pulse[i] for i in (5, 6, 9, 10)}), 1)
        self.assertNotEqual(pulse[5], pulse[0])

    def test_full_live_cycle_uses_one_bank_edit_per_beat_and_only_changes_colors(self):
        model = factory_preset(1)
        original = model.to_bytes()
        device = MagicMock()
        bank = device.preset.__getitem__.return_value.bank.__getitem__.return_value
        bank.edit.return_value.__enter__.return_value = model.bank[3]
        now = [0.0]

        def sleep(delay):
            now[0] += delay

        args = SimpleNamespace(preset=1, bank=3, bpm=110, seed=1, frames=112)
        with (
            patch("discofloor.disco.time.monotonic", side_effect=lambda: now[0]),
            patch("discofloor.disco.time.sleep", side_effect=sleep),
            redirect_stdout(io.StringIO()) as out,
        ):
            animate(args, device)
        self.assertEqual(bank.edit.call_count, 112)
        self.assertEqual(out.getvalue().count("Pattern "), 7)
        self.assertAlmostEqual(now[0], 111 * 60 / 110)
        updated = model.to_bytes()
        for offset in range(2931):
            if not (832 <= offset < 1248 and (offset - 832) % 26 in (5, 6, 7)):
                self.assertEqual(updated[offset], original[offset])
        device.save.assert_not_called()
