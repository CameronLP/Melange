# TODO

## Urgent

- [ ] **BUG**: In fullscreen, if the mouse cursor comes to rest over
      the window (rather than moving off it or leaving entirely), the
      toolbar/cursor never auto-hides. Reported by the user, not yet
      investigated. The auto-hide path is `mouse_move` ->
      `reveal_toolbar` (arms a 3s `hide_timer`) -> `hide_toolbar`
      (window.py) - `mouse_move` only resets that timer on genuine
      cursor movement (a `MOVEMENT_THRESHOLD_PX` guard specifically
      added to ignore WebKit's ~60Hz synthetic motion-event replay at
      the last real cursor position, see the comment above it), so a
      *stationary* cursor shouldn't keep re-arming the timer and
      `hide_toolbar` should still fire on its own. Likely candidates:
      `mouse_over_toolbar` getting stuck `True` (set by
      `toolbar_enter`, cleared by `toolbar_leave` - if the cursor is
      resting somewhere that fires enter without ever firing leave,
      `hide_toolbar` returns early every time), or the motion
      controller/hit-testing behaving differently over the WebKit
      surface specifically in fullscreen. Not yet reproduced or
      root-caused in this environment.
- [ ] Look for any remaining audio-visual latency/lag - the PCM AudioWorklet's unbounded sample queue (main.js) was found and fixed (capped at 50ms, see Done), which was the most likely source of the originally reported ~0.5s pause/resume delay. Not yet re-verified end-to-end, and there are other unexamined points earlier in the pipeline that could still add lag: GStreamer buffer/latency-time on the capture pipeline (start_system_audio, window.py), GLib.idle_add scheduling in on_audio_sample, and WebKit's evaluate_javascript IPC round-trip for each chunk.
- [ ] Some presets still don't seem to react much to audio - real bugs affecting reactivity have already been found and fixed (mono/stereo interleaving corruption, PCM worklet chunk truncation, unbounded queue latency - see Done), but the complaint has resurfaced since. Worth checking again whether this is a genuine remaining bug or just preset-design diversity (many community MilkDrop/Butterchurn presets are deliberately more ambient/subtle than others) - not yet determined which.
- [ ] **UNRESOLVED**: mirror window (mirror_window.py, see Done for
      the feature itself) shows broken/frozen content when maximized
      on a specific real setup - a 1080p secondary monitor rotated 90
      degrees. Confirmed (via the user, since this environment has no
      rotated monitor to test against): the primary window maximizes
      correctly on that same screen, and the mirror *window itself*
      resizes to the correct dimensions - only the mirrored picture's
      content breaks. A related, separate symptom was also reported
      and fixed with higher confidence: clicking the mirror while
      maximized made the picture go blank/grey, traced to
      `on_window_drag_pressed` calling `begin_move()` on an
      already-maximized surface (a meaningless request) - now skipped
      whenever `is_maximized()` is true.

      For the freeze itself, three targeted fixes were tried, each
      confirmed (via temporary debug instrumentation) to fire
      correctly with no errors, and each reported as insufficient on
      its own:
      1. `notify::maximized` -> recreate the `Gtk.WidgetPaintable`
         outright (`refresh_paintable`) rather than just
         `queue_draw()` - a plain redraw request was tried first and
         confirmed not to help, hence the stronger reset.
      2. `notify::scale-factor` -> same reset, added defensively for
         monitor changes generally, not confirmed relevant to this
         specific report.
      3. `notify::is-active` -> same reset, added because the user
         directly observed that clicking a *different* window (so the
         frozen mirror loses active state) unfroze it on its own, with
         no maximize/scale-factor change involved.
      A fourth approach - an unconditional `refresh_paintable()` every
      2 seconds via a timer, as a guaranteed self-healing fallback
      regardless of the exact trigger - was tried and explicitly
      confirmed by the user to still not fix it, then removed per
      their request rather than left in as dead weight.

      All three signal-based triggers above are still in place (they
      don't hurt, and might help *some* cases even if not this exact
      one). What's not yet tried: this could be a WebKitGTK/Mesa/
      Wayland-compositor-level interaction with rotated output
      transforms specifically (not a GTK-widget-level state GTK
      itself exposes a signal for at all, which would explain why
      every GObject-property-notify-based trigger has come up short),
      in which case no amount of `Gtk.WidgetPaintable`-recreation
      timing is the real fix - something more fundamental (e.g.
      forcing the mirror window's GDK surface itself to fully
      reallocate, or moving away from WidgetPaintable-based mirroring
      for this case) would be. Genuinely blocked on further progress
      without either access to a rotated-output setup to test against
      directly, or a way to capture WebKit/Mesa/Mutter-level logs from
      that specific machine at the moment it happens.

## In progress / not started

