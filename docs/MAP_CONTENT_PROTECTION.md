# Map content protection

The seasonal publishing pipeline creates protected derivatives under `seasonal/share/`
without changing the scientific source maps.

Each derivative includes:

- the normal in-map `wall.cloud` mark anchored to the detected map frame;
- repeated low-opacity `wall.cloud` marks with a short stable map identifier;
- embedded rights/provenance metadata where the image format supports it;
- `share/provenance.json`, containing the logical source path and SHA-256 hashes
  for the source and published derivative;
- a cache-version bump so existing derivatives are rebuilt when the protection
  layer changes.

The visible marks are attribution and crop-resistance measures. They are not a
technical restriction on copying and they do not tell an AI system what it may
or may not do. Image models can ignore metadata, remove watermarks, or recreate
a map. The hash manifest and source metadata are intended to make ownership and
provenance easier to demonstrate in a platform report or legal dispute.

The underlying provider data and model output remain subject to their own
licenses and terms. The protection layer covers the original presentation,
composition, annotations, styling, and other human-authored expression in the
published graphics.
