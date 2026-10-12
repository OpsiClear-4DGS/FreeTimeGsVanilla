# Bundled player source

Source: [OpsiClear-4DGS/ftgs-player](https://github.com/OpsiClear-4DGS/ftgs-player).
Revision: [`bc73995`](https://github.com/OpsiClear-4DGS/ftgs-player/commit/bc73995bae3f21d7711f7d53ac8e92ca2c5134c2).

`src/`, `site/`, the embedding hosts, TSOG tools, schema and synthetic
conformance/browser checks come from this revision. Rendering and playback
math are unchanged. The bundled UI shares its compact opening panel,
borderless transport, audio defaults, speed and fullscreen behavior.

Bundle differences:

- Root module and stylesheet aliases preserve the previous player URLs.
- Existing documentation URLs point to `docs/`.
- The Jensen manifest points to the hosted TSOG; recorded model data, its
  screenshot, and standalone package/recorded-example tests are omitted.
  The small social-card JPEG is bundled; the card opens the hosted interactive
  4DGS scene.
- The README and development guide describe this bundled copy.

When updating, preserve these compatibility aliases and local-file behavior,
retain license notices, and run the checks in [CONTRIBUTING](docs/CONTRIBUTING.md).
