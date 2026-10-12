# Bundled player source

Source: [OpsiClear-4DGS/ftgs-player](https://github.com/OpsiClear-4DGS/ftgs-player).
Revision: [`009c622`](https://github.com/OpsiClear-4DGS/ftgs-player/commit/009c6228153c1905fac774be8ef22487e0f39664).

`src/`, `site/`, the embedding hosts, TSOG tools, schema and synthetic
conformance/browser checks come from this revision. Rendering and playback
math preserve the same attributes and pixels. Decoding runs in a temporary
worker where supported. Sorting caches conservative temporal bounds and reuses
scratch buffers. TSOG loading shares SH centroids and covariance buffers.
The bundled UI shares its compact opening panel,
borderless transport, audio defaults, speed and fullscreen behavior.
TSOG readers accept version 1 and legacy Xiaomi v4; packaging emits version 1
with playback profile 1.

Bundle differences:

- Root module and stylesheet aliases preserve the previous player URLs.
- Existing documentation URLs point to `docs/`.
- The Slop Example manifest points to the hosted TSOG; recorded model data, its
  screenshot, and standalone package/recorded-example tests are omitted.
  The small social-card JPEG is bundled; image cards link to the hosted
  interactive 4DGS scene page.
- The README and development guide describe this bundled copy.

When updating, preserve these compatibility aliases and local-file behavior,
retain license notices, and run the checks in [CONTRIBUTING](docs/CONTRIBUTING.md).
