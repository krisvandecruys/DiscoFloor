"""Persistent validated bank-read and write transport."""

import time
from contextlib import ExitStack
from typing import Any

from .ports import require_ports
from .protocol import decode


class Session:
    """Keep both MIDI ports open; serialize requests and validate replies."""

    def __init__(self, port, backend: Any = None):
        self.port = port
        self.backend = backend
        self.stack = ExitStack()

    def __enter__(self):
        if self.backend is None:
            import mido

            self.backend = mido.Backend("mido.backends.rtmidi")
        self.port = require_ports(self.backend, self.port)
        try:
            self.incoming = self.stack.enter_context(self.backend.open_input(self.port))
            self.outgoing = self.stack.enter_context(
                self.backend.open_output(self.port, autoreset=False)
            )
            # Cold-start USB reads can be lost immediately after opening CoreMIDI ports.
            # Waiting before the first request worked without Midi Suite or MIDI Monitor.
            time.sleep(1)
        except BaseException:
            self.stack.close()
            raise
        return self

    def __exit__(self, *args):
        return self.stack.__exit__(*args)

    def request(
        self,
        message: bytes,
        *,
        read_offset: int | None = None,
        read_register: int = 5,
        read_count: int = 416,
    ) -> bytes:
        list(self.incoming.iter_pending())
        factory = getattr(self.backend, "message_from_bytes", None)
        if callable(factory):
            outgoing = factory(message)
        else:
            import mido

            outgoing = mido.Message.from_bytes(list(message))
        self.outgoing.send(outgoing)
        deadline = time.monotonic() + 2
        while time.monotonic() < deadline:
            for reply in self.incoming.iter_pending():
                if reply.type != "sysex":
                    continue
                packet = decode(bytes(reply.bytes()))
                if read_offset is not None:
                    if packet["type"] != 0x23:
                        continue
                    if (
                        packet.get("register") != read_register
                        or packet.get("offset") != read_offset
                    ):
                        raise RuntimeError("Unexpected read address; no configuration writes sent")
                    data = bytes.fromhex(packet["data"])
                    if packet["count"] != read_count or len(data) != read_count:
                        raise RuntimeError("Incomplete read; no configuration writes sent")
                    return data
                if packet["type"] == 0:
                    if packet["raw"] != "00 59 00 01 00 00 00 ff":
                        raise RuntimeError("Unexpected ACK; write stopped")
                    return b""
            time.sleep(0.001)
        raise TimeoutError("No expected reply within two seconds; no automatic retry")
