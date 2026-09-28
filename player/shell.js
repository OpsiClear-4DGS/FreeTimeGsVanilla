/** Markup shared by the standalone player and the canvas example. */
export function mountPlayerShell(viewer) {
  viewer.innerHTML = `
    <svg class="symbols" xmlns="http://www.w3.org/2000/svg" aria-hidden="true">
      <defs>
        <symbol id="play" viewBox="0 0 24 24"><path d="M8 5.5v13l10-6.5Z" /></symbol>
        <symbol id="pause" viewBox="0 0 24 24"><path d="M7 5.5h3.5v13H7zm6.5 0H17v13h-3.5z" /></symbol>
        <symbol id="open" viewBox="0 0 24 24"><path d="M3 7.5V5.8c0-.7.6-1.3 1.3-1.3H10l2 2H20v3M3 9.5h18l-2.5 10H4.3L3 9.5Z" /></symbol>
        <symbol id="reset" viewBox="0 0 24 24"><path d="M4.5 10a7.5 7.5 0 1 1 1.7 7M4.5 4.5V10H10" /></symbol>
        <symbol id="expand" viewBox="0 0 24 24"><path d="M8.5 4.5h-4v4m11-4h4v4m-15 7v4h4m11-4v4h-4" /></symbol>
        <symbol id="collapse" viewBox="0 0 24 24"><path d="M4.5 8.5h4v-4m7 0v4h4m-15 7h4v4m7 0v-4h4" /></symbol>
      </defs>
    </svg>
    <canvas id="canvas" tabindex="0" aria-label="3D scene. Drag to orbit, Shift-drag to pan, scroll to zoom. WASD to move, Q/E down/up, Shift to move faster."></canvas>
    <button id="empty" class="empty" aria-label="Choose a .ftgs.ply file">
      <span class="empty-icon"><svg aria-hidden="true"><use href="#play" /></svg></span>
      <span class="empty-title">Drop a .ftgs.ply to play</span>
      <span class="empty-caption">or click to open a file</span>
    </button>
    <input id="file" type="file" accept=".ply" hidden aria-label="Open FTGS file" />
    <div id="message" class="message" role="status" hidden></div>
    <div id="ar-hint" class="message ar-hint" role="status" hidden></div>
    <div id="drop-hint" class="drop-hint" hidden>
      <svg aria-hidden="true"><use href="#open" /></svg><span>Drop to play</span>
    </div>
    <div id="controls" class="controls" role="group" aria-label="Playback controls" hidden>
      <div id="timeline-area" class="timeline-area">
        <output id="seek-preview" class="seek-preview" aria-hidden="true">0:00</output>
        <input id="timeline" type="range" min="0" max="1" step="0.0001" value="0" aria-label="Timeline" disabled />
      </div>
      <div class="transport">
        <button id="play-toggle" class="icon-button" data-tooltip="Play (Space)" aria-label="Play" disabled>
          <svg class="solid-icon" aria-hidden="true"><use id="play-icon" href="#play" /></svg>
        </button>
        <output id="timecode" aria-label="Playback time" aria-live="off">0:00 <span>/ 0:00</span></output>
        <span class="transport-space"></span>
        <button id="reset-view" class="icon-button" data-tooltip="Reset view (R)" aria-label="Reset view" disabled>
          <svg aria-hidden="true"><use href="#reset" /></svg>
        </button>
        <button id="open-file" class="icon-button" data-tooltip="Open file (O)" aria-label="Open file">
          <svg aria-hidden="true"><use href="#open" /></svg>
        </button>
        <button id="ar" class="icon-button ar-button" data-tooltip="View in AR" aria-label="View in AR" aria-pressed="false" disabled hidden>AR</button>
        <button id="fullscreen" class="icon-button" data-tooltip="Fullscreen (F)" aria-label="Fullscreen">
          <svg aria-hidden="true"><use id="fullscreen-icon" href="#expand" /></svg>
        </button>
      </div>
    </div>`;
}
