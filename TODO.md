# TODO

## Urgent

- [ ] Look for any remaining audio-visual latency/lag - the PCM AudioWorklet's unbounded sample queue (main.js) was found and fixed (capped at 50ms, see Done), which was the most likely source of the originally reported ~0.5s pause/resume delay. Not yet re-verified end-to-end, and there are other unexamined points earlier in the pipeline that could still add lag: GStreamer buffer/latency-time on the capture pipeline (start_system_audio, window.py), GLib.idle_add scheduling in on_audio_sample, and WebKit's evaluate_javascript IPC round-trip for each chunk.
- [ ] Some presets still don't seem to react much to audio - real bugs affecting reactivity have already been found and fixed (mono/stereo interleaving corruption, PCM worklet chunk truncation, unbounded queue latency - see Done), but the complaint has resurfaced since. Worth checking again whether this is a genuine remaining bug or just preset-design diversity (many community MilkDrop/Butterchurn presets are deliberately more ambient/subtle than others) - not yet determined which.

## In progress / not started

- [ ] Reduce audio-bridge overhead further - `send_audio_to_webview`
      (window.py) calls `webview.evaluate_javascript()` with a fresh
      JS source string (`receiveAudio('<base64>')`) built fresh for
      every audio chunk (tens of times/sec while audio is playing).
      WebKitGTK's C API has no way to invoke a JS function with an
      out-of-band argument - the payload has to be embedded as a
      string literal in the source - so JavaScriptCore parses/compiles
      a "new" script on every single call instead of reusing one. Two
      other real inefficiencies in the same path were found and fixed
      (see Done: ring-buffer PCM queue, transferable postMessage), but
      this one would need a different transport (e.g. a local
      WebSocket from Python to a JS-side listener) to actually avoid,
      which is a bigger change than fits alongside the other two.
- [ ] Default Cycle Interval to 30s instead of Off
- [ ] Better preset organization + a larger preset browser window
- [ ] Favorite presets
- [ ] Optional "now playing" overlay in the corner of the canvas
- [ ] X-Y scope visualizer
- [ ] Frequency-range control for what feeds the visualizer (so presets that only react to bass, etc. can be tuned)
- [ ] Logo + symbolic icon polish - the scalable app icon was replaced (Bottles-based, data/icons/hicolor/scalable/apps), but the symbolic icon at data/icons/hicolor/symbolic/apps/com.cameronlp.Melange-symbolic.svg is still the original template placeholder and doesn't match
- [ ] Cursor auto-hide in fullscreen - WebKit manages its own cursor over page content and overrides host-level GtkWidget.set_cursor(), so this needs to be driven from inside the page (JS toggling a `cursor: none` CSS class) instead
- [ ] Reduce memory usage further - real measurement (not guessing) already found and fixed several things: eager loading of the whole baron preset pack at startup, GC churn in the audio hot path, and some unused WebKit persistence/features (see Done). What's left is WebKitGTK's own multi-process baseline itself (UI + network + web-content processes, each a full engine instance) - measured at roughly 500-650MB total for a single window even after the above fixes. Trimming that further means either the WebSocket audio-bridge change above, or the native-rendering direction below; see that for the real lever.

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
- [ ] Native rendering via libprojectM instead of Butterchurn/WebKitGTK -
      the big lever for memory, not an incremental one: WebKitGTK's
      multi-process browser-engine architecture (UI + network +
      web-content processes) is the dominant remaining memory cost
      (~500-650MB baseline for one window, even after the WebKit
      settings/persistence trimming and audio-path fixes below), and
      no amount of settings-tuning removes that - it's the cost of
      embedding a full browser to host one WebGL canvas. `libprojectM`
      is a native C++ implementation of the actual MilkDrop rendering
      algorithm; rendering into a `Gtk.GLArea` instead would eliminate
      WebKitGTK entirely (no browser engine, no JS engine, no
      multi-process overhead), let audio feed the renderer directly via
      projectM's PCM API (no JSON/base64/evaluate_javascript bridge at
      all - this would also make the "reduce audio-bridge overhead"
      item above moot rather than solved), and load real `.milk`
      presets natively - incidentally resolving the paused MilkDrop
      converter compatibility problem too, since there'd be no
      conversion step. The real cost: this is a full rewrite of the
      rendering/audio/preset layer, not a patch - it means losing the
      curated Butterchurn/baron JS preset packs (switching to a native
      MilkDrop preset collection instead) and re-implementing or
      dropping most of what's JS-side today (beat/drop detection, the
      preset queue's JS half, the shader-repair hacks), plus writing a
      ctypes/GObject wrapper around libprojectM's C API since no
      official Python binding exists. Realistically multi-day work -
      worth doing if memory footprint and native .milk compatibility
      are worth that trade-off, not something to start speculatively.

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

