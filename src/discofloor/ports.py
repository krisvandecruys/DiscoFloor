"""Readable diagnostics when the requested MIDI port pair is missing."""

DEFAULT_PORTS = ("SINCO SMC-PAD Pocket-Private", "SMC-PAD Pocket Bluetooth")
PORT_HELP = "Exact MIDI input/output name; default: USB Private, then Pocket Bluetooth"


class MidiPortUnavailable(ValueError):
    """The selected input/output pair was not found."""


def require_ports(backend, name=None) -> str:
    inputs = list(backend.get_input_names())
    outputs = list(backend.get_output_names())
    candidates = DEFAULT_PORTS if name is None else (name,)
    for candidate in candidates:
        if candidate in inputs and candidate in outputs:
            return candidate
    requested = name if name is not None else " or ".join(DEFAULT_PORTS)
    lines = [f"MIDI port unavailable: {requested}", "", "Found MIDI inputs:"]
    lines.extend(f"  {port}" for port in inputs)
    if not inputs:
        lines.append("  (none)")
    lines.extend(["", "Found MIDI outputs:"])
    lines.extend(f"  {port}" for port in outputs)
    if not outputs:
        lines.append("  (none)")
    raise MidiPortUnavailable("\n".join(lines))
