# Slop Example

[Play the scene](https://opsiclear-4dgs.github.io/ftgs-player/?example=jensen-2m)
or [download the TSOG](https://opsiclear-4dgs.github.io/ftgs-player/examples/assets/jensen-2m.tsog).
The demo loads all 2,000,000 Gaussians and uses the saved front-view camera.
The website fetches this file only when the example is selected.

The display name is Slop Example. Existing `jensen-2m` URLs and filenames
remain available for compatibility.

`jensen-2m.tsog` contains the preserved reconstruction from
[sam3dbx4danyone](https://github.com/OpsiClear-4DGS/sam3dbx4danyone):
the Base36 Jensen 2M run with original 1M movement scales, fitting seed 2042.
It contains 121 source timestamps at 24 fps, 5 seconds of animated content,
and the original embedded audio track. The version-1 ZIP container is 45,092,406
bytes (45.1 MB). Its attribute images, soundtrack and decoded values are unchanged;
only metadata and ZIP packaging were updated. The original version-4 container
checksum, current SHA-256, detailed provenance and camera bookmark are in
[jensen-2m.json](jensen-2m.json).

This is an example reconstruction from generated multiview inputs, not a
reference mesh or a claim of accurate recovered geometry. It is kept separately
from the synthetic decoder fixtures and excluded from the reusable npm package.

The AGPL-3.0-only license covers the player code. Source media and embedded audio
retain their original rights; the code license does not relicense that content.

## Social preview

`site/assets/social-preview.jpg` is a 1200×675 render of the same full 2M scene
at normalized time 0.3, using its saved camera. It is the static thumbnail
for X large-image cards and Open Graph previews and retains the source media's
original rights. The [scene page](../jensen-2m.html) opens the interactive 4DGS
web app with the retained TSOG and its embedded audio. WebGL2 and the model download
are required when opening the interactive scene.
