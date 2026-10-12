# Website design

The standalone player and embedding examples follow
[COLMAPView's design system](https://github.com/ColmapView/Colmapview.github.io/blob/main/docs/design-system.md)
and [style reference](https://github.com/ColmapView/Colmapview.github.io/blob/main/STYLE_REFERENCE.md).
The shared tokens live in `site/theme.css`: neutral charcoal surfaces, silver
accents, a 4 px spacing scale, restrained borders and 4–8 px corner radii.

The opening screen is one compact panel: a single "Open scene" file drop area,
a small example row labelled "Slop Example", and quiet resource links in the
panel heading. During playback, the scene occupies its own area above the
playback dock, so controls never
cover its content. Very tall viewports use a portrait canvas with space above
and below, keeping the scene's horizontal framing useful on phones.

The borderless dock uses one row on wide screens and two on narrow containers.
Play and pause are plain icons with a subtle surface on hover or keyboard focus,
and 44 px hit areas. Secondary view actions move into a keyboard-accessible
menu. Play, time, audio, speed
and fullscreen remain available; fullscreen is the last control at the bottom
right. Controls fade while playing and stay visible on hover or keyboard focus.
There is no persistent title bar or sidebar.

Both embedding examples use `site/transport.css` and fullscreen the entire
scene-and-controls container. Audio is enabled by default. If the browser
blocks sound, the UI pauses playback and the main Play button retries it from
a user gesture, rather than continuing an unexpectedly silent scene.

IBM Plex Sans and JetBrains Mono are self-hosted; their SIL Open Font License
notices are in `site/fonts/`. Icons are inline SVG. No external fonts, icon
services or UI framework are required at runtime.

The canvas library never imports these styles. Hosts can use this theme or
provide their own. `examples/embedding.html` demonstrates host-owned controls
for both the canvas library and the iframe API.