- [x] Multiple visualizer windows (mirror mode, for multi-monitor
      setups) - "New Mirror Window…" in the primary window's menu
      opens an additional window (mirror_window.py, `MirrorWindow`)
      showing a true pixel mirror of the primary's webview via
      `Gtk.WidgetPaintable.new(primary.webview)` displayed in a
      `Gtk.Picture` - GTK's own built-in "show this live widget's
      rendered content somewhere else" mechanism, GPU-composited by
      GTK itself. Deliberately NOT a second Butterchurn/WebKit
      instance (an earlier design that synced separate instances via
      broadcast preset/settings messages was scrapped once it was
      clear a true pixel mirror was wanted instead) - a mirror window
      costs one extra GTK window and a Picture widget, no extra
      WebKitWebProcess/JS engine/audio-sync bridge, so it doesn't add
      the several-hundred-MB-per-window cost a second real WebKit
      instance would (see the RAM investigation elsewhere in this
      file). The primary "controls" mirrors in the simplest possible
      sense - they have no independent state or behavior at all, only
      ever showing whatever the primary is currently rendering. Since
      it's the primary's actual live webview being shown, not a copy,
      anything the page renders (including the on-canvas preset-nav
      arrows) appears in the mirror too - there's only one underlying
      DOM - but Gtk.Picture doesn't forward input back to a
      paintable's source, so those arrows (and everything else in the
      page) are inert there, purely visual.

      A real, serious bug was found and fixed while building this:
      returning False from a window's "close-request" handler (the
      usual "let the default handler run" convention) turned out to
      leave the window fully alive rather than closing it - confirmed
      via get_visible() staying True regardless of whether an explicit
      self.destroy() was also called first. This wasn't just a mirror
      issue - the same broken pattern had been added to the *primary*
      window's own close-request handler (for the mirror-cascade
      logic below), meaning closing the main window would have stopped
      working entirely. Fixed by destroying from a deferred idle
      callback and returning True instead, verified on both windows:
      get_visible() correctly flips to False, and - critically -
      closing the primary while a mirror is open now cascades through
      both and the whole process exits cleanly (checked via the actual
      process list, not just widget state). A related crash (toggling
      fullscreen on a mirror, but only *after* it had been through the
      broken close path) turned out to be a downstream symptom of this
      same bug, not a fullscreen-specific issue - confirmed by
      reproducing fullscreen toggling safely many times over on a
      mirror that was never touched by the broken close path.

      Mirrors get their own F11 fullscreen + Escape-to-exit (so each
      can be fullscreened independently once dragged to its target
      monitor - GTK/Wayland doesn't let an app auto-position a window
      onto a specific monitor, so that drag is a manual step, not
      something this automates), the same draggable-from-anywhere and
      toolbar-auto-hide behavior as the primary window (simpler here
      since Gtk.Picture, unlike WebKit, doesn't swallow input events -
      no CAPTURE-phase controllers or synthetic-motion filtering
      needed), a title that includes its mirror number and the current
      preset name (kept in sync via the primary's own PRESET_NAME
      handling - see `current_preset_name`/`update_mirror_titles`), a
      header button to instantly match the primary window's current
      size, and a header button to double as a quick way to
      raise/focus the primary (a double-click anywhere in the mirror
      also does this - single-click still drags). The picture fills
      the window completely (Gtk.ContentFit.FILL) even if that distorts
      the aspect ratio, rather than the default letterboxed CONTAIN.
      "Close All Mirrors" is available from the primary's menu too.
      A `notify::maximized` handler forces a redraw as a defensive fix
      for maximizing a mirror leaving the picture showing a frozen
      frame - the FILL content-fit above may already address the
      underlying cause, since CONTAIN's aspect-locked size negotiation
      is a more complex layout path, but this covers the symptom
      directly either way.

      Verified: confirmed Gtk.WidgetPaintable correctly tracks a live
      WebKit.WebView (intrinsic size matched the source, both widgets
      realized/mapped, no errors) via a throwaway test run through the
      app's real launch path before building the feature on top of it.
      End-to-end, repeatedly, via org.gtk.Actions over D-Bus (window-
      level actions turned out to be introspectable that way): mirror
      creation spawns no second WebKitWebProcess; fullscreen toggling
      a healthy mirror in and out produces no errors; close-all-
      mirrors and closing the primary with a mirror open both
      correctly tear everything down and the process fully exits.
      D-Bus action-group objects were found to keep responding to
      calls for a while after their owning window is destroyed - a
      red herring in this environment's testing methodology, not a
      real bug (get_visible() is the reliable signal, not whether the
      D-Bus path still answers). Not independently verified by hand:
      actually dragging a mirror to a second monitor and confirming
      the pixels visually match, and the toolbar auto-hide/drag/
      double-click-to-focus interactions (no screenshot or input-
      injection capability in this environment for any of these).