- [ ] `badShaderPattern`'s belt-and-suspenders check (main.js,
      `window.loadPresetFile`, see the bvecN &&/|| bug in Done) has a
      false-positive case, found by batch-converting a large random
      sample (800 files) of a real-world preset collection
      (`presets-cream-of-the-crop`, external to this repo) through
      `convertPreset` + `repairBadShader` and checking the result:
      3/800 (all by the same author, "amandio c, flexi") were flagged
      and would be refused, but their converted GLSL is actually
      valid. The regex (`/bvec[234]\s*\([^;{}]*?\)\s*(&&|\|\|)/`) only
      checks "does a `bvecN(...)` call appear, followed eventually by
      `&&`/`||` after some balanced parens" - it doesn't verify the
      `&&`/`||` actually applies to the `bvecN(...)` result itself.
      These presets' converted shaders legitimately construct a
      `bvecN(...)` from scalar-bool component expressions where one
      component happens to itself be a `scalarBool && scalarBool`
      sub-expression - e.g.
      `bvec3(tile1, tile2, (bool(...) && tile2))` - which is valid
      GLSL (scalar && scalar), but trips the regex because a `)` +
      `&&` sequence still occurs somewhere inside the outer
      `bvecN(...)` call's argument list. Not fixed - the existing
      check is deliberately conservative ("refusing to load is much
      safer than risking the renderer hang"), and a more precise
      fix would need to track paren nesting depth relative to the
      bvecN call's own top-level argument boundaries (distinguishing
      "&&/|| immediately after the bvecN call closes" from "&&/||
      inside one of its arguments") rather than trying to patch the
      regex. Low impact (0.4% of a large real-world sample) - noted
      here rather than fixed blind, since this environment can't
      actually render the "fixed" GLSL in WebGL to confirm a change
      doesn't let a genuinely bad case back through.
- [ ] Add a maximize button to the primary window too - currently only
      the mirror window has one (it needed a dedicated header button
      since it has no native decorations either; the primary's own
      header bar is hidden/auto-hide with no visible window controls
      at all today, so this needs a similar explicit affordance -
      likely a toolbar button and/or a menu entry, alongside or
      instead of relying on a window-manager keybinding).
- [ ] Translation support - the GNOME-app-template scaffolding for
      this is already present (po/meson.build, po/LINGUAS,
      po/POTFILES.in, `translatable="yes"` markers throughout
      window.ui, `from gettext import gettext as _` used for a few
      strings in main.py) but po/LINGUAS is empty - no language has
      actually been translated yet. More importantly, POTFILES.in only
      covers window.ui + main.py + window.py; checked and virtually
      none of window.py's own user-facing strings (toasts, tooltips,
      dialog headings/bodies, row titles - most of what's been added
      this session, since most new UI was built directly in Python
      rather than window.ui) are actually wrapped in `_()` at all, and
      mirror_window.py (also all-Python) isn't in POTFILES.in or
      gettext-wrapped either. Real translation support means auditing
      and wrapping those strings, not just adding language files.
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
- [ ] A larger preset browser window - the dialog itself is still a
      fixed 560x560 even now that it holds three tabs (Presets/
      Favorites/Queue, see Done) rather than just one list; worth
      revisiting now that there's more to fit.
- [ ] Optional "now playing" overlay in the corner of the canvas
- [ ] Frequency-range control for what feeds the visualizer (so presets that only react to bass, etc. can be tuned)
- [ ] Logo + symbolic icon polish - the scalable app icon was replaced (Bottles-based, data/icons/hicolor/scalable/apps), but the symbolic icon at data/icons/hicolor/symbolic/apps/com.cameronlp.Melange-symbolic.svg is still the original template placeholder and doesn't match
- [ ] Cursor auto-hide in fullscreen - WebKit manages its own cursor over page content and overrides host-level GtkWidget.set_cursor(), so this needs to be driven from inside the page (JS toggling a `cursor: none` CSS class) instead
- [ ] Reduce memory usage further - real measurement (not guessing) already found and fixed several things: eager loading of the whole baron preset pack at startup, GC churn in the audio hot path, and some unused WebKit persistence/features (see Done). What's left is WebKitGTK's own multi-process baseline itself (UI + network + web-content processes, each a full engine instance) - measured at roughly 500-650MB total for a single window even after the above fixes. Trimming that further means either the WebSocket audio-bridge change above, or the native-rendering direction below; see that for the real lever.

### Visualizer settings ideas

- [ ] Silence auto-pause - freeze/dim rendering when no audio is detected for a while, to save CPU/GPU
- [ ] Global post-processing tint/gamma - a brightness/color adjustment layered on top of whatever the preset renders
- [ ] Shuffle pool weighting/exclusion - let shuffle skip specific packs (e.g. exclude baron), or restrict it to favorites only now that those exist

### Bigger feature ideas

- [ ] Slide-in sidebar for the Queue/Playlist - a panel that slides in
      over the visualizer (edge-anchored, like the on-canvas nav-arrow/
      favorite overlays) showing the current queue or loaded playlist,
      reorderable in place, instead of requiring the separate Presets
      browser dialog - so the up-next list stays visible without
      leaving fullscreen or interrupting the visualizer
