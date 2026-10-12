# Bundled player source

Source: [OpsiClear-4DGS/ftgs-player](https://github.com/OpsiClear-4DGS/ftgs-player).
Revision: [`c235a61`](https://github.com/OpsiClear-4DGS/ftgs-player/commit/c235a614edf91f7817ef9c636fcf50afd4cd43e5).

`src/`, `site/`, the embedding hosts, TSOG tools, schema and synthetic
conformance/browser checks come from this revision. Rendering and playback
math are unchanged. The bundled UI shares its compact opening panel,
borderless transport, audio defaults, speed and fullscreen behavior.

Bundle differences:

- Root module and stylesheet aliases preserve the previous player URLs.
- Existing documentation URLs point to `docs/`.
- The Jensen manifest points to the hosted TSOG; recorded model data, its
  screenshot, and standalone package/recorded-example tests are omitted.
  The small social-card JPEG and MP4 are bundled; their regeneration tool
  remains in the standalone project, which retains the full TSOG.
- `site/examples.js` uses a retried whole-file request for the hosted sample,
  since GitHub Pages does not expose Content-Range to other origins. The
  expected byte count is still checked; local assets use validated ranges.
- The README and development guide describe this bundled copy.

When updating, preserve these compatibility aliases and local-file behavior,
retain license notices, and run the checks in [CONTRIBUTING](docs/CONTRIBUTING.md).
