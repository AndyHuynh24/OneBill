# Pillow layer

Source folder for the Pillow Lambda layer (photo tiles, design: docs/design/phase2c-image-tiles.md).
The layer itself is built at synth time by `pillowLayer()` in `lib/python-code.ts`:
locally with uv from the manylinux arm64 wheel (cached in `infra/.layers/`, gitignored),
or in the SAM Python build image if uv isn't available.