- [ ] Experimental: togglable auxiliary visualizer windows - separate
      small windows (VU meter, X-Y/Lissajous oscilloscope scope, etc.)
      driven from the same real-time audio data as the main
      visualizer, each independently opened/closed rather than baked
      into the main canvas. Likely built on the existing Mirror
      Windows infrastructure (mirror_window.py, window.py) - a
      separate `Gtk.Window` per aux view - rather than a wholly new
      window-management path, though the content itself won't be a
      pixel mirror of the main canvas (each shows its own
      analysis/rendering of the audio, not what the main preset is
      drawing). Supersedes the old standalone "X-Y scope visualizer"
      idea - folds it in as one of several possible auxiliary window
      types instead of a one-off feature.

      First cut in progress: VU Meter, X-Y Scope, Spectrum, and
      Spectrogram (a pure-Python FFT, no numpy) aux_window.py windows,
      each toggled from a new "Visualizer Windows" menu. Per-window
      settings popover (color, bar count, decay, frequency labels,
      mirror-reflection-below), fullscreen toggle, and the same
      auto-hiding header/overlay-button style and drag-from-anywhere
      as the main window/mirror windows - the settings/fullscreen
      overlay buttons were briefly not matching that style (see Done
      once committed) because Gtk.MenuButton's CSS node name is
      "menubutton", not "button", so style.css's
      "button.nav-arrow-button" selector never matched it - switched
      to a plain Gtk.Button with the popover opened/closed by hand
      (manual `.set_parent()` + `.popup()`) to work around it.

      That workaround turned out to be the wrong fix: reported from
      real usage, the settings gear didn't open a popover at all.
      Reverted to Gtk.MenuButton (GTK's own purpose-built button-that-
      shows-a-popover widget, `set_popover()` instead of driving
      `Gtk.Popover.popup()`/`popdown()` by hand - the same widget the
      hamburger menu itself already uses successfully elsewhere in
      this app) and fixed the actual styling problem instead of
      routing around it: style.css's selectors widened from
      "button.nav-arrow-button" to plain ".nav-arrow-button" (no type
      qualifier), so they match a "menubutton" CSS node too. Not yet
      re-confirmed against the live report, since this environment
      has no GUI interaction capability to click it - the fullscreen
      button's placement (header.pack_end, same as MirrorWindow's own
      header fullscreen button) was also separately reported as not
      appearing in the aux window's top bar, but on inspection the
      code already does this identically to MirrorWindow; possibly a
      stale Flatpak build rather than a real gap - worth a clean
      `flatpak-builder --force-clean` rebuild before assuming
      otherwise.

      VU meter was also found reading yellow/red almost constantly -
      it drove off the instantaneous per-chunk sample peak rather than
      RMS, and loud/modern masters routinely have individual samples
      near full scale even when the track isn't objectively "hot".
      Switched to RMS (average power, like a real VU meter's
      ballistics) with an empirical VU_GAIN so normal material still
      uses the full visible range.

      VU Meter later given a Style choice (settings popover) on top of
      the original continuous bars: LED Segments (a fixed stack of
      individually lit/unlit blocks, same fixed green/yellow/red zone
      convention as the bars but by segment position rather than a
      continuous fill - segment count adjustable) and Needle (a
      classic analog dial gauge per channel, pivot at the bottom,
      needle sweeping a fixed angle range - tinted via the Color
      picker, now shown for VU Meter too, unlike the fixed-color bars/
      LEDs, since a real analog needle's color isn't the information
      the way the bars'/LEDs' zone colors are). A general Labels
      switch (renamed from "Frequency Labels", now shown for VU Meter
      too) adds L/R channel labels beneath each column in every style,
      plus tick marks on the needle gauge specifically.

      Spectrum/Spectrogram's low end also had many bars/rows reading
      identically - FFT_SIZE was 512 (~86Hz/bin), and the log-spaced
      bar/row boundaries below a few hundred Hz are narrower than one
      bin there, so several consecutive low bars ended up reading the
      exact same FFT bin. Bumped to 2048 (~21.5Hz/bin, ~46ms added
      buffering latency, imperceptible for a passive visualizer) -
      the classic STFT time/frequency trade-off, not a bug in the
      binning logic itself; a large enough window still won't fully
      resolve the very lowest handful of bars, since log spacing gets
      arbitrarily narrow near 20Hz.
