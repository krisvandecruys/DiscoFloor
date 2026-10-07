"""Inclusive, one-based pad selections for command-line arguments."""

import argparse
import re


def pad_range(text: str) -> list[int]:
    pads = []
    for part in text.split(","):
        match = re.fullmatch(r"\s*(\d+)(?:\s*-\s*(\d+))?\s*", part)
        if match is None:
            raise argparse.ArgumentTypeError("Use pads such as 1, 1-4, or 1-4,7,9-12")
        start = int(match[1])
        end = int(match[2]) if match[2] else start
        if not 1 <= start <= end <= 16:
            raise argparse.ArgumentTypeError("Pad ranges must be ascending and within 1–16")
        for number in range(start, end + 1):
            if number not in pads:
                pads.append(number)
    return pads
