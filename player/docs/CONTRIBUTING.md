# Bundled player development

The canonical player lives in
[OpsiClear-4DGS/ftgs-player](https://github.com/OpsiClear-4DGS/ftgs-player).
[UPSTREAM.md](../UPSTREAM.md) records this copy's revision and bundle differences.

Keep `src/` independent of the opt-in website UI in `site/`. Preserve the root
module aliases for existing browser and Node imports; the sorting worker must
remain beside its imported sorting module.

From the FreeTimeGsVanilla repository root, run:

```bash
npm --prefix player test
npm --prefix player run validate:fixtures
npm --prefix player run build
python -m pip install -r player/tests/requirements.txt
python -m playwright install chromium
python player/tests/browser_smoke.py
python player/tests/browser_api.py
python player/tests/browser_tsog.py
python player/tests/browser_audio.py
python player/tests/browser_xr.py
python player/tests/browser_sandbox.py
```

The checks cover parsing, packaging, renderer math, navigation, file recovery,
mobile layout, canvas/iframe isolation, fullscreen, speed, audio and simulated
XR. Physical-device AR and performance still need device testing. Node.js 22+
is used only for tooling; browser playback has no package installation step.
`browser_sandbox.py` checks rendering, audio after Play, built-in controls and
cleanup in cross-origin and opaque-origin iframes. This verifies browser
compatibility; X card acceptance requires a real post and testing in X clients.

The optional recorded example is hosted by the canonical project; local files
and synthetic fixtures require no external assets. Preserve AGPL-3.0-only and
all third-party notices when distributing either player.
