# Contributing

Use Python 3.14+ and uv:

```sh
uv sync
uv run python -m unittest discover -s tests -v
uv run ruff check .
uv run ruff format --check .
uv run ty check
uv build
```

Add `--extra midi` to `uv sync` for physical-device testing. Close Midi Suite
before using the Private MIDI ports. Keep persistent Save separate from temporary
writes. New protocol claims should be backed by vendor-app captures or clearly
labelled as hypotheses in `docs/PROTOCOL.md`.

Do not commit complete personal MIDI sessions or unrelated device backups.
Prefer compact, focused fixtures. Describe firmware/app versions and whether
validation used hardware, captures, or mocked transport.