- [x] Shuffle Queue - a Gtk.ToggleButton in the Queue dialog's header
      (win.shuffle-queue, same stateful-action pattern as Loop Queue).
      Turning it on shuffles presetQueue in place (Fisher-Yates), then
      nextPreset() just drains front-to-back as always - a first
      attempt picked a random index on every draw instead, which
      turned out biased (the same item could come up twice in a row,
      or another could sit unplayed for an arbitrarily long stretch),
      caught before committing and replaced with the in-place shuffle.
      Reordering the array means the Queue dialog's visible order
      changes when shuffle turns on (no separate "original order" is
      kept to restore if it's turned back off - a deliberate
      trade-off). Loading a playlist (setQueue) also shuffles on
      arrival if Shuffle Queue is already on. Verified offline
      (Node, 60k trials of a 3-item queue): all 6 permutations came up
      with near-equal frequency, confirming no bias, and a 5-item
      shuffle round-trip kept the same items with none lost or
      duplicated.
- [x] Save/load playlists + Loop Queue - a "Playlists…" button in the
      Queue dialog opens a Profiles-tab-style list (a "Save Current
      Queue as Playlist…" row at the top, then a row per saved
      playlist with Load/Delete), persisted as JSON at
      `$XDG_CONFIG_HOME/melange/playlists.json` next to profiles.json.
      Loading a playlist replaces the queue wholesale via a new JS
      `setQueue()` (unknown/removed preset names are dropped rather
      than rejecting the whole playlist) and goes through the same
      announceQueue()/QUEUE: round trip every other queue mutation
      does, rather than Python writing self.preset_queue directly.
      Also added a Loop Queue toggle (win.loop-queue, a stateful
      action bound straight to a Gtk.ToggleButton in the Queue
      dialog's header via action_name) - off keeps today's behavior
      (the queue drains and empties as it plays), on rotates each
      played item to the back instead of discarding it, so the same
      queue/playlist repeats indefinitely without falling through to
      shuffle/sequential once it's been played through. Verified:
      clean startup both with no saved playlists and with a
      hand-written playlists.json: every widget-construction step
      build_playlists_dialog/build_playlist_row use (including
      Adw.PreferencesGroup.add/remove used standalone, outside a
      PreferencesPage) was also exercised in isolation to confirm no
      errors. Not independently verified by hand: actually clicking
      through Save/Load/Delete/Loop in the running dialogs (no GUI
      automation available in this environment).
- [x] Settings profiles - a fourth "Profiles" tab in the Preferences
      dialog itself (alongside Audio/Playback/Rendering, rather than a
      separate top-level dialog - profiles are snapshots *of* those
      other tabs, so they belong inside Preferences, not next to it)
      lists named, saved snapshots of every Preferences control
      (sensitivity, cycle interval/jitter, blend time, mesh size,
      framerate, render scale, anti-aliasing, beat cycle/mode/
      sensitivity/cooldown/silence floor), with Load/Delete per row
      and a "Save Current Settings as Profile…" row at the top
      (Adw.AlertDialog with a name entry). Persisted as JSON at
      `$XDG_CONFIG_HOME/melange/profiles.json` (the Flatpak-sandboxed
      config dir - no manifest permission changes needed). Loading a
      profile calls each control's own existing setter
      (`Gtk.Scale.set_value`/`Adw.SwitchRow.set_active`/
      `Adw.ComboRow.set_selected`), which re-fires that control's
      already-wired change handler - so applying a profile reuses the
      exact same path a manual slider drag would, rather than needing
      a second way to push values into the visualizer. Verified: clean
      startup with the new dialog/actions wired in, the sandboxed
      config directory gets created on first launch, a hand-written
      profiles.json with two profiles loads back without error, and
      every getter/setter pair `profile_fields()` relies on was
      exercised directly against real widgets outside the full app.
      Not independently verified by hand: actually clicking Load/Save/
      Delete in the running dialog (no GUI automation available in
      this environment - same limitation noted elsewhere in this
      file).
- [x] Trimmed unused WebKit settings/persistence (webview.py) -
      audited every `WebKitSettings enable-*` flag against what the
      page actually uses (confirmed via grep: no localStorage/
      IndexedDB, no `<video>`/MediaSource, no links, no real
      navigation - presets switch entirely via JS, not URL changes -
      and native GTK fullscreen is used instead of the JS Fullscreen
      API). Disabled html5_database, html5_local_storage, page_cache,
      mediasource, media_capabilities, fullscreen (JS API),
      resizable_text_areas, tabs_to_links, smooth_scrolling, and
      site_specific_quirks. Left webaudio/webgl/javascript/media_stream
      alone - all genuinely used (WebGL rendering, Web Audio pipeline,
      getUserMedia for microphone mode). Also switched to an ephemeral
      `WebKit.NetworkSession` instead of the default persistent one -
      this page only ever talks to its own localhost server and has
      nothing worth remembering between runs, so there's no reason to
      pay for on-disk cookie/HTTP-cache/database backing stores.
- [x] Lazy-load the baron preset pack instead of eagerly loading all
      ~760 presets (5+MB combined) at startup. Root cause:
      butterchurn-presets-baron's own generated `dist/index.js` does
      `presets[name] = await import('./presets/<name>.json')` as a
      top-level await for every single preset, unconditionally, the
      moment the module is imported - ES module evaluation blocks
      until all of a module's top-level awaits settle, so just
      importing that module (even only to ask it for preset *names*)
      forced every preset's full JSON payload to be fetched and parsed
      up front. Confirmed via the real request log: previously
      hundreds of individual preset `.js` GETs fired in the first
      couple seconds of every launch, before a single preset had even
      been chosen. Fixed by bypassing that module entirely - main.js
      now uses `import.meta.glob()` (non-eager) directly against the
      raw preset JSON files to get names/loaders without invoking any
      of them, and only resolves+parses a given preset's content
      (`resolvePreset`) the moment it's actually about to be shown,
      caching it after that. Verified after the change: startup does
      exactly one asset fetch (the main JS bundle) instead of
      hundreds, and dozens of distinct baron presets loaded correctly
      with zero errors during real navigation. Core butterchurn-presets
      (the non-baron base pack) is left as-is - it's a single
      pre-bundled ~640KB file with no per-preset splitting possible
      without forking it, small enough not to be worth chasing.
      Steady-state RSS during active browsing wasn't cleanly
      isolated - the window is on the real desktop and something/
      someone was actively navigating through many presets during
      testing, which confounds any before/after RSS comparison once
      navigation starts (each newly-viewed preset, lazy or not, costs
      a real WebGL shader compile) - but the startup-cost elimination
      itself is unambiguous and directly verified.
- [x] Investigated RAM usage (WebKit) - watched RSS of the python/GTK
      process, WebKitNetworkProcess, and WebKitWebProcess over several
      minutes while idle with system audio playing. WebKitWebProcess
      climbed noticeably during the first ~30s (warm-up: JIT tiering,
      shader/texture caches) then oscillated in a bounded range rather
      than growing without limit - not a hard leak. Found and fixed
      two real inefficiencies in the audio hot path that were driving
      unnecessary allocation/GC churn on every chunk (tens of times a
      second, for as long as audio plays):
      1. The AudioWorkletProcessor's PCM sample queue (main.js,
         `setupPCM`) was a plain JS array using `push`/`shift`/
         `splice` - all O(n), called from the real-time audio
         callback (~344 times/sec). Replaced with a fixed-capacity
         ring buffer (typed arrays + read/write indices) for O(1)
         push/pop with no per-sample allocation.
      2. `receiveAudio` (main.js) posted the de-interleaved L/R
         Float32Arrays to the worklet via `port.postMessage()` without
         a transfer list, so the browser structured-clone (copied)
         both arrays on every chunk instead of transferring them
         zero-copy. Now passes `[left.buffer, right.buffer]` as the
         transfer list.
      A third, structural cost was found but not fixed this pass - see
      "Reduce audio-bridge overhead further" above.
- [x] Fixed a double-window bug when launching from GNOME Shell -
      `do_activate()` (main.py) unconditionally created a new
      `MelangeWindow` every time it fired, but GApplication's
      "activate" isn't guaranteed to fire only once (GNOME Shell's
      D-Bus activation path can trigger it more than once for what
      looks like a single launch). Fixed by checking
      `get_active_window()` first and only creating a window if none
      exists yet.
- [x] Scroll-wheel preset navigation - scroll up for next, scroll down
      for previous, throttled (0.35s) so one physical wheel click
      doesn't fire multiple changes
- [x] Shuffle enabled by default (both the JS default and the menu
      toggle's initial state)
- [x] Lower Mesh Size (min 8 -> 2) and Render Resolution Scale
      (min 0.25x -> 0.1x) slider minimums
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
