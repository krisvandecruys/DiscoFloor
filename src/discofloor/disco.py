#!/usr/bin/env python3
"""Disco tiles: odd pads on beats 1/3, even pads on beats 2/4 at 110 BPM.

Close Midi Suite, then run: uv run discofloor demo
Snapshots the bank and selection first; restores on completion or Ctrl-C.
No persistent Save is sent.
"""

import argparse
import random
import time
from contextlib import contextmanager

from discofloor import Color, Pocket
from discofloor.protocol import preset_setting_message

# Saturated, illuminated floor tiles inspired by the reference photo.
PALETTE = tuple(
    Color.from_hex(value)
    for value in (
        "#ff1744",
        "#ff8000",
        "#ffd600",
        "#32ff70",
        "#00dfff",
        "#3050ff",
        "#a030ff",
        "#ff30b0",
        "#ffb0d0",
    )
)


def next_floor(rng: random.Random, previous: list[Color] | None) -> list[Color]:
    """Avoid matching adjacent tiles and change every tile between frames."""
    tiles = []
    for index in range(16):
        excluded = set()
        if previous:
            excluded.add(previous[index])
        if index % 4:
            excluded.add(tiles[index - 1])
        if index >= 4:
            excluded.add(tiles[index - 4])
        tiles.append(rng.choice([color for color in PALETTE if color not in excluded]))
    return tiles


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
        f"preset {args.preset}, bank {args.bank}. Ctrl-C to stop."
    )
    rng = random.Random(args.seed)
    previous = None
    deadline = time.monotonic()
    frame = 0
    beat = 0
    while args.frames is None or frame < args.frames:
        time.sleep(max(0, deadline - time.monotonic()))
        numbers = range(1 if beat % 2 == 0 else 2, 17, 2)
        if current_bank is not None:
            # Preserve the other eight pads; exit still sends one bank write.
            with current_bank.edit() as bank:
                existing = [bank.pad[number].color for number in range(1, 17)]
                candidates = next_floor(rng, existing)
                for number in numbers:
                    bank.pad[number].color = candidates[number - 1]
        else:
            if previous is None:
                previous = [Color(0, 0, 0)] * 16
            candidates = next_floor(rng, previous)
            colors = previous.copy()
            for number in numbers:
                colors[number - 1] = candidates[number - 1]
            print(f"Beat {beat % 4 + 1}: " + " ".join(c.to_bytes().hex() for c in colors))
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
    parser.add_argument("--port", default="SINCO SMC-PAD Pocket-Private")
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
    except KeyboardInterrupt:
        print("\nStopped. No persistent Save was sent.")


if __name__ == "__main__":
    main()