- [ ] "DVD Bounce" aux window built - a fifth Visualizer Windows entry
      (aux_window.py). A chosen icon (defaults to the real DVD logo -
      see below; a settings-popover dropdown offers Tux, the app's own
      logo, and a few symbolic alternates - Star/Heart/Sun/Smiley/
      Wireless) bounces around the window, classic-DVD-screensaver
      style: reflects off each wall, and
      cycles to a new random color from a fixed palette on every wall
      hit (kept separate from continuous audio reactivity - a
      constant hue shift and a discrete per-bounce one would fight
      each other) - the actual iconic part of the reference, not just
      a moving shape. Size and speed both scale continuously with the
      live audio level (RMS, same VU-meter-style attack/decay as the
      VU Meter window) on top of that, plus a user Speed multiplier in
      its settings popover. Symbolic icons (the alternates, not the
      full-color app logo) are recolored to the current bounce color
      by loading the icon as a plain GdkPixbuf, then using it as a
      Cairo mask against a flat color fill (push_group/pop_group/mask,
      the same technique the mirror-reflection fade elsewhere in this
      file already uses) - GTK's own symbolic-icon recoloring is CSS-
      driven and doesn't apply to a bare pixbuf load. Runs its own
      independent ~60fps GLib timer (dvd_tick) rather than only
      redrawing when push_audio delivers a new audio chunk (every
      other aux window kind's approach) - needed since the icon has to
      keep moving smoothly through silence and between chunks, not
      just when new audio happens to arrive; cancelled alongside the
      existing hide_timer in on_close_request. Icon lookups are
      cached (icon name + current pixel size, since size is
      continuously audio-reactive) rather than hitting the icon
      theme/disk on every tick. Not yet confirmed against a live
      display - this environment has no GUI interaction capability
      (same limitation as everything else in this file marked that
      way), so the bounce physics, wall-collision math, and icon
      recoloring are reasoned through and compile-checked but not
      eyeballed running.

      The default "DVD Logo" choice is the real thing - Wikimedia
      Commons' File:DVD_logo.svg, tagged there as public domain
      (PD-textlogo: simple shapes/text, below copyright's threshold of
      originality), fetched from the URL the user gave directly and
      bundled into melange.gresource (src/dvd-logo.svg,
      melange.gresource.xml) rather than the real trademarked artwork
      being reproduced by hand. Commons separately flags it as
      possibly trademark-protected regardless of copyright status -
      noted for awareness, not a blocker for a nostalgic screensaver
      parody use like this one, and the user's call to make as project
      owner. Loaded via GdkPixbuf.Pixbuf.new_from_resource_at_scale at
      its own aspect ratio (~2.27:1, not stretched into the square
      bounce bbox) and recolored via the same push_group/mask
      technique as the symbolic icon choices, since the source path is
      a plain black fill on transparent - falls back to an original
      hand-drawn "DVD" wordmark plate (Cairo text, not image-based) if
      resource loading ever fails (e.g. gdk-pixbuf's SVG loader/
      librsvg missing at runtime), so the window still shows something
      recognizable either way. A Tux option was also added the same
      way - given a URL to Wikipedia's File:Tux.svg, fetched Larry
      Ewing's real, original artwork (freely licensed - credited in
      README's Credits section per its usual terms) and bundled it
      (src/tux.svg) the same way as the DVD logo. Genuinely full-color
      (confirmed via its own SVG fill attributes: black, off-white,
      several yellow/orange shades), so shown as-is rather than
      recolored, unlike the DVD wordmark - the same treatment already
      given the full-color app-logo option. Falls back to an original,
      simplified Cairo-drawn penguin doodle (not a reproduction of
      Ewing's specific linework) if the bundled resource ever fails to
      load.

      Also given a settings-popover Size (base icon size, replacing
      the previously-hardcoded DVD_BASE_SIZE) and Audio Reactivity
      (a single multiplier scaling how strongly the continuous level-
      driven size/speed effects react, generalizing what were
      previously hardcoded 0.35/1.5 factors) slider, plus beat
      reactivity: a React to Beats switch and Beat Sensitivity slider
      driving a separate, snappier size pop layered on top of the
      continuous level-based scaling (dvd_tick) - detects a beat as a
      sudden jump in instantaneous level *above its own recent rolling
      average* (a ~1s history at the tick rate), not just "loud right
      now" (level, VU-ballistics-style, stays high throughout a
      sustained loud passage with no discrete beats in that sense). A
      self-contained Python-side detector, same idea as the app's own
      Energy Threshold beat mode (docs/beat-detection.md) but not
      reusing it directly - that one runs JS-side off a different tap
      of the audio graph, with no existing path forwarding its
      detections to aux windows at all.
- [ ] One more aux window idea raised, not yet built: a circular/
      radial spectrogram - same waterfall data as the rectangular
      Spectrogram, but frequency as radius and time as angle around a
      circle instead of x/y axes (Cairo `arc`-based wedge segments
      instead of rectangles - render cost per frame needs checking, an
      arc fill is pricier than a plain rectangle and there could be
      many more of them at usable resolution).
- [ ] Requested batch of further aux window work, not yet built - one
      at a time, each committed on its own once done:
      1. Peak Meter - a hardware-style peak meter distinct from the
         existing VU Meter: instantaneous per-channel peak (not RMS)
         with a peak-hold indicator (a thin line that jumps to a new
         peak instantly and decays back down slowly on its own timer,
         separate from the bar's own fast-attack/slow-release
         ballistics) and a dBFS scale, since peak and VU-style average
         loudness are genuinely different readings professionals
         watch for different reasons (peak for clipping headroom, VU
         for perceived loudness) - not just a reskin of the VU Meter.
      2. Oscilloscope - a scrolling time-domain waveform (amplitude vs
         time, both channels), distinct from the existing X-Y Scope
         (which plots L against R, not either channel against time).
      3. Vector Scope - a proper phase-correlation "goniometer" style
         display (traditionally rotated 45° from a plain L/R X-Y
         plot, so mono material draws a vertical line and out-of-phase
         material spreads horizontally), with intensity/persistence
         trails, L/R/M/S axis labels, and a phase correlation reading -
         a more information-dense relative of the existing X-Y Scope,
         not a duplicate of it.
      4. 3D Terrain Map Spectrogram - the existing Spectrogram's
         waterfall data (magnitude per frequency bin per time column)
         rendered as an oblique/isometric pseudo-3D ridge-line terrain
         (height = magnitude) instead of the existing flat 2D heatmap -
         a new aux window kind, not a mode of the existing Spectrogram.
      5. A vertical-orientation setting for the existing (rectangular)
         Spectrogram - today it always scrolls left-to-right (time on
         x, frequency on y, newest column at the right edge); this
         adds a settings-popover toggle to instead scroll top-to-bottom
         (time on y, frequency on x, newest row at top or bottom).
      6. Pipes - a "3D Pipes"-screensaver-style aux window: a handful
         of colored tubes turning corners and filling the window on a
         simple grid-walk, not audio-analysis-driven the way the
         others are - live audio just modulates pipe speed/spawn rate,
         for consistency with the rest of this feature rather than
         because the reference screensaver itself reacts to anything.
- [ ] **BUG**: some of the aux visualizer windows above (VU Meter/X-Y
      Scope/Spectrum/Spectrogram) reportedly don't react to audio in
      some cases - not yet reproduced or root-caused in this
      environment (no real speaker/mic loop to confirm against).
      Worth checking: whether it's every window of a given kind or
      only some sessions/some audio sources; whether it's specific to
      one kind (e.g. Spectrum/Spectrogram need a full FFT_SIZE-sample
      rolling buffer before their first real frame -
      aux_window.py's `push_audio`/`update_spectrum_levels`/
      `update_spectrogram_columns` - so a very short burst of audio
      might never fill it) or affects all of them equally (which would
      point at the shared forwarding path instead -
      `forward_audio_to_aux_windows`/`on_audio_sample` in window.py);
      and whether it only affects windows opened *after* audio
      playback already started, versus ones open from the start.
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

- [x] Per-row "add to playlist" button (bookmark-new-symbolic) next to
      the existing favorite-star/add-to-queue buttons on every row in
      the Presets and Favorites tabs (they share one row-rendering
      function, `build_preset_search_list`, so both got it for free).
      Previously the only way to get a specific preset into a saved
      playlist was to build/reorder the whole queue first and then
      "Save Current Queue as Playlist…" - this adds it straight to an
      existing playlist (or a brand-new one) in one click, the same
      way the star adds straight to favorites. Clicking it opens a
      small ad-hoc `Gtk.Popover` (built fresh each click, not a cached
      `Gio.Menu`, since which playlists exist can change between
      clicks) listing every saved playlist plus a "New Playlist…" row;
      picking an existing one that already contains the preset is a
      no-op with a toast rather than a duplicate entry.
      `playlists.json`'s existing shape (`{"name", "presets"}` per
      playlist) needed no changes - this is just a second way to
      mutate the same `presets` list `save_playlist_clicked` already
      writes, reusing `save_playlists`/`refresh_playlists_list`
      directly. `refresh_playlists_list` picked up one real bug: it
      unconditionally touched `self.playlists_group`, which only
      exists once the Playlists dialog has been opened at least once -
      fine for its previous callers (all inside that dialog already),
      but this button can now trigger it before that dialog has ever
      been built, which would have crashed with an `AttributeError`.
      Fixed with an early return when `self.playlists_dialog is None`.
      Verified end-to-end via a temporary debug action added to a real
      running instance (via D-Bus) and removed before committing:
      exercised creating a brand-new playlist from a preset, adding a
      second preset into that same existing playlist, and re-adding a
      preset already in it (confirmed genuinely a no-op - the playlist
      stayed at 2 presets, not 3) - both with the Playlists dialog
      never opened yet and with it already open (the
      previously-unguarded refresh path), all against real
      `playlists.json` contents on disk, no tracebacks either time.
      Not independently verified by hand: actually clicking the new
      button and seeing the popover itself (no GUI interaction
      capability in this environment).
