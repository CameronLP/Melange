# TODO

## Urgent

- [ ] Look for any remaining audio-visual latency/lag - the PCM AudioWorklet's unbounded sample queue (main.js) was found and fixed (capped at 50ms, see Done), which was the most likely source of the originally reported ~0.5s pause/resume delay. Not yet re-verified end-to-end, and there are other unexamined points earlier in the pipeline that could still add lag: GStreamer buffer/latency-time on the capture pipeline (start_system_audio, window.py), GLib.idle_add scheduling in on_audio_sample, and WebKit's evaluate_javascript IPC round-trip for each chunk.

## In progress / not started

- [ ] Better preset organization + a larger preset browser window
- [ ] Favorite presets
- [ ] Optional "now playing" overlay in the corner of the canvas
- [ ] X-Y scope visualizer
- [ ] Frequency-range control for what feeds the visualizer (so presets that only react to bass, etc. can be tuned)
- [ ] Logo + symbolic icon polish - the scalable app icon was replaced (Bottles-based, data/icons/hicolor/scalable/apps), but the symbolic icon at data/icons/hicolor/symbolic/apps/com.cameronlp.Melange-symbolic.svg is still the original template placeholder and doesn't match
- [ ] Multiple visualizer windows
- [ ] Cursor auto-hide in fullscreen - WebKit manages its own cursor over page content and overrides host-level GtkWidget.set_cursor(), so this needs to be driven from inside the page (JS toggling a `cursor: none` CSS class) instead
- [ ] Settings profiles - save/load named sets of Preferences (sensitivity, mesh size, blend time, beat sensitivity, etc.) so you can switch between e.g. a "party" profile and a "chill" profile instead of manually re-tuning every slider
- [ ] Reduce memory usage - not yet profiled to find where it's actually going (node_modules vendored in the repo isn't the same as runtime memory, so this needs real measurement of the running app, not just guessing)

### Visualizer settings ideas

- [ ] Silence auto-pause - freeze/dim rendering when no audio is detected for a while, to save CPU/GPU
- [ ] Global post-processing tint/gamma - a brightness/color adjustment layered on top of whatever the preset renders
- [ ] Shuffle pool weighting/exclusion - let shuffle skip specific packs (e.g. exclude baron, or "only my favorites" once favorites exist)

### Bigger feature ideas

- [ ] MPRIS "now playing" integration - pull actual track/artist from whatever's playing (Spotify, etc.) via D-Bus instead of just the preset name; also enables auto-advancing the preset on track change
- [ ] Recording/export - save the visualizer output as a video clip, or grab a screenshot of a good moment
- [ ] MIDI controller support - map physical knobs/pads to sensitivity, blend time, next/prev preset
- [ ] D-Bus remote control - expose next/prev/lock/shuffle over D-Bus for external tools (Stream Deck, macros, scripts) to drive without focus
- [ ] System tray / background mode - stay running and controllable when the window is closed/unfocused
- [ ] Save/load playlists - save the current Queue (see Done, below) as a named, persisted playlist you can reload later, rather than it existing only for the current session

## Paused

- [ ] MilkDrop (.milk) preset converter compatibility - `repairBadShader()` fixes the
      most common converter bug (see `docs/milkdrop-preset-converter-shader-bug.md`),
      but some presets still fail or hang the converter itself. Put on
      hold in favor of feature work; possible future direction is a
      projectM-based foundation instead of `milkdrop-preset-converter`.
- [ ] Beat detection (Energy Threshold + Tempo Tracking modes, plus
      drop detection) - **experimental, partially implemented**. Built
      and unit-tested against synthetic signals only (see
      docs/beat-detection.md, now marked experimental there and in the
      Preferences UI itself) - never validated against real, varied
      music through the actual live pipeline. Real usage already
      surfaced two bugs synthetic testing missed (tempo mode firing on
      almost every frame; an octave error), so there's good reason to
      expect more tuning is needed before this is trustworthy. Picking
      this back up means listening across real tracks/genres and
      tuning from there, not just re-running the synthetic tests.

## Done

- [x] Shuffle/random toggle for advancing presets
- [x] Cycle time (auto-advance interval, with an "Off" state)
- [x] Transition/blend time control
- [x] Mesh size setting
- [x] Framerate setting
- [x] About page (credits Butterchurn/jberg, the MilkDrop preset
      converter, and the baron preset pack)
- [x] Preferences dialog (Audio/Playback/Rendering tabs) replacing the
      cramped popover-menu sliders
- [x] Light/Dark/Follow System theme selector (hamburger menu, GNOME
      Text Editor-style circular swatches)
- [x] Header bar tints to the active theme instead of always being dark
- [x] Fullscreen: Esc to exit, toolbar auto-hide (cursor auto-hide still open, see above)
- [x] Baron preset pack merged into the built-in preset list
- [x] Open native Butterchurn (.json) preset files, not just .milk
- [x] Investigate visualizer sensitivity - real bug found: the system-audio capture is genuine interleaved stereo (window.py, channels=2), but receiveAudio (main.js) was decoding it as one flat mono stream, alternating L/R samples together as if they were sequential samples of the same channel. That corrupted the frequency content Butterchurn's bass/mid/treb analysis runs on for every preset, not just stereo-aware ones. Fixed by de-interleaving properly and feeding true stereo through the custom PCM worklet.
- [x] Preset queue - full manager: a "+" button per row in the preset browser adds to a JS-owned queue, a Queue... dialog lists it with up/down/remove per item, and nextPreset() drains the queue before falling back to shuffle/sequential
- [x] Anti-Aliasing toggle, Render Resolution Scale (0.25x-2x canvas pixel buffer, distinct from Mesh Size), and Beat-Driven Cycle (bass-onset detector with a rolling average + cooldown, tapped off the same node feeding Butterchurn so it works for mic or system audio) - all in Preferences
- [x] Cycle Interval Jitter, and tunable Beat Sensitivity/Cooldown/Silence Floor - the beat detector's previously-hardcoded constants exposed as Preferences sliders
- [x] Tempo Tracking beat detection mode - a second, selectable algorithm (Beat Detection Mode dropdown) alongside the original Energy Threshold, aimed at continuously mixed DJ sets where the original struggles (needs loud/quiet contrast a relentless mix doesn't have). Spectral flux onset detection + autocorrelation-based tempo estimation, with an octave-error correction found and fixed during testing, and a second false-positive bug (self-inclusion in the comparison average) found and fixed from real usage logs. Full writeup, references, and measured performance in docs/beat-detection.md.
- [x] Drop detection - runs alongside whichever Beat Detection Mode is selected whenever Beat-Driven Cycle is on. A drop is a rare, sustained, dramatic broadband energy jump, not just another beat, so neither beat mode had any particular affinity for it. Verified against a synthetic groove+buildup+drop track (one correct detection, no false positives during the buildup). Own toast text ("Drop detected...") distinct from a regular beat advance.
