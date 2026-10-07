"""Seven beat-driven tile patterns for the Pocket's 4×4 grid.

Rows run bottom to top: pads 1–4 at the bottom, pads 13–16 at the top.
Each pattern occupies four 4/4 bars, or sixteen quarter-note beats.
"""

import random
from dataclasses import dataclass
from typing import Callable

from .model import Color

BEATS_PER_BAR = 4
BARS_PER_PATTERN = 4
BEATS_PER_PATTERN = BEATS_PER_BAR * BARS_PER_PATTERN
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
BLACK = Color(0, 0, 0)
PERIMETER = (0, 1, 2, 3, 7, 11, 15, 14, 13, 12, 8, 4)


def shade(color: Color, amount: float) -> Color:
    return Color(*(round(component * amount) for component in color.to_bytes()))


def next_floor(rng: random.Random, previous: list[Color] | None) -> list[Color]:
    """Choose new colors, avoiding equal neighbors and the previous tile color."""
    tiles = []
    for index in range(16):
        excluded = {previous[index]} if previous else set()
        if index % 4:
            excluded.add(tiles[index - 1])
        if index >= 4:
            excluded.add(tiles[index - 4])
        tiles.append(rng.choice([color for color in PALETTE if color not in excluded]))
    return tiles


def shuffle(step, rng, previous):
    candidates = next_floor(rng, previous)
    # Keep the original demo: odd pads on beats 1/3, even pads on beats 2/4.
    return [candidates[i] if i % 2 == step % 2 else previous[i] for i in range(16)]


def checkerboard(step, rng, previous):
    first, second = PALETTE[(step // 4) % len(PALETTE)], PALETTE[(step // 4 + 4) % len(PALETTE)]
    return [first if (i // 4 + i % 4 + step) % 2 == 0 else second for i in range(16)]


def stripes(step, rng, previous):
    colors = (PALETTE[0], PALETTE[2], PALETTE[4], PALETTE[6])
    return [colors[(i % 4 + step) % 4] for i in range(16)]


def row_chase(step, rng, previous):
    row = step % 4
    color = PALETTE[(step // 4 * 2) % len(PALETTE)]
    return [color if i // 4 == row else shade(color, 0.05) for i in range(16)]


def diagonal_wave(step, rng, previous):
    return [PALETTE[(i // 4 + i % 4 - step) % len(PALETTE)] for i in range(16)]


def perimeter_spin(step, rng, previous):
    color = PALETTE[(step // 4 + 4) % len(PALETTE)]
    result = [shade(color, 0.06)] * 16
    for distance in range(4):
        result[PERIMETER[(step - distance) % len(PERIMETER)]] = shade(
            color, (1, 0.6, 0.3, 0.12)[distance]
        )
    return result


def center_pulse(step, rng, previous):
    levels = (1, 0.55, 0.2, 0.55)
    center, edge = (
        PALETTE[(step // 4 * 2 + 1) % len(PALETTE)],
        PALETTE[(step // 4 * 2 + 5) % len(PALETTE)],
    )
    return [
        shade(center, levels[step % 4])
        if i // 4 in (1, 2) and i % 4 in (1, 2)
        else shade(edge, levels[(step + 2) % 4])
        for i in range(16)
    ]


@dataclass(frozen=True)
class Animation:
    name: str
    description: str
    render: Callable[[int, random.Random, list[Color]], list[Color]]


ANIMATIONS = (
    Animation("Color shuffle", "Odd and even pads trade fresh colors each beat.", shuffle),
    Animation("Checkerboard", "Two interlocking tile groups swap colors.", checkerboard),
    Animation("Candy stripes", "Four colored columns march sideways.", stripes),
    Animation("Row chase", "A bright row sweeps from bottom to top.", row_chase),
    Animation("Diagonal wave", "Rainbow bands travel diagonally across the grid.", diagonal_wave),
    Animation("Perimeter spin", "A bright comet circles the outside twelve pads.", perimeter_spin),
    Animation(
        "Center pulse", "The center four tiles and outer ring breathe in opposition.", center_pulse
    ),
)


def animation_at(beat: int) -> tuple[int, int]:
    """Return pattern index and local beat, following musical time if beats skip."""
    if beat < 0:
        raise ValueError("Beat must be nonnegative")
    return (beat // BEATS_PER_PATTERN) % len(ANIMATIONS), beat % BEATS_PER_PATTERN


def render_frame(beat: int, rng: random.Random, previous: list[Color]) -> list[Color]:
    index, step = animation_at(beat)
    return ANIMATIONS[index].render(step, rng, previous)