- [x] Reorganized the hamburger menu: "_Browse Presets…" and
      "_Queue…" (previously two flat items) are now a "Presets"
      submenu containing Browse Presets…/Favorites…/Queue…/
      Playlists…, matching the existing "Mirror Windows" submenu
      pattern (window.ui). Added `win.show-favorites` and
      `win.show-playlists` `Gio.SimpleAction`s (window.py) so those
      two - previously only reachable from inside the preset browser
      or Queue tab - get their own menu entries and, matching the
      other browser tabs, shortcuts-dialog rows. Fixed
      `show_playlists_clicked`'s signature (`self, button` ->
      `self, action, param`) since it's now a GAction handler too,
      not just a `Gtk.Button` "clicked" callback, and switched the
      Queue tab's Playlists button to `action_name="win.show-playlists"`
      instead of a manual `.connect`.

      Consolidating two flat items into one submenu shifts every
      later item in that menu section down by one, so the hardcoded
      `mirror_windows_submenu = section2.get_item_link(5, ...)` index
      in `window.py`'s `__init__` had to move to `4` - missing this
      would crash at startup, since `get_item_link` on the wrong
      index either returns `None` (making the very next line, the
      open-mirrors-section lookup, raise `AttributeError`) or the
      wrong submenu (making `rebuild_mirror_windows_menu` corrupt an
      unrelated menu). Verified via a full rebuild + relaunch: app
      starts with no errors, `org.gtk.Actions.List` on the primary
      window shows `show-favorites`/`show-playlists` registered, and
      D-Bus `Activate` calls for `browse-presets`, `show-favorites`,
      `show-playlists`, `new-mirror-window`, `present-mirror`, and
      `close-all-mirrors` all round-tripped cleanly with no
      tracebacks in the log - confirming the Mirror Windows submenu
      (and its now-shifted index) still resolves and rebuilds
      correctly.
