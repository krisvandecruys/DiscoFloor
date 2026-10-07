#!/usr/bin/env python3
"""Seven disco tile patterns, each four 4/4 bars at 110 BPM.

Close Midi Suite, then run: uv run discofloor demo
Snapshots the bank and selection first; restores on completion or Ctrl-C.
No persistent Save is sent.
"""

import argparse
import random
import time
from contextlib import contextmanager

from discofloor import Pocket
from discofloor.animations import ANIMATIONS, BEATS_PER_PATTERN, BLACK, animation_at, render_frame
from discofloor.ports import PORT_HELP, MidiPortUnavailable
from discofloor.protocol import preset_setting_message


@contextmanager
def saved_settings(args, device):
    snapshot = device.preset[args.preset].bank[args.bank].snapshot()
    try:
        yield
    finally:
        snapshot.restore()
        print("Original bank colors/settings and preset/bank selection restored. No Save sent.")


def animate(args, device=None):
    current_bank = None
    if device is not None:
        device.select_preset(args.preset)
        device.send([preset_setting_message(args.preset, "bank", args.bank)])
        current_bank = device.preset[args.preset].bank[args.bank]
    period = 60 / args.bpm
    print(
        f"{args.bpm:g} BPM, one change every {period:.3f}s; "
        f"{len(ANIMATIONS)} patterns × 4 bars; preset {args.preset}, bank {args.bank}. Ctrl-C to stop."
    )
    rng = random.Random(args.seed)
    previous = [BLACK] * 16
    last_pattern = None
    deadline = time.monotonic()
    frame = 0
    beat = 0
    while args.frames is None or frame < args.frames:
        time.sleep(max(0, deadline - time.monotonic()))
        index, step = animation_at(beat)
        pattern_key = beat // BEATS_PER_PATTERN
        if pattern_key != last_pattern:
            animation = ANIMATIONS[index]
            print(
                f"Pattern {index + 1}/{len(ANIMATIONS)}: {animation.name} — {animation.description} (4 bars)"
            )
            last_pattern = pattern_key
        if current_bank is not None:
            # All patterns still use one bank update and preserve non-color fields.
            with current_bank.edit() as bank:
                existing = [bank.pad[number].color for number in range(1, 17)]
                colors = render_frame(beat, rng, existing)
                for number, color in enumerate(colors, start=1):
                    bank.pad[number].color = color
        else:
            colors = render_frame(beat, rng, previous)
            print(
                f"Bar {step // 4 + 1}/4, beat {step % 4 + 1}: "
                + " ".join(c.to_bytes().hex() for c in colors)
            )
            previous = colors
        beat += 1
        frame += 1
        deadline += period
        now = time.monotonic()
        if deadline < now:
            skipped = int((now - deadline) // period) + 1
            deadline += skipped * period
            beat += skipped


def main(argv=None):
    parser = argparse.ArgumentParser(prog="discofloor demo", description=__doc__)
    parser.add_argument("--bpm", type=float, default=110)
    parser.add_argument("--preset", type=int, choices=range(1, 5), default=1)
    parser.add_argument("--bank", type=int, choices=range(1, 8), default=3)
    parser.add_argument("--port", help=PORT_HELP)
    parser.add_argument("--seed", type=int)
    parser.add_argument("--frames", type=int)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)
    if not 0 < args.bpm < float("inf") or (args.frames is not None and args.frames < 1):
        parser.error("BPM must be finite and positive; frame count must be positive")
    try:
        if args.dry_run:
            animate(args)
        else:
            device = Pocket(args.port)
            with saved_settings(args, device):
                animate(args, device)
    except MidiPortUnavailable as error:
        parser.exit(1, f"{error}\n")
    except KeyboardInterrupt:
        print("\nStopped. No persistent Save was sent.")


if __name__ == "__main__":
    main()
