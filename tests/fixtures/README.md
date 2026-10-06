# Protocol fixtures

These fixtures come from the Midi Suite 1.15.0 / Pocket v4 investigation.
Captured write/read packets provide an independent check of the encoder, and
preset/calibration snapshots check lossless roundtrips and restoration.

JSON fixtures retain protocol bytes and decoded fields. Endpoint labels,
timestamps, polling noise, and unrelated events were removed where possible.
File names are retained to match the protocol investigation notes. No `.mmon`
sessions, photos, or extracted vendor firmware assets are included.

The complete original evidence is retained locally outside this Git repository.
Tests never open MIDI ports; transport is mocked.
