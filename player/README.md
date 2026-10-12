# FTGS Player

The bundled browser player is synchronized with
[OpsiClear-4DGS/ftgs-player](https://github.com/OpsiClear-4DGS/ftgs-player).
It opens animated `.ftgs.ply` and packaged `.tsog` scenes with WebGL2,
without a build step or runtime dependencies.

From the FreeTimeGsVanilla repository root:

```bash
python -m http.server 8765 --bind 127.0.0.1 --directory player
```

Open <http://localhost:8765>, choose a scene or drop a file onto the page.
The compact opening panel has one file action and an optional example.
Playback uses borderless icon controls below the scene, with seeking, speed,
audio and bottom-right fullscreen. Audio starts enabled; press Play if the
browser blocks autoplay with sound. Local files stay in your browser.

- [Controls, URL settings, audio and AR](docs/PLAYER.md)
- [Canvas and iframe APIs](docs/API.md), [embedding examples](examples/embedding.html)
- [FTGS format](docs/FORMAT.md), [TSOG guide](docs/TSOG.md),
  [playback specification](docs/TSOG-SPEC.md), [schema](tsog.schema.json)
- [Development checks](docs/CONTRIBUTING.md), [upstream revision](UPSTREAM.md)

`src/` contains the reusable controller, renderer, decoder and sorting worker.
`site/` contains the standalone UI. Root JavaScript entry points continue
forwarding to these modules for existing integrations, including TSOG packaging.
The examples use the same speed, audio and fullscreen controls for canvas and
iframe hosts, and show mode-specific integration code below the player with
a Copy code button. The Jensen example is fetched only when selected; its 45 MB
model is hosted in the standalone project. `?demo=1` uses a generated scene.

The hosted player and example pages include static X Player Card and Open
Graph tags with a 1200×675 thumbnail and no personal account attribution.
The card's player URL opens the interactive Jensen 2M 4DGS scene with playback,
camera navigation and its embedded audio. The thumbnail is bundled here; the
full model remains hosted in the standalone project. X controls whether it
opens the player inside its card, its in-app browser or a regular browser.
See the [thumbnail provenance](examples/assets/README.md).

Code is **AGPL-3.0-only**. Retain [LICENSE](LICENSE) and
[third-party notices](docs/THIRD_PARTY.md), including the bundled font licenses.