- [x] Window title reflects a loaded playlist (`Melange (Playlist
      Name) - "preset name"`), plus a way to unload one - an
      "Unload Playlist" button (media-eject-symbolic) next to Loop/
      Shuffle/Playlists in the Queue tab's control row.
      `current_playlist_name` (window.py) is set on load and cleared
      on unload; `build_window_title` composes the full title from it
      plus `current_preset_name`, called from both the existing
      PRESET_NAME handler and the two playlist load/unload paths so
      the title stays correct regardless of which one last changed.
      Unloading only clears the name association and title - the
      queue's actual contents are left untouched (loading a different
      playlist, or a genuinely empty queue, are the existing ways to
      change what's actually queued; "unload" specifically means
      "stop calling this the X playlist," not "clear the queue").
      Verified end-to-end via a temporary debug action exercising the
      full save/load/unload sequence against the real running app -
      confirmed the title reads exactly
      `Melange (TestPL) - "$$$ Royal - Mashup (197)"` after loading and
      correctly reverts to `Melange - "$$$ Royal - Mashup (197)"`
      after unloading - before removing the scaffolding and test data.
- [x] Favorite presets - a star toggle in the corner of the
      visualizer (Gtk.Overlay, same pattern as the nav arrows -
      starred-symbolic/non-starred-symbolic, reflects and toggles
      favorite status for whatever preset is currently showing,
      hover-reveals with the toolbar) plus one on every row in the
      preset browser. Favorites are a purely Python-side concept
      (window.py) unlike the queue - JS never needs to know what's
      favorited, since it doesn't drive any playback decision the way
      the queue does - persisted as JSON at
      `$XDG_CONFIG_HOME/melange/favorites.json` alongside profiles/
      playlists. Also bound to a new `F` keyboard shortcut
      (win.toggle-favorite) and added to the Keyboard Shortcuts
      dialog/README.

      Restructured the preset browser (previously a single searchable
      list) into a tabbed dialog - Presets/Favorites/Queue
      (Adw.ViewStack + Adw.ViewSwitcher in the header, same tabbed
      pattern already used for Preferences) - on request, so Favorites
      has a proper home and Queue isn't a separate dialog anymore.
      Presets and Favorites share the same search/filter/row-rendering
      code (build_preset_search_list), just backed by a different
      Gtk.StringList; each row now has both a favorite-star and the
      existing "add to queue" button. The Queue tab reuses the
      existing queue list/drag-reorder code verbatim - only its
      Loop/Shuffle/Playlists controls moved, from that dialog's own
      header (which doesn't exist anymore) to a small button row above
      the list, since Adw.ViewStack pages share one header (the
      switcher) rather than each page bringing its own.
      `win.show-queue` (and its `Q` shortcut) now opens the browser
      with the Queue tab pre-selected instead of a separate dialog -
      same action name, so the shortcut and the "_Queue…" menu item
      didn't need to change, just what happens when they fire.

      Verified end-to-end via D-Bus: clean startup, opening the
      browser (which builds all three tabs at once) produces no
      errors, win.show-queue correctly switches to the Queue tab,
      the relocated Loop Queue/Shuffle Queue stateful toggles work
      (via SetState, not Activate - a real D-Bus invocation mistake on
      my own part caught and corrected during testing, not a bug in
      the app), toggle-favorite persists correctly to disk, and
      reopening the (now-cached, built-once) dialog afterward still
      works. A separate false alarm was caught and correctly
      diagnosed rather than chased as a bug: an early manual toggle
      test appeared to show removal failing, but was actually the new
      30s default auto-cycle changing the current preset *during* the
      test's own investigation time, so two consecutive toggles were
      silently operating on two different presets - confirmed by
      printing current_preset_name directly and re-testing with calls
      issued back-to-back.
- [x] "Mirror Windows" submenu listing currently open mirrors - on
      request, consolidated all mirror-related menu entries (New
      Mirror Window, Close All Mirrors, and this) into one submenu
      rather than scattering them across the flat menu plus a separate
      dynamic submenu, so it reads as one coherent group. Structured
      as two `<section>`s inside the one `<submenu>` (window.ui) - a
      static one (New Mirror Window/Close All Mirrors) and a dynamic
      one (`open_mirrors_section`, rebuilt at runtime), so rebuilding
      the list of open mirrors never has to touch the static items.
      Clicking a "Mirror N" entry presents that specific window, via
      one parameterized `win.present-mirror` action (an integer
      target - the mirror's number) rather than registering/
      unregistering a separate action per mirror as they open and
      close. `rebuild_mirror_windows_menu` (window.py) runs whenever
      the list changes - both when a mirror opens
      (`new_mirror_window_clicked`) and when one closes
      (`MirrorWindow.on_close_request` - missed on the first pass,
      caught by testing the close path specifically rather than only
      the open path, and fixed before committing). Shows a plain,
      unbound "No mirror windows open" placeholder when the list is
      empty. Verified end-to-end via D-Bus: clean startup (would crash
      immediately on bad menu-item-index retrieval math, same pattern
      already used for Audio Source), opening two mirrors, presenting
      each by number, closing them all, and opening a fresh one
      afterward all produced no errors - including specifically
      re-testing the close path after finding the missed rebuild call
      there.
- [x] Fleshed out keyboard shortcuts - most actions previously only
      reachable via the hamburger menu/header buttons now have
      accelerators too (main.py): Ctrl+O (load preset file), Ctrl+F
      (browse presets), Q (show queue), Ctrl+M (new mirror window),
      Ctrl+Shift+M (close all mirrors), Ctrl+, (preferences) - picked
      to follow well-worn cross-app conventions where one exists
      (Open/Find/Preferences) and to avoid colliding with anything
      already bound otherwise. The Keyboard Shortcuts dialog
      (shortcuts-dialog.ui) picks up each one's actual bound key
      automatically (AdwShortcutsItem's action-name property reads it
      from the accelerator registered for that action, so the two
      can't drift out of sync) - reorganized into Visualizer/Presets/
      Windows/General sections now that there's enough to warrant it,
      and added an explicit-accelerator entry (not action-based, since
      Escape-to-exit-fullscreen isn't a GAction) for Escape, which was
      previously undocumented there despite being real, working
      behavior. README's shortcuts table updated to match. Verified:
      clean template build (would fail loudly on a bad accelerator
      string or malformed .ui), clean startup, opening the Shortcuts
      dialog itself produced no errors, and every action referenced
      from it is confirmed still correctly registered
      (org.gtk.Actions.List). Not independently verified: actually
      pressing each new key combo (no keyboard-input-injection
      capability in this environment).
- [x] Default Cycle Interval to 30s instead of Off - the Preferences
      slider's initial value changed to 30.0 (window.py), and main.js
      now calls `window.setCycleInterval(30)` at startup so the timer
      actually starts, not just so a variable defaults to a number
      nothing schedules. Real bug caught while doing this: the first
      attempt placed that call right after the initial preset loads,
      near the top of the module - but `window.setCycleInterval` isn't
      defined until much further down the file (main.js runs top to
      bottom), so that threw "window.setCycleInterval is not a
      function" every launch. Moved to right before the existing
      `debug("APP_READY")` line, which is deliberately placed after
      everything else in the module has finished defining itself (see
      its own comment) - the same guarantee that already made it the
      right spot for Python to know it's safe to call into the page.
      Verified end-to-end: clean startup, and (via Monitor watching
      the actual log rather than assuming) the preset genuinely
      auto-advanced twice with no manual trigger, both times at
      roughly the 30s mark.
- [x] Fullscreen toggle button in the primary window's header bar
      (window.ui, `fullscreen_button`, bound via `action-name` to the
      existing `win.toggle-fullscreen` action - no new action needed).
      Its icon swaps between view-fullscreen-symbolic and
      view-restore-symbolic via a `notify::fullscreened` handler
      (`update_fullscreen_button_icon`) on the window itself, so it
      stays correct regardless of *how* fullscreen was entered/exited
      (the button, F11, or Escape all end up flipping the same
      property rather than needing individual updates). Removed the
      "Toggle Fullscreen" hamburger menu entry as redundant now that
      it's reachable via a visible header button as well as the
      existing F11 shortcut - three ways to reach the same action was
      one too many, and it was the least necessary of the three, so it
      was the one that got cut. Verified: clean flatpak-builder
      template validation (would fail loudly on a bad widget/property
      reference in window.ui), clean startup, and toggling
      win.toggle-fullscreen (the same action the button triggers)
      in and back out via D-Bus produced no errors, confirming
      notify::fullscreened fires and update_fullscreen_button_icon
      runs cleanly on both transitions. Not independently verified:
      actually clicking the button (no GUI interaction capability in
      this environment).
      Mirror windows (mirror_window.py) got the same button/icon-swap
      treatment on request, reusing each mirror's own existing
      win.toggle-fullscreen action the same way. Verified the same way
      as the primary: creating a mirror and toggling its
      win.toggle-fullscreen via D-Bus produced no errors entering or
      exiting fullscreen.
- [x] Preset-nav arrows moved to GTK - they used to be part of the
      page itself (HTML buttons drawn on the canvas, index.html/
      main.js), which meant a mirror window (showing only the
      webview's own rendered content via Gtk.WidgetPaintable) also
      showed them, as inert non-interactive clutter (Gtk.Picture never
      forwards input back to a paintable's source). Moved to two real
      Gtk.Button widgets (window.py), overlaid on the webview via a
      Gtk.Overlay wrapping it, with the same circular/translucent
      look and hover-reveal-then-fade behavior (now tied to the same
      toolbar auto-hide state as the header bar, via a new
      set_nav_arrows_visible, rather than a per-button CSS :hover
      zone) - so they no longer appear in mirror windows at all, and
      still look/behave the same in the primary window. The existing
      15%-edge-of-window carve-out in the drag-to-move gesture
      (on_window_drag_pressed) is kept, now guarding against dragging
      the window when a click lands on a nav button rather than
      WebKit's own hit-testing swallowing the click. The buttons call
      next_preset()/previous_preset() directly (same lock-check/toast
      path as every other trigger) rather than round-tripping through
      the JS debug-message channel the HTML buttons used to need -
      that channel's "NAV_NEXT" message is still used by the cycle
      timer (main.js scheduleCycleTick), so it wasn't removed
      entirely, only the now-dead "NAV_PREVIOUS" side of it (nothing
      sends that anymore) and the arrows' own listeners.
      Verified: clean startup (would have failed loudly if the
      Overlay/button construction were broken), win.next-preset/
      win.previous-preset (the same methods the buttons call) advance
      and reverse correctly with no errors, and creating a mirror
      window afterward still works with no errors and no second
      WebKitWebProcess spawned - confirming the webview's move into an
      Overlay didn't break WidgetPaintable's tracking of it. Not
      independently verified by hand: actually clicking the buttons or
      seeing the hover-reveal fade, and confirming visually that they
      no longer appear in a mirror window (no GUI interaction or
      screenshot capability in this environment).
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
      anything the page renders appears in the mirror too - there's
      only one underlying DOM, and Gtk.Picture doesn't forward input
      back to a paintable's source, so nothing in the page is
      interactive there. This included the on-canvas preset-nav arrows
      at first (inert clutter in the mirror as a result) - see the
      "Preset-nav arrows moved to GTK" entry below, which moved them
      out of the page entirely so this no longer applies to them.

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
      raise/focus the primary. A "Find Main Window" header button
      (guaranteed reliable - a plain button click has no
      click-vs-drag ambiguity to get wrong) does this unconditionally
      now, added after two attempts at double-click detection on the
      picture/header bar were each reported as not actually working:
      the first (a single GestureClick's "pressed" handler, calling
      begin_move() straight away whenever n_press was 1) had a real,
      identified bug - begin_move() grabs the pointer for an
      interactive move the moment the *first* press of a would-be
      double-click happens, before GTK ever gets a chance to recognize
      a second press following it, so the click sequence gets consumed
      by the move grab instead of delivered as a second discrete
      press. The fix (splitting drag and click-counting across a
      Gtk.GestureDrag, which only starts a move once real motion
      happens, and a separate CAPTURE-phase Gtk.GestureClick for
      double-click counting) was well-reasoned and didn't error, but
      was *also* reported as still not working - unverifiable further
      in this environment, which has no way to simulate real pointer
      gestures to confirm GTK's gesture arbitration behaves as
      expected. Both double-click handlers are left in place (harmless
      if they don't fire), but the button (`bring_to_attention`, see
      below) is now the actually-reliable way to do this.

      `bring_to_attention` (MelangeWindow) does three things together:
      present()s the window, reveals its toolbar even if it had
      already auto-hidden (present() alone would otherwise bring an
      empty-looking header-less window to the front), and briefly
      flashes the header bar to the accent color a few times via a CSS
      keyframe animation (`.melange-header.attention-flash` in
      style.css) - GTK4 has no OS-level window-shake/"demand
      attention" API to call into any more (Wayland deliberately
      restricts that kind of app-initiated attention-grabbing, unlike
      old X11 urgency hints), so a CSS-driven flash on the header
      itself is the standard GNOME-native substitute. Verified end-to-
      end (present + reveal + flash + the timer-based class removal
      after) via a temporary debug action, including calling it
      several times in rapid succession with no errors.
      The picture fills the window completely
      (Gtk.ContentFit.FILL) even if that distorts the aspect ratio,
      rather than the default letterboxed CONTAIN. "Close All Mirrors"
      is available from the primary's menu too. A single click on the
      mirror is skipped (rather than attempting to drag it) when it's
      already maximized - dragging a maximized
      surface is meaningless and was a likely trigger for a real
      corrupted-picture symptom, see the rotated-monitor freeze entry
      under Urgent, which also covers a still-unresolved freeze issue
      specific to this feature.

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
