# TODO

## Urgent

- [ ] **PERF**: Spectrogram aux window (aux_window.py) reported to
      cause lag - not yet fixed, but a likely cause is already visible
      from the code: `render_spectrogram` redraws its *entire* history
      (up to `SPECTROGRAM_COLUMNS` = 200 columns x `num_bars` bars,
      12,800 rectangle fills at the default settings, more with a
      higher bar count) on every single `on_draw`, and `on_draw` fires
      on every `push_audio()` call - which is every audio chunk
      forwarded from `window.py`'s `forward_audio_to_aux_windows`
      (tens of times/sec while audio plays, the same delivery rate
      noted elsewhere in this file for the main webview audio bridge),
      not just when `update_spectrogram_columns` actually appends a
      *new* column (throttled separately, to `SPECTROGRAM_FRAME_INTERVAL`
      = every 50ms). So ~199 of every 200 redraws re-fill columns that
      haven't changed since the last frame, for no visible benefit -
      only the newest column ever actually differs between two
      consecutive draws at the current 50ms column rate. Likely fix:
      cache the already-drawn history to an offscreen Cairo
      `ImageSurface`/`cairo.Group` and blit-plus-append instead of
      redrawing from scratch each time, or throttle `queue_draw`
      itself (in `push_audio`) to the same 50ms cadence for this kind
      specifically rather than firing on every audio chunk. The pure-
      Python FFT (`fft`, radix-2 Cooley-Tukey on `FFT_SIZE` = 2048
      samples, no numpy) is already correctly throttled to the 50ms
      column rate and is a secondary suspect at most - worth
      profiling rather than assuming, but the redraw path above is the
      more obviously wasteful one from a straight code read. Also
      worth checking: whether having Spectrum *and* Spectrogram open
      at once doubles FFT cost for no reason, since each keeps its own
      separate `spectrum_buffer`/FFT call with no sharing between
      windows.
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

- [ ] Disable the 3D mini visualizers (Terrain/Waterfall/Pipes) until
      their performance can be improved - requested, not implemented.
      Related to the already-logged Mini Visualizer efficiency TODO
      above (no profiling done yet for any of them) - this is a
      narrower, more immediate ask (hide/disable specifically the 3D
      ones from the menu in the meantime) rather than waiting on a
      real perf fix.
- [ ] FPS counter overlay for visualizers, toggleable - requested, not
      designed. "Including the mini ones" implies both the main
      window and every aux_window.py kind - unclear yet whether this
      is one shared per-window toggle or a single global setting,
      and where it would live in each window's own settings surface
      (main window has Preferences; aux windows have their own
      settings popover; mirror windows have neither an easy place for
      it, matching the earlier "these have no settings-dialog
      infrastructure" theme).
- [ ] Needle VU meter not vertically centered - reported bug, not
      investigated yet. draw_vu_needle's own comment describes a
      "pivots near the bottom of the cell" gauge shape - worth
      checking whether that's the actual root cause of what reads as
      "not centered" (a bottom-pivoted needle dial deliberately uses
      the *lower* portion of its cell by design, which may be exactly
      what looks off-center) or a separate real bug in its geometry
      math.
- [ ] Mini visualizer settings popover doesn't close on click-outside,
      and the gear icon itself sometimes doesn't respond - reported
      bug. This is a *known-unresolved* area, not a fresh report: a
      CAPTURE-phase click-outside fix was applied earlier this
      project's history, then a second layered fix (a notify::
      is-active-based popdown) made things actively worse ("now I
      cannot open the setting in the mini visualizers") and was fully
      reverted rather than patched further, since there was no way to
      click-test the result at the time - see that revert's own
      history for the full reasoning. The original CAPTURE-phase fix
      was left in place but explicitly "not yet confirmed as fully
      sufficient." This report confirms it isn't. "Gear icon sometimes
      doesn't respond" is a new symptom on top of that, and could
      plausibly share a root cause with the click-outside failure
      (some event/grab being swallowed or left dangling) rather than
      being two unrelated bugs - worth investigating together. Escape
      already working to close it is a useful clue: whatever's
      failing is specific to the click/pointer path, not the
      popover's dismissal logic in general.
- [x] (confirmed via reading the code, not yet fixed) VU LED segments'
      peak-hold outline doesn't use the custom Peak Hold Color -
      reported bug, root cause found: draw_vu_led's hold-segment
      outline (the "which segment the peak currently sits in" marker)
      still calls self.canvas_foreground_rgba(0.9) directly - it was
      never updated to go through peak_hold_draw_color() when Peak
      Hold Color was added this session. draw_vu_bar/draw_peak_bar/
      both Spectrum render functions all got that update; draw_vu_led
      was simply missed. Trivial one-line fix once picked up
      (cr.set_source_rgba(*self.peak_hold_draw_color(0.9))), just not
      done yet since this whole batch was requested as TODO-only.
- [ ] VU LED segments' "rainbow option does nothing" - reported.
      Real cause, found while looking at the peak-hold bug above:
      draw_vu_led's segment colors are hardcoded to the fixed green/
      yellow/red zone convention with no reference to self.color_mode
      or self.color at all - same as draw_vu_bar, which documents this
      as *deliberate* ("kept fixed - not the user color setting -
      since the zones themselves are the information"). So this may
      not be a bug in the sense of broken code so much as a design
      decision the user is now pushing back on for LED style
      specifically - needs a decision on whether LED segments should
      actually respect color_mode/rainbow (and if so, how that
      interacts with the zone-color convention the bar style
      deliberately keeps) rather than a pure code fix.
- [ ] Now Playing artwork doesn't appear - reported. Needs
      disambiguating before assuming a cause: this may be the already-
      known, already-decided limitation (no home-filesystem access,
      user's explicit choice - file:// art, e.g. Firefox's, was
      already confirmed to fail closed rather than display) rather
      than a new bug, *unless* it's also failing for https://-served
      art (e.g. Feishin's), which was expected to work fine under the
      existing sandbox permissions and was never actually confirmed
      end-to-end (the earlier verification only got as far as
      confirming the URL scheme, not a real successful image load, and
      a later attempt to test the real https:// URL was blocked by the
      permission classifier since it contained the user's own account
      password). Needs the user to say which case this actually is.
- [ ] Placeholder artwork when Show Artwork is on but no art is
      available for the current track - requested, not implemented.
- [ ] "Find a better graphics engine for spectrograms?" - open-ended
      research question, not investigated. Related to (but broader
      than) the already-logged Mini Visualizer efficiency TODO and the
      shared-FFT idea below - unclear whether this means a genuinely
      different rendering approach/library or just optimizing the
      existing Cairo one.
- [ ] Common/shared FFT for all mini visualizers - requested again,
      reinforcing the same idea already logged multiple times above
      (each aux window currently runs its own independent FFT per
      frame - real, confirmed duplicated work, not yet implemented).
- [ ] Audio not detected on launch - reported bug, not investigated.
      No details yet on reproduction (which audio source, timing,
      whether switching sources or reloading fixes it).
- [ ] Settings should persist between app launches - requested. This
      app currently has zero settings persistence beyond the current
      session, except Settings Profiles' own explicit named-snapshot
      save/load (profile_fields/save_profiles/load_profiles) - there's
      no GSettings schema or equivalent auto-persisted defaults layer
      at all. Related to (but broader than) the already-logged Export
      Settings TODO above - that one's about getting a snapshot out to
      a portable file on request; this one's about the app remembering
      its own current settings automatically across restarts with no
      user action needed, most likely via a real GSettings schema
      (data/ already has a place for one, per this project's standard
      GNOME app layout, but none exists yet) rather than reusing the
      profiles JSON file for this different job.
- [ ] Tunable X-Y Scope that can write text/draw images - requested,
      a much bigger feature idea than a simple setting. This is the
      "oscilloscope art"/vector-monitor genre (Lissajous-style X-Y
      traces shaped to spell text or draw recognizable images via
      carefully constructed waveforms) - would need real waveform-
      synthesis work, not just a rendering tweak to the existing X-Y
      Scope aux window. Not scoped or designed at all yet.
- [ ] Now Playing text transparency - requested (TODO-only, not
      implemented). Real gap found while noting this down: the
      existing Text Color Gtk.ColorDialogButton already lets picking
      a color with less than full alpha (GTK4's color dialog has an
      alpha slider by default), but apply_now_playing_text_style
      currently only reads color.red/green/blue into Pango.
      attr_foreground_new(r, g, b) - alpha is silently dropped
      entirely, so picking a translucent color from that dialog today
      already has zero visible effect. The real fix is probably just
      also inserting Pango.attr_foreground_alpha_new(int(color.alpha *
      65535)) alongside the existing foreground attribute - Pango
      keeps foreground color and its alpha as two separate attribute
      types, confirmed to exist via introspection earlier this
      session, not something requiring a new UI control at all.
- [ ] Setting to auto-scale Now Playing's text bigger in fullscreen -
      requested (TODO-only, not implemented). Would need to react to
      notify::fullscreened (already used elsewhere in window.py for
      the fullscreen button's own icon sync) and apply some multiplier
      on top of the existing Text Size setting rather than replacing
      it - e.g. a separate "Fullscreen Scale" factor, restoring the
      configured Text Size as-is on exiting fullscreen. Interacts with
      the still-open Scroll Long Titles box-sizing bug above (if the
      text gets bigger while fullscreen, whatever fixes that bug needs
      to also handle the box's fixed size changing when the scale
      factor itself changes, not just staying fixed within one scale).
- [ ] Scroll Long Titles bug - reported (TODO-only, not implemented):
      "the outline box changes size each scroll movement... the box
      [should] remain fixed size during scrolling." This is the direct
      flip side of the previous fix (start_now_playing_title_scroll
      setting max-width-chars to -1/ellipsize to NONE to stop GTK from
      clipping the already-correctly-rotating text) - removing that
      constraint fixed the truncation bug but means the label's own
      *natural* width now varies tick to tick with whatever substring
      is currently shown (different characters, different pixel
      widths), and GtkBox/the card naturally renegotiates its size to
      that natural width, producing the reported jitter.
      Real fix isn't "put the width limit back" (that's the bug that
      was just fixed) - it needs the label's *visible container* to
      have a genuinely fixed size while its *content* is allowed to be
      wider than that and get clipped, rather than sizing the
      container to the content. GTK4's per-widget set_overflow(Gtk.
      Overflow.HIDDEN) (used nowhere in this codebase yet) is the
      likely mechanism - clip a fixed-size viewport around the title
      label instead of relying on ellipsize/max-width-chars at all
      while scrolling. The fixed size itself would need computing once
      when a scroll starts (e.g. measure the label at the configured
      Text Box Width via Pango/measure(), not a live natural-size
      read that would reintroduce the same jitter) rather than derived
      from whatever's currently showing.
- [ ] Toolbar Hide Delay bug - reported (TODO-only, not investigated):
      setting Hide Delay to 0 ("Never") keeps the header bar/nav
      arrows visible as intended, but the mouse *cursor* itself still
      disappears in fullscreen regardless. Likely cause, not yet
      confirmed: cursor-hiding in fullscreen (search for
      Gdk.Cursor.new_from_name("none")/is_fullscreen() in window.py)
      is probably its own separate mechanism from schedule_toolbar_
      hide/hide_toolbar, and likely doesn't check toolbar_hide_delay
      at all - "Never" for the toolbar wouldn't currently imply
      "never" for the cursor unless that path is also gated on it.
- [x] Scroll Long Titles - requested ("if text title is too long for
      now playing, i want setting to have it scroll across"). New
      toggle - when on and a title's length exceeds Text Box Width,
      it scrolls as a rotating marquee instead of the normal static-
      plus-ellipsize display; a title that already fits just shows
      normally regardless of the setting. Implemented as a text-based
      marquee (rotating which substring of "title + separator +
      title" is shown, one character per 300ms tick) rather than
      actual pixel-position animation - reuses Text Box Width's
      existing character-count sizing directly as the visible window
      size, avoiding any need for real Pango/pixel measurement of the
      label's rendered width. An approximation (proportional fonts
      mean a fixed character count isn't a perfectly constant pixel
      width) consistent with how this card is already sized
      everywhere else, not a new kind of imprecision.
      Re-evaluated (started/stopped/restarted as needed, without
      resetting an already-running scroll for the same title) on every
      track change, on the Scroll Long Titles toggle itself, and on
      Text Box Width changes - widening or narrowing the window can
      push a title across the "needs to scroll" threshold either way.
      Verified in the sandbox: a long title starts scrolling
      immediately with the correct first window, the window genuinely
      advances over real time (not just an incrementing counter with
      no visible effect), re-feeding the identical title mid-scroll
      doesn't reset its position, a short title shows in full with no
      scrolling, and turning the setting off immediately shows
      whatever's currently playing in full instead of a stale
      truncated window.
      Bug fix, reported ("scroll long titles does not work after the
      font size or box width are changed"): confirmed by direct
      testing that the underlying rotation (scroll offset advancing,
      label text genuinely changing every tick) kept working correctly
      regardless of font size or width changes - the actual bug was
      that GTK's own ellipsize/max-width-chars on the title label
      (meant for the *static*, non-scrolling display) was fighting the
      manually-fed scroll text: once the character-count window's real
      pixel width exceeded what was actually available (easy at a
      larger font size, or after apply_now_playing_width unconditionally
      reset max-width-chars back to a finite value on every width
      change even while a scroll was already active), ellipsize
      clipped the already-correctly-rotating text down to a near-
      static truncated string - looked completely broken even though
      the content underneath never stopped cycling. Fixed two things:
      start_now_playing_title_scroll now sets max-width-chars to -1
      (unlimited) and ellipsize to NONE for the duration of the
      scroll, restored to the normal static values in stop_now_
      playing_title_scroll; and apply_now_playing_width now skips the
      title label specifically while it's actively scrolling, instead
      of unconditionally resetting all three labels' width limits
      every time (the real fix for the width-change half of the
      report - the scroll's own visible-window size already reads
      self.now_playing_width fresh every tick regardless, so the
      label's own max-width-chars constraint has no job to do while
      scrolling is active in the first place). Verified in the
      sandbox, reproducing both exact scenarios from the report: a
      width change during an active scroll no longer clobbers the -1/
      NONE override (confirmed on the actual GtkLabel properties, not
      just state variables), a font size change likewise leaves it
      undisturbed, and in both cases the label's actual rendered
      content is the full, untruncated (no "…") expected-length window
      on the very next tick.
- [ ] Now Playing display follow-ups, both requested (TODO-only, not
      implemented):
      (1) Widen the Fade Interval (Periodic Fade) slider's range -
      currently 1-15s - to support much longer periods, e.g. once a
      minute or every 2 minutes. Simple range change
      (build_now_playing_periodic_fade_seconds_control's
      build_slider_row bounds) once actually done.
      (2) A new "hide until a new song plays, then show for some
      period" mode - explicitly distinct from the existing Auto-Hide
      (built last session) in two ways worth getting right when this
      is designed: Auto-Hide's "change" key includes play/pause status,
      so pausing/resuming the *same* track already re-triggers a show -
      this new mode should presumably react only to an actual track
      change (title/artist), not play/pause. Auto-Hide also shows
      immediately the moment any MPRIS info first arrives (e.g. on app
      startup with something already playing) since its "last change"
      key starts as None, which differs from anything real - this new
      mode's framing ("hide *until* a new song plays") suggests it
      should probably stay hidden through that first/already-playing
      track too, only peeking on the *next* genuine change. Needs a
      decision on whether this becomes a variant of Auto-Hide (e.g. an
      "ignore play/pause" sub-option) or a genuinely separate third
      mode alongside it and Periodic Fade.
      Additional detail supplied afterward: the *show* transition for
      this new mode should itself fade in, not snap to visible - a
      real difference from Auto-Hide's existing behavior, which
      deliberately snaps in instantly on a change (see that feature's
      own comment: "a track changing is exactly the moment you want to
      see it, not fade into view") and only fades on the way *out*.
      This new mode wants both directions to fade. Reinforces treating
      it as a genuinely separate mode rather than an Auto-Hide
      variant, since it disagrees with Auto-Hide's own show behavior,
      not just its trigger condition.
- [ ] While fullscreen, disable Transparency Mode if it's on, and
      re-enable it on exiting fullscreen - requested (TODO-only, not
      implemented). Would need to distinguish "off because fullscreen
      forced it off" from "the user explicitly turned it off while
      fullscreen" (so exiting fullscreen doesn't turn it back on if
      they didn't want it in the first place) - probably a separate
      remembered "was enabled before fullscreen" flag rather than
      reusing self.transparency_mode_enabled directly for both jobs.
      Hooks into notify::fullscreened, same signal mirror_window.py's
      own fullscreen-icon-sync already uses.
- [ ] Transparency for the mini visualizer windows - requested (TODO-
      only, not implemented). Would follow the same pattern as the
      primary window (fade a content widget, add a CSS class for the
      window's own background - aux_window.py's windows already share
      the extend_content_to_top_edge/floating-header structure) and
      probably the same "keep it simple, no sliders" shape Mirror
      Windows got (a settings-popover toggle rather than the primary's
      full Opacity Level/Fade Speed pair - aux windows do have a real
      settings popover already, unlike mirrors, so sliders are more
      feasible here if wanted).
- [ ] Visualizer creator - requested to be recorded, not designed or
      scoped at all yet. Unclear what this actually means without
      more input: a UI for building a new mini-visualizer kind (aux_
      window.py-style, Cairo-drawn) without hand-writing Python? A
      MilkDrop/Butterchurn preset editor (equations/shaders) for the
      main visualizer? Something else entirely? Needs real scoping
      before any design work starts.
- [x] Two menu reorganizations, both requested together ("I think
      there should be a transparency toggle on the main window too,
      in the hamburger. Transparency should be in the hambrrger on the
      mirrors too", following on from "should some of the mirror
      window top bar icons be moved into hamburger menus?"):
      (1) Transparency Mode is back in the main window's hamburger
      menu (window-management section, after Immersive Mode) -
      reverses the earlier "should only be in settings" decision on
      request. Preferences > Appearance > Transparency Mode still
      exists too, for the Opacity Level/Fade Speed sliders a menu
      checkbox can't hold - this is just a quick on/off alongside it.
      (2) Mirror windows get a real hamburger menu for the first time
      (previously just direct header buttons, no menu at all) - Match
      Main Window Size and Find Main Window moved into it (one-off/
      infrequent actions, following the recommendation given when
      asked), and Transparency Mode moved into it too rather than
      staying the direct toggle button it was built as a message ago
      (explicit request this time, overriding that earlier
      recommendation to keep it direct). Fullscreen stays a direct
      header button - the one thing left that's genuinely toggle-
      style/quick-access, matching the primary window's own header
      keeping Fullscreen after everything else moved into its menu.
      Needed two new real Gio.SimpleActions (win.match-primary-size,
      win.find-main-window) on MirrorWindow, since menu items can only
      invoke actions, not the arbitrary clicked-signal callbacks the
      old direct buttons used - match_primary_size_clicked's signature
      changed from (self, button) to (self, action, param) accordingly
      (its body never used that first argument either way). Also
      stored the new menu button as self.menu_button (previously a
      bare local variable) to match the primary window's own
      convention and make it reachable for inspection/testing.
      Verified in the sandbox with a real primary + real mirror
      window: the primary's menu section is exactly [Mirror Windows,
      Mini Visualizers, Immersive Mode, Transparency Mode] with Mirror
      Windows' own positional index still resolving correctly
      (appending, not inserting, avoided yet another index bump - see
      that comment's ongoing history); the mirror's own menu is
      exactly [Match Main Window Size, Find Main Window, Transparency
      Mode]; Fullscreen remains a direct action-bound button; and all
      three mirror menu actions actually run when activated (Find Main
      Window confirmed to genuinely call primary.bring_to_attention()).
- [x] Transparency Mode on Mirror Windows - asked first ("can it be
      applied to mirrors? should it?"), answered with a recommendation
      (technically easy - mirror_window.py already uses the identical
      floating-header/extend_content_to_top_edge structure - but
      probably shouldn't be synced with the primary's own setting,
      since a mirror is often projected to a second monitor/TV for an
      audience, where fading it to reveal the desktop defeats the
      point most of the time; a real per-mirror opt-in toggle would
      make sense instead), then built on "yes, build it". Each
      MirrorWindow now has its own completely independent win.
      transparency-mode action/state - toggling the primary's
      Transparency Mode has no effect on any open mirror and vice
      versa. Deliberately simpler than the primary's version: a fixed
      50% opacity and a fixed 300ms fade (TRANSPARENCY_OPACITY/
      OPACITY_FADE_MS constants) rather than the primary's
      configurable Opacity Level/Fade Speed sliders, and a single
      Gtk.ToggleButton in the mirror's own header (view-conceal-
      symbolic, next to the existing fullscreen/find-primary buttons)
      rather than a settings popover - this window has no Preferences-
      dialog-style infrastructure to put sliders in, and a plain
      on/off toggle is what was actually asked for. Reuses the exact
      same mechanism as the primary (fade a content widget's own
      opacity - self.picture here, the mirror's equivalent of
      toast_overlay - plus the same .transparency-active CSS class for
      the window's own background, already a generic class-based rule
      in style.css so no CSS changes were needed beyond a comment).
      Verified in the sandbox with a real primary + real mirror
      window pair: the mirror has its own action independent of the
      primary's (toggling one doesn't affect the other), enabling
      fades self.picture toward 50% over the real ~300ms window (not
      just state assertions - sampled actual mid-fade opacity), and
      disabling fades back to fully opaque.
- [x] Three small naming/organization requests, all done together:
      (1) "move the beat detection to an experimental section" - Beat
      Detection is now its own Preferences page ("Experimental",
      applications-science-symbolic - confirmed present in the icon
      theme before using it, unlike the earlier Appearance-icon bug)
      instead of a group folded into Playback, dropping the
      description's own "Experimental -" prefix since the page itself
      now says that. Also gives a real home for any future feature
      that needs the same framing, rather than each one bolting its
      own "Experimental" description onto whatever page it happens to
      land in. (2) "is there a better name for 'visualizer windows'?"
      - asked as a genuine question, not a command; picked "Mini
      Visualizers" (matches how these have been described in
      conversation and TODO history all session - "miniviz", "the
      miniature widgets" - and reads as clearly distinct from "Mirror
      Windows", the other windows-related menu entry) and applied it,
      rather than just answering without acting, since renaming a menu
      label is trivial to redo if it's not the right call. (3) "rename
      'browser presets' to presets" - the "_Browse Presets…" menu item
      is now "_Presets…"; also renamed the header bar's magnifying-
      glass button tooltip from "Browse Presets" to "Presets" for
      consistency, since it opens the same dialog via the same
      win.browse-presets action (not explicitly requested, but the
      same rename left half-applied would have been inconsistent).
      Verified in the sandbox: the Experimental page's icon exists in
      the real icon theme, and the hamburger menu's actual labels are
      "_Presets…" and "Mini Visualizers".
      Follow-up ("do mirror windows belong in that menu?" -> "yes,
      split it" -> "more generally, do mirror windows belong there or
      in settings?"): split the hamburger menu's shared section in two
      - Presets…/Lock Preset/Shuffle Presets (preset *behavior*) stay
      together, Mirror Windows/Mini Visualizers (opening/managing
      secondary *windows*) got their own section below a divider,
      since they'd been sitting in one undifferentiated flat block.
      On menu vs. Preferences: Mirror Windows belongs in the menu, not
      Preferences - it's an action (create a window, and a live list
      of currently-open ones that changes at runtime), not a
      configurable preference the way Transparency Mode/Now Playing
      are (ongoing state with sub-settings like opacity/placement,
      which is exactly why those moved TO Preferences-only earlier
      this session). A Preferences dialog isn't really built for
      "create a new window" actions or a dynamically-changing open-
      window list in the first place. Fixed rebuild_mirror_windows_menu's
      positional section index again (now section 3, not 2, since a
      new section was inserted before it) - this keeps happening, see
      the comment there, now spelling out the full history (four
      breaks so far). Verified in the sandbox via direct GMenu
      introspection: section 2 is exactly [_Presets…, Lock Preset,
      Shuffle Presets], section 3 is exactly [Mirror Windows, Mini
      Visualizers], and open_mirrors_section still resolves correctly
      through the new index.
- [ ] Export Settings for backup - requested, not yet designed.
      Settings Profiles (build_profiles_page/profile_fields/
      save_profiles) already save named snapshots of every slider/
      toggle covered by profile_fields, but only to this app's own
      internal profiles file (load_profiles/save_profiles) - there's
      no way to get a profile (or the current live settings) out to a
      user-chosen file the way Load Preset uses a real Gtk.FileDialog.
      A real "export" would likely reuse profile_fields' existing
      (key, getter, setter) list to serialize to JSON and a
      Gtk.FileDialog.save() to pick a destination, with an "import"/
      Load Settings counterpart handling the reverse - neither
      attempted here.
- [ ] Export Playlist for loading later - requested, not yet designed.
      Same shape of gap as Export Settings: Playlists already save/
      load internally (save_playlists, build_playlists_dialog,
      save_playlist_clicked) but only to this app's own internal
      playlists file, with no way to get one out to a portable file a
      user could back up, move to another machine, or share. Would
      need a real Gtk.FileDialog.save()/open() pair (same pattern
      Load Preset already uses) serializing one playlist's preset-name
      list to JSON, plus a decision on what happens if an imported
      playlist references presets this install doesn't have (bundled
      pack differences, or a preset that was only ever a locally-
      loaded file elsewhere) - not attempted here.
- [ ] Mini Visualizer efficiency - originally reported just about
      Spectrogram ("make spectrogram more efficient, it causes lag"),
      broadened on request to the mini visualizers generally ("make
      the miniviz graphics more efficient, they cause lag"). Not yet
      investigated for any of them - no profiling done, nothing
      confirmed as the actual cause. Likely candidates given how every
      aux window's audio path works: each window running its own
      independent FFT per draw (see the "share FFT" idea already
      logged above - real duplicated work across every currently-open
      window, not just Spectrogram) and/or per-kind bookkeeping like
      update_spectrogram_columns' column history. Needs picking one
      concrete case to profile first (which window(s), what the actual
      per-frame cost is) rather than guessing at a fix blind.
- [x] Peak Hold Color - requested ("customize peak hold color too").
      New shared setting (peak_hold_color_custom/peak_hold_color,
      alongside the existing shared peak_hold_seconds/
      peak_hold_fall_rate) for VU Meter/Peak Meter/Spectrum's hold
      marker line, off by default (follows the theme-reactive
      canvas_foreground_rgba, unchanged from before) with a switch +
      Gtk.ColorDialogButton in the settings popover to opt into a
      fixed color instead. Verified by actually rendering a Spectrum
      surface to a real Cairo ImageSurface with a custom color set and
      scanning the pixel buffer for it - the picked color genuinely
      appears in the output, not just stored in state.
- [x] Spectrum defaults to Solid color - requested ("spectrum miniviz
      should be solid color default"). Added "spectrum" to the same
      kind-list that already defaulted Oscilloscope/Vector Scope/X-Y
      Scope to solid instead of rainbow.
- [x] Spectrum Height Scale - requested ("also height scaling for
      spectrum"). New settings-popover slider (0.25-2.5x, default
      1.0) multiplying the effective bar/curve height in both
      render_spectrum_bars and render_spectrum_smooth (and their
      peak-hold markers, so a hold cap stays visually attached to the
      bar it's capping) - a gain-style control, not a 0-100%-of-canvas
      one, so values above 1.0 let bars/the curve clip at the canvas
      edge rather than being capped at "fits the available height".
      Verified by rendering to a real Cairo surface at three different
      scales and confirming the drawn-pixel count actually changes
      (not just that the state variable changed).
- [x] Simplified the hamburger menu's preset entries - requested ("I
      do not want presets submenu. Just open the presets window, and
      stuff is divided by tabs. simpler") plus a related request
      ("Load preset should be in the preset tab"). Collapsed the old
      Presets submenu (Browse Presets…/Favorites…/Queue…/Playlists…)
      down to a single "Browse Presets…" item, and moved the separate
      top-level "Load Preset…" item into a button at the top of the
      Presets tab itself (build_presets_tab), above the search entry.
      win.show-favorites/-queue/-playlists still exist as actions (the
      Queue tab's own Playlists… button still uses win.show-playlists)
      - only their menu entries are gone, since every tab is reachable
      from the one dialog now anyway. Fixed rebuild_mirror_windows_menu's
      positional menu index again (5 -> 4 -> 3, this menu section keeps
      changing shape - see the comment there, which now spells out the
      full history). Verified in the sandbox: window construction
      succeeds (index resolves), the menu section is exactly
      [Browse Presets…, Lock Preset, Shuffle Presets, Mirror Windows,
      Visualizer Windows], win.load-preset still works as an action,
      and the Load Preset button is the Presets tab's first child.
      Follow-up, requested ("move the load preset button to a better
      spot? maybe the bottom?"): moved from above the search entry
      (prepend) to below the list (append) - out of the way of the
      tab's main job (browsing/searching) instead of competing with it
      for top-of-tab attention.
- [ ] Reference for a possible Butterchurn -> MilkDrop transition,
      requested to be recorded ("record this as a possible exam[ple]
      to look at"): https://silkdrop.vercel.app/ - not yet looked into
      (what it actually is, how it renders, license, whether it's
      still a WebGL/JS engine like Butterchurn or something closer to
      native MilkDrop) or evaluated against this codebase. Purely a
      pointer for later, nothing investigated or decided.
- [x] Real bug found and fixed while investigating "why does it not
      load '.../deepseek_json_20260904_de78cf.json'": that specific
      file is syntactically valid JSON but not a real Butterchurn
      preset - an AI-generated (DeepSeek) file with an invented schema
      (presetName/shapes/waves/motionVectors/beatDetection/variables/
      effects, perFrame as an array of JS-looking expression strings)
      that only superficially resembles MilkDrop concepts. Since it's
      valid JSON, loadPresetFile's JSON.parse succeeds and it never
      even reaches the MilkDrop-conversion fallback or the warp/comp
      shader-repair checks (this file has no warp/comp keys at all).
      The actual bug: main.js's loadPresetFile called goToPreset(...)
      without awaiting it - when visualizer.loadPreset() then threw
      inside Butterchurn's own preset-application code (confirmed:
      "Unexpected keyword 'return'", from trying to compile this
      preset's made-up perFrame arrays as EEL2 equations), that became
      an unhandled promise rejection instead of reaching loadPresetFile's
      own try/catch - so LOAD_PRESET_ERROR: never fired, but the
      preset was still added to names/resolvedPresets/nameSet
      beforehand, so it silently appeared in the preset list (and
      would have appeared in the new Loaded tab) while the visualizer
      itself never actually switched to it (no PRESET_NAME:, window
      title unchanged) - zero feedback either way. This is also the
      direct answer to "when loaded can it immediately switch?" - yes,
      goToPreset(index, 0) already uses a 0-second blend, i.e. an
      instant cut, when a load actually succeeds; this bug is why a
      failed one looked like nothing happened instead of erroring OR
      switching.
      Fixed: goToPreset(...) is now awaited inside a nested try/catch
      that rolls back the names/nameSet/resolvedPresets additions and
      re-throws into the existing outer catch on failure - a preset
      that fails to actually apply no longer lingers in the list (or
      Loaded tab) looking usable, and now correctly produces a
      LOAD_PRESET_ERROR: toast like every other failure path already
      did. Required `npm run build` in src/web/ before the fix took
      effect in the Flatpak build - flatpak-builder only ever copies
      whatever's already in src/web/dist/ (install_subdir in
      meson.build), it never runs Vite itself; noted in memory since
      it's an easy step to miss on any future main.js change.
      Verified in the sandbox with the exact reported file: before the
      fix, it silently added to the list with the title unchanged and
      no error; after, a LOAD_PRESET_ERROR: toast fires immediately and
      it's absent from preset_names/user_loaded_presets afterward. This
      specific file still won't ever load correctly, since it's not
      real preset data - MilkDrop-format .milk or actual Butterchurn
      .json presets (the two formats this app has ever supported) are
      needed, not an AI-invented JSON shape resembling one.
- [x] Load Preset - asked ("Does load preset work???"). Verified end
      to end in the sandbox by driving the real loadPresetFile() path
      directly (bypassing only the native GtkFileDialog itself, which
      is standard GTK plumbing) with a real Butterchurn/.json preset
      and a real MilkDrop/.milk preset: both loaded, played
      immediately (window title updated to the loaded preset's name),
      and were appended to the preset list with no JS errors. Works
      correctly for both preset formats.
- [x] "Section for user loaded presets" - requested, built. New
      "Loaded" tab in the preset browser dialog (alongside Presets/
      Favorites/Queue), showing only presets added via win.load-preset
      this session - self.user_loaded_presets, confirmed (not just
      dispatched) via the same PRESET_LIST: re-announcement Load
      Preset already triggers on success: a name is only added once
      it's seen to have gone from absent to present in that list,
      rather than trusting the moment Python asked JS to load it (which
      doesn't yet know if the parse/conversion actually succeeded).
      Deliberately session-scoped only, unlike Favorites (which
      persists to disk) - a loaded preset's actual content only ever
      lives in the webview's JS runtime state, never written anywhere,
      so persisting just the name across restarts would list entries
      that fail to resolve the moment they're picked. A real cross-
      session version would need to persist the original file (or its
      full content), not just its name - not attempted here.
      Verified in the sandbox: starts empty, a confirmed real load
      appears immediately, re-loading the same name doesn't create a
      duplicate entry.
- [x] Removed the disabled "+" (Add to Queue or Playlist) overlay
      button - requested ("disable the plus overlay button. Queues and
      playlists should go through the menu"). It was already
      set_sensitive(False) (never wired up), so this is full removal of
      the dead/inert widget, not a new disabled state. Queue and
      Playlists are already reachable via the hamburger menu (Presets
      submenu > Queue…/Playlists…, see window.ui), unaffected by this.
- [ ] Toolbar Hide Delay - requested ("add setting to control how
      soon top bar hides") - built. New Preferences > Appearance >
      Toolbar > Hide Delay slider (0-10s, 0 = "Never" i.e. stays
      visible, default 3s matching the old hardcoded value) controls
      the same delay that was previously a bare hardcoded `3` in two
      places (toolbar_leave and reveal_toolbar's own
      GLib.timeout_add_seconds calls) - both now go through a new
      shared schedule_toolbar_hide() helper instead of duplicating the
      timer logic. That consolidation incidentally fixed a latent bug
      in toolbar_leave: it used to assign a fresh hide_timer without
      cancelling whatever it already held, so re-entering/leaving the
      toolbar before the first timer fired could leak a duplicate
      pending callback - schedule_toolbar_hide always cancels first,
      same pattern as animate_opacity elsewhere in this file.
      Changing the slider re-arms immediately against the new delay
      (unless the mouse is currently over the toolbar, in which case
      it's left alone until the next real leave) rather than only
      taking effect on the next toolbar interaction. Verified in the
      sandbox: reveal_toolbar/toolbar_leave schedule using the
      configured value, "Never" cancels a pending hide, a new value
      re-arms immediately, no timer leak on repeated toolbar_leave
      calls, and the hovering guard.
- [x] Immersive Mode - requested, built. New win.hidden-mode stateful
      action, a real hamburger menu item (not Preferences-only, unlike
      Transparency Mode) since once it's on, the header bar - and the
      hamburger menu that would otherwise reach Preferences - is gone,
      so the only way back has to work without either: a right-click
      on the content area (on_content_right_click, a new
      Gtk.GestureClick - SECONDARY button, CAPTURE phase, same
      content_box placement and WebKit-hit-testing reasoning as the
      existing drag-to-move gesture right next to it) shows a small
      Gio.Menu/Gtk.PopoverMenu with one "Exit Immersive Mode" item.
      Turned out to need less new code than expected: window dragging
      from anywhere in the content area already existed unconditionally
      (self.get_surface().begin_move(), the drag gesture right above
      the new right-click one) and needed no changes at all. The
      hide/reveal mechanism reuses toolbar_view.set_reveal_top_bars
      rather than adding a parallel one - hidden_mode_changed forces
      it False and hides the nav arrows on enable; the one new piece
      is a hidden_mode_enabled guard at the top of reveal_toolbar
      itself, since every path that would normally re-reveal the
      toolbar (mouse_move, toolbar_enter, schedule_toolbar_hide) already
      funnels through either that method or a no-op-when-hidden hide
      timer, so one guard is enough to stop auto-hide's normal
      "re-reveal on movement" behavior from fighting Immersive Mode's
      "stay hidden regardless of the mouse" one.
      Verified in the sandbox: the action/menu item exist and the menu
      item lands at the right position without disturbing Mirror
      Windows' own positional index (again - see that comment's
      history); enabling actually collapses toolbar_view's top bars;
      reveal_toolbar() and mouse_move() are confirmed no-ops while
      enabled (both left the toolbar collapsed); disabling restores it;
      the right-click handler builds its popover without crashing when
      enabled and does nothing when it's not; and both the pre-existing
      drag gesture and the new right-click gesture are genuinely
      attached to content_box side by side on their own distinct
      buttons.
      Follow-up, two real bug reports plus a naming request all
      together ("the right click does not work. Additionally I cannot
      drag the window around in 'hidden' mode. Also open to different
      names for the mode"):
      Renamed throughout to Immersive Mode (win.hidden-mode ->
      win.immersive-mode, hidden_mode_enabled/_changed ->
      immersive_mode_enabled/_changed, menu label too) - suggested as
      more standard terminology for "hide all chrome, content fills
      everything" (matches Android's own "immersive mode" and similar
      framing in other media apps) and applied directly since a menu
      label/action-name rename is trivial to redo if it's not the
      right call, same reasoning as the earlier Mini Visualizers
      rename.
      Root cause found for both bugs at once: WebKit's own native
      right-click context menu (Back/Forward/Reload/Inspect Element)
      was never disabled anywhere in this codebase - confirmed via
      GObject.signal_lookup that WebKit.WebView's "context-menu"
      signal exists and was unconnected. That default menu takes its
      own pointer grab to show itself, which was very likely
      intercepting the click before it ever reached the CAPTURE-phase
      gesture on content_box (explaining "right click does not work" -
      the *wrong*, native menu was eating the event, not showing
      anything the user would recognize as this app's own), and a
      still-open grabbing menu left over from that would also explain
      "can't drag either" as a knock-on effect, not a separate bug.
      Fixed in webview.py: view.connect("context-menu", lambda *args:
      True) - returning True tells WebKit the request was already
      handled, suppressing its default popup. This makes sense as a
      fix regardless of Immersive Mode specifically, since a browser-
      tab-style context menu never made sense on a music visualizer to
      begin with.
      Verified in the sandbox: the renamed action/menu item resolve
      correctly (old win.hidden-mode name confirmed gone), a handler
      is now genuinely connected to WebView's context-menu signal, and
      the full enable/mouse-move-no-op/disable cycle still works
      end to end under the new names. NOT independently re-verified
      against a real display that right-click/drag now actually work
      as the user experiences them - this fix follows directly from a
      real, confirmed gap (the signal genuinely was unconnected) and a
      strong explanatory mechanism for both symptoms, but doesn't rule
      out a second contributing cause if the user still sees a
      problem after this.
      Second follow-up, a real bug report ("exit button doesn't show
      under cursor, instead at top left of the window"): confirmed via
      direct sandbox introspection that `Gdk.Rectangle(x=..., y=...,
      width=..., height=...)` silently ignores every constructor
      keyword - a PyGObject boxed-type limitation, not something
      specific to this codebase (it even emits a "Passing arguments to
      gi.types.Boxed.__init__() is deprecated. All arguments passed
      will be ignored." warning if you go looking for it) - so
      on_content_right_click's popover was always being pointed at
      (0, 0, 0, 0), i.e. content_box's own top-left corner, regardless
      of the real click position. Fixed by constructing a bare
      Gdk.Rectangle() and setting .x/.y/.width/.height individually
      after construction instead of via kwargs - confirmed this
      pattern actually sets the fields correctly, unlike the kwargs
      form. Verified two ways: directly re-deriving the correct
      rectangle fields, and (more rigorously) driving the real,
      unmodified on_content_right_click through a fully presented
      window and reading back the actual popover's own
      get_pointing_to() - confirmed it now reports the exact click
      coordinates passed in, not (0, 0).
- [ ] Now Playing overlay - requested ("optional music title and
      artwork show in bottom, placement configurable in settings") -
      built. Melange only ever captures raw system audio (GStreamer) -
      it has no idea what's playing or who's playing it, so the only
      realistic source for title/artist/artwork is MPRIS
      (org.mpris.MediaPlayer2.* on the session D-Bus), the standard
      Linux desktop "now playing" interface every major player
      (Spotify, browsers, VLC, Rhythmbox, etc.) implements.
      New now_playing.py module (NowPlayingWatcher): opens the session
      bus, discovers existing org.mpris.MediaPlayer2.* names plus
      watches NameOwnerChanged for new ones, and for each creates a
      Gio.DBusProxy scoped to the org.mpris.MediaPlayer2.Player
      interface specifically (so GDBusProxy's own automatic property-
      caching + g-properties-changed signal does the metadata
      tracking, no manual PropertiesChanged plumbing needed). Whichever
      known player currently reports PlaybackStatus=="Playing" (most
      recently *transitioned into* Playing, if more than one) is "the"
      active one - if none are playing, nothing is shown, rather than
      showing stale paused/wrong-app info. That "most recently
      transitioned into Playing" wording is deliberate, not "most
      recently changed any property" - the latter was the first
      implementation and is wrong: it lets a player's routine,
      unrelated property churn keep perpetually outranking a genuinely
      newly-started one. Artwork (mpris:artUrl, file:// or http(s),
      both seen in the wild against real players) loaded via
      Gio.File.load_contents_async (handles both URI schemes through
      GIO's own VFS, no separate download path needed) into a
      Gdk.Texture, with a generation token guarding against a slow
      late art fetch clobbering a newer track's already-applied art.
      UI: a small translucent card (Gtk.Picture + title/artist
      Gtk.Labels, same visual language as the existing nav-arrow-
      button/playlist_queue_button overlays) added to webview_overlay,
      hidden whenever the feature is off or nothing is playing.
      Settings: new Preferences > Appearance page (also where
      Transparency Mode's group moved to, per "wondering if there
      should be a separate settings tab for appearance" - previously
      lived under Playback) - an Enabled switch plus a Placement
      Adw.ComboRow (Bottom Left/Bottom Center/Bottom Right), no
      hamburger menu entry at all, matching the Transparency Mode
      precedent of settings-only for this kind of toggle.
      Sandbox permission added: --talk-name=org.mpris.MediaPlayer2.*
      in com.cameronlp.Melange.json's finish-args - narrow, read-only-
      in-effect (lets the app query/watch other apps' MPRIS interfaces,
      nothing else).
      Deliberately NOT granted: broad home-directory filesystem access
      (--filesystem=home), which would be needed to load artwork that
      a player caches as a local file:// path (confirmed against a
      real running Firefox instance - its MPRIS art is a
      ~/.mozilla/.../firefox-mpris/*.png path, invisible to the
      sandbox without it). User's explicit choice, weighing "some
      players' local art won't display" against "read access to the
      whole home directory" - title/artist always show regardless, and
      http(s)://-served artwork (confirmed present on at least one
      other real player on this machine, Feishin) is unaffected since
      that only needs the network share already granted. The art
      widget fails closed in this case (stays hidden, no broken-image
      icon, no crash) rather than erroring.
      Verified against real, live, currently-running MPRIS players on
      this machine (not just synthetic fixtures) - detection, title/
      artist extraction, and the file:// art graceful-failure path all
      confirmed end to end this way. One thing that could NOT be
      verified in-sandbox: the multi-simultaneous-"Playing"-player
      tie-break logic specifically, since a same-sandbox mock player
      can't own an org.mpris.MediaPlayer2.* bus name without a
      separate --own-name grant (only --talk-name was requested,
      deliberately, since the real app never needs to impersonate a
      media player) - confirmed this is the sandbox correctly doing
      its job (the mock's owned name never reached
      NowPlayingWatcher.players) rather than a bug, but it does mean
      that one code path is unverified beyond static review.
      Follow-up, all requested in one message ("show when paused?
      selector of the app? text size and color? background toggle?
      both artist and title? cover art? audio time?"):
      - Shows for Paused now too, not just Playing (was hiding on
        pause, which "answer the door" or similar briefly pausing
        made look like the feature just stopped working) - Playing
        still outranks Paused when choosing among several active
        players, same "most recently transitioned into this tier"
        logic as before, now with two tiers instead of one.
      - Source selector: new Adw.ComboRow ("Auto" + one row per
        currently-known player, by its real MPRIS Identity - e.g.
        "Firefox" - not the raw bus name) lets a specific app be
        pinned instead of the automatic Playing-over-Paused/most-
        recent heuristic. Falls back to Auto on its own (both the
        watcher's internal state and the row's own selection) if the
        pinned player disappears, rather than silently showing
        nothing or erroring. The row's model is rebuilt live as
        players come and go (NowPlayingWatcher gained a second
        on_players_changed callback for this) rather than being a
        snapshot from whenever Preferences happened to first open.
      - Show Title / Show Artist / Show Artwork / Show Playback Time /
        Show Background: five independent toggles - each hides its
        own widget outright, and title/artist/artwork additionally
        stay hidden if the current track just doesn't have that data
        even when the toggle is on (an empty artist line, or a broken-
        image icon for a track with no art, would be worse than
        nothing).
      - Text Size (8-24px) and Text Color (Gtk.ColorDialogButton, GTK
        4.10+'s native color picker) apply to title/artist/time
        together via Pango attributes set directly on each Gtk.Label
        (Pango.attr_size_new_absolute + attr_foreground_new) rather
        than injected CSS - simpler API for "these text runs, these
        attributes" than standing up a CssProvider for it.
      - Playback Time ("1:23 / 3:45"): MPRIS's Position property is
        explicitly NOT meant to be tracked via PropertiesChanged per
        the spec (players only signal on a real seek, not
        continuously as it advances), so this is a plain synchronous
        Properties.Get once a second (NowPlayingWatcher.
        get_position_us) rather than interpolated locally - simpler,
        and a once-a-second local D-Bus round trip is cheap enough not
        to bother optimizing away. Off by default (Show Playback Time
        unchecked) since not every player/track supports it and it's
        the one field most likely to just silently not appear.
      Verified against the same real, live Firefox MPRIS session as
      before: source model built from the real player's actual
      Identity, pinning/un-pinning it, all five field toggles, text
      size+color actually landing in the label's Pango attributes, and
      the playback-time mechanism running without crashing (its
      *visibility* depends on whether Position/length are actually
      available, which varies by track/player, so only "ran cleanly"
      was asserted there, not "always shows"). Paused-state ranking
      itself still isn't independently verified beyond static review
      (same sandbox --own-name limitation as before - and pausing the
      user's own real, live playback to test it directly was correctly
      out of bounds).
      Also added: two more Placement options, Top (Left/Center/Right)
      alongside the original Bottom three - requested ("should also be
      able to be played in top too"). Top placements originally got a
      larger top margin (56px vs 12) than bottom ones, an approximation
      of a typical header bar height meant to avoid the header - see
      below, this was wrong and got fixed the same session.
      Follow-up, a real bug report ("many of the apps leave space for
      the top bar... shifted down from the top... especially since the
      top bar fades away and will not block the view"): that 56px top
      margin was exactly this - reserving space against a header that
      already floats over the content and fades away on its own,
      defeating the entire point of extend_content_to_top_edge. Fixed
      to the same plain 12px both top and bottom now use. Also added a
      Font control (Gtk.FontDialogButton restricted to
      Gtk.FontLevel.FAMILY - just the typeface, since style/size are
      already this app's own separate controls) and widened Text Size
      from 8-24px to 8-72px, both requested. Verified in the sandbox:
      top/bottom margins now match, the font family lands in the
      title label's Pango attributes, and the widened slider range
      actually applies up to 72px.
      Follow-up: the same report also named "miniviz graphics" (the
      aux visualizer windows) as shifted down the same way - user
      supplied 3 screenshots (VU Meter, 3D Terrain Spectrogram with
      the header visible, same Terrain window with it faded). Directly
      measured a real AuxVisualizerWindow's widget allocations in the
      sandbox to settle this rather than guess further from the
      screenshots alone: win.drawing_area.get_allocation() reports
      (0, 0, full_width, full_height) - i.e. the actual canvas already
      starts at y=0 and covers the entire window, identically whether
      reveal_top_bars is True or False. So extend_content_to_top_edge
      genuinely works correctly here (confirming the earlier read of
      the code) - there is no GTK layout bug reserving header space.
      The blank gap the screenshots show is coming from inside each
      visualization's own Cairo rendering instead: e.g. VU Meter's
      needle-style gauge (draw_vu_needle) is a compact semicircular
      dial that only occupies roughly the bottom half of its allotted
      cell - normal geometry for that gauge style (an analog meter's
      dial housing is wider than it is tall), not a bug, but it does
      visually read as "space reserved at the top" at a glance. Not
      yet changed - this is a rendering/proportions question for each
      affected visualization individually (which one(s) should use
      more of the available vertical space, and how), not a single
      fix. Deferred pending the user's input on which specific
      visualizations should be adjusted and how.
      Also added, requested ("now play text box width should be
      adjustable"): a Text Box Width slider (10-80 "chars", default
      28 matching the previous hardcoded value) - drives
      set_max_width_chars on all three labels (title/artist/time).
      Deliberately a character-count width (matching how the card was
      already sized) rather than a literal pixel width, so it scales
      naturally with Text Size/Font instead of fighting them. Verified
      in the sandbox that it applies to all three labels together.
      Follow-up, both requested: widened the range further (10-80 ->
      10-200); and gave the card equal 12px margins on both left and
      right regardless of placement/halign ("when it reaches the right
      side, there should be the same gap as there is on the left") -
      previously only the side it was aligned *away* from had zero
      margin reserved, which was fine until a wide enough title (Text
      Box Width turned up, or just a long song title) made the card's
      natural size reach that edge with nothing to stop it.
      Second follow-up, requested ("change font/bold/italic in the
      overlay text"): the Font control's Gtk.FontDialogButton now uses
      Gtk.FontLevel.FACE instead of FAMILY - adds style (Regular/
      Bold/Italic/Bold Italic) selection alongside the typeface, still
      without a size field (Text Size remains the one place that's
      set). self.now_playing_font_family (a bare family string)
      became self.now_playing_font_desc (a full Pango font description
      string sans size, e.g. "Sans Bold Italic") - apply_now_playing_
      text_style now builds one combined Pango.AttrFontDesc (family +
      weight/style + this app's own pixel size, merged) instead of
      separate AttrFamily/AttrSize attributes, since there's no
      standalone "AttrBold"/"AttrItalic" attribute type - bold/italic
      only exist as part of a full FontDescription. Verified in the
      sandbox: the font button reports FontLevel.FACE, picking "Serif
      Bold Italic" produces a font-desc attribute containing all three
      words, and the Text Size slider still overrides whatever size
      the font dialog itself last had.
      Third follow-up, two independent display-behavior requests:
      (1) "only appear when there is a change or pause/play. Display
      then fade out after some time" - new Auto-Hide toggle + Auto-
      Hide Delay slider (1-20s, default 5s). on_now_playing_changed
      tracks a (title, artist, status) key and only treats it as a
      real "change" worth reacting to when that key actually differs
      from last time (so e.g. a Position-only update, if this ever
      gets fed more granular info later, wouldn't spuriously re-trigger
      it) - snaps straight to fully visible on a genuine change (a
      track changing is exactly the moment you want to see it, not
      fade into view) and restarts a countdown to fade back out over a
      fixed 500ms. (2) "option to have the title periodically fade in
      and out" - new Periodic Fade toggle + Fade Interval slider
      (1-15s, default 4s), independent of Auto-Hide - a continuous
      sine-wave opacity cycle (one formula covers the whole fade-out-
      then-back-in cycle, no discrete show/hold/hide state machine
      needed) rather than an event-triggered fade. The two can be
      turned on together, which produces overlapping/unspecified-
      looking behavior since both drive now_playing_box's opacity -
      not guarded against, the user can just not combine them.
      Verified in the sandbox: periodic fade's timer starts/stops
      correctly and genuinely animates opacity through real
      intermediate values (not just toggling state); auto-hide shows
      the card immediately and starts a timer on a real change,
      re-feeding *identical* info does nothing (correctly not treated
      as a new change), the card actually fades below 0.5 opacity once
      the configured delay elapses with no further changes, and a
      subsequent play/pause change immediately re-shows it.
      Also added, requested ("add keyboard shortcuts to more things
      that make sense"): Ctrl+T (win.transparency-mode), Ctrl+I
      (win.immersive-mode - also a second way out of Immersive Mode
      besides the right-click menu, since the header/hamburger menu
      that would otherwise reach this action is gone once it's on),
      and Ctrl+Shift+F (win.show-favorites, following the same "Shift
      adds a related secondary action" pattern Ctrl+Shift+M/Ctrl+M
      already established for Close All Mirrors/New Mirror Window).
      Verified all three resolve via Gtk.Application.get_accels_for_
      action() against the real built app.
- [x] Fixed the Appearance page's tab icon - real bug, not just a
      request: it used "preferences-desktop-theme-symbolic", which
      does not exist in this runtime's icon theme (confirmed via
      Gtk.IconTheme.get_icon_names() - only the non-symbolic
      "preferences-desktop-theme" does), so it was silently falling
      back to some generic/inconsistent icon instead of a real
      symbolic one. Changed to "preferences-desktop-appearance-
      symbolic", which does exist and fits the page's actual name
      better besides. Verified the correct icon name is really present
      in the icon theme in the sandbox.
- [x] Lock Preset now visibly disables the Cycling preferences group
      (Cycle Interval/Cycle Interval Jitter/Transition Blend Time) -
      requested ("if locked preset then disable the cycle intervals
      and transition, they should not be active if preset is
      locked"). Auto-cycling already refused to actually advance while
      locked (next_preset() itself checks preset_locked, and every
      auto-cycle/beat/drop trigger already routed through it via the
      NAV_NEXT debug message) - this was a pure UI-affordance gap, not
      a functional one: the sliders stayed interactive while silently
      having no effect. lock_preset_changed now also sets
      self.cycling_group.set_sensitive(not self.preset_locked).
      Verified in the sandbox: locking/unlocking toggles the group's
      sensitivity both ways.
- [ ] Per-widget FPS setting for the aux visualizer windows - each
      window kind (VU Meter, Peak Meter, X-Y Scope, Spectrum,
      Spectrogram, Terrain, Waterfall, DVD Bounce, Pipes) should be
      able to set its own render/tick rate independently, rather than
      all sharing whatever their current fixed redraw cadence is.
      Requested, not yet designed or implemented - see also the
      already-logged "share same FFT processing" idea above/below,
      which is related (both are about per-window vs. shared timing)
      but distinct (that one's about the audio analysis feeding every
      window, this one's about each window's own draw cadence).
- [ ] Custom fractal visualizer - requested, not yet designed. Would
      need an actual fractal-rendering approach/library (nothing in
      this codebase currently does fractal math) - unclear yet whether
      that means a new aux visualizer window kind (aux_window.py,
      Cairo-drawn like the others) or something WebGL/Butterchurn-side
      (related to the already-logged "fractal Butterchurn preset"
      idea elsewhere in this file, but that one's a MilkDrop preset
      idea, not a real fractal renderer - these may or may not end up
      being the same feature). Needs a decision on approach before any
      implementation starts.
- [ ] Peak Meter bug: reported "mostly red" - not yet investigated.
      Likely the same shape of bug as the earlier Terrain "mostly
      green"/"mostly blue" color reports (a level-to-color mapping
      that's saturating toward one end of its scale for real music
      input), but Peak Meter's own color logic hasn't been checked
      against this specific report yet.
- [ ] Hover Transparency built for the main window (win.hover-
      transparency, a hamburger menu toggle, off by default) -
      requested ("is it possible to make a main window transparency
      mode - full transparency when mouse hovers over"). Confirmed
      feasible with no prior groundwork needed: GTK4 toplevels are
      already alpha-capable under a compositing Wayland/X11 setup with
      no RGBA-visual setup required (unlike GTK3), so `self.
      set_opacity(0.0)`/`(1.0)` on enter/leave is enough - no CSS or
      WebKit background changes needed for a straight 0/1 toggle
      (would matter for a partial/dimmed value, since the webview's
      own `background:black` CSS - index.html - would still show
      through a semi-transparent window; not relevant at full 0/1).
      Deliberately scoped to just the content area (a new
      Gtk.EventControllerMotion, CAPTURE phase for the same WebKit-
      hit-testing reason the existing toolbar-auto-hide motion
      controller already needs it, added to webview_overlay) rather
      than the whole window the way that existing motion controller
      is - the header bar (and its hamburger menu, the only way to
      turn this back off) needs to stay reachable on its own terms;
      including it would mean moving the mouse toward the menu to
      disable the mode triggers the same transparency, forcing a
      blind click. Not yet confirmed against a real display - this
      environment has no way to visually verify hover-triggered window
      transparency, only that it builds cleanly (flatpak-builder's own
      window.ui template compilation, which fails loudly on a bad
      action/widget reference) and the action registers via the same
      stateful-toggle pattern already used elsewhere (Lock Preset/
      Shuffle Presets).
      Follow-up, requested ("is there a slider for it?" -> both
      Opacity Level and Fade Speed): added two sliders to Preferences
      > Playback, in a new "Hover Transparency" group below Beat
      Detection (reusing the existing build_slider_row helper, same as
      Cycle Interval/Mesh Size/etc.) - Opacity Level (0.0-1.0, default
      0.0 "Fully Invisible", matching the original hard-0.0 behavior)
      sets the target opacity used on hover instead of always going
      fully transparent; Fade Speed (0.0-2.0s, default 0.0 "Instant")
      animates set_opacity() toward that target over a GLib.timeout_add
      step timer (16ms tick, same repeating-timeout shape as the DVD
      Bounce/Pipes tick timers in aux_window.py) instead of snapping,
      with 0 staying an instant snap so the default behavior is
      unchanged. Disabling the toggle mid-fade still forces opacity
      back to 1.0 immediately and cancels any in-flight fade timer,
      on purpose - it's a safety net, not something that should be
      slowed down by the user's own fade setting.
      This time actually verified end-to-end in the sandbox (not just
      "builds cleanly"): registered the app's own gresource
      (/app/share/melange/melange.gresource) and set
      PYTHONPATH=/app/share/melange to construct a real MelangeWindow
      via `flatpak run --command=python3`, drove both new slider
      widgets programmatically, toggled win.hover-transparency, and
      asserted actual self.get_opacity() values mid-fade and at
      completion for both the enter (fade to target) and leave (fade
      back to 1.0) paths, plus the instant-mode and disable-mid-fade
      cases. That test run caught a real, unrelated regression from
      when Hover Transparency's own menu item was first added: it
      sits between Shuffle Presets and the Mirror Windows submenu in
      window.ui, which shifted rebuild_mirror_windows_menu's hardcoded
      `section2.get_item_link(4, MENU_LINK_SUBMENU)` off by one -
      construction crashed with AttributeError before ever reaching
      the transparency code. Fixed to index 5, with a comment flagging
      that this positional lookup is brittle and will break again if
      another item is inserted above the Mirror Windows submenu
      without updating it.
      Second follow-up, both requested: (1) inverted the trigger -
      "the transparency should activate when mouse is moved away", so
      on_content_hover_enter now animates back to fully opaque (1.0)
      and on_content_hover_leave animates to hover_transparency_opacity
      - the opposite of the original hover-to-hide direction, better
      suited to "stay out of the way while I'm not looking at it,
      come back when I am" than the original "peek behind it"
      framing. (2) added an Enabled switch to the same Preferences >
      Playback > Hover Transparency group, above the two sliders -
      an Adw.SwitchRow with action-name="win.hover-transparency"
      rather than a build_toggle_row callback (Adw.SwitchRow
      implements Gtk.Actionable, same as the Loop/Shuffle Queue
      Gtk.ToggleButtons elsewhere), so it and the hamburger menu item
      stay in sync automatically in both directions with no extra
      state to reconcile - verified directly (a same-shape SwitchRow
      parented under the real window, since action-name resolution
      needs an actual ancestor chain to the "win" group) that toggling
      either one updates the other and self.hover_transparency_enabled
      correctly.
      Third follow-up, requested ("change it to a general transparency
      mode? except for the top bar. Even when the mouse is there there
      is no fading effect"): dropped hover-triggering entirely -
      renamed win.hover-transparency -> win.transparency-mode (menu
      label now "Transparency Mode") and the underlying state/methods
      to match, and removed the whole content_hover_motion
      EventControllerMotion/on_content_hover_enter/leave machinery,
      since nothing reacts to mouse position anymore. Enabling now
      just fades the content straight to the configured Opacity Level
      immediately, and disabling fades it straight back to fully
      opaque - both via the same Fade Speed setting (no more special-
      cased instant snap on disable; that was only needed to guard
      against the old mouse-position edge case, which no longer
      exists). animate_opacity now targets self.toast_overlay (the
      ToolbarView's "content", below the header bar) instead of self
      (the whole window/surface) - the header bar is a sibling `child
      type="top"` in window.ui, outside toast_overlay entirely, so it
      never changes opacity regardless of the configured level.
      Verified directly in the sandbox: toggling win.transparency-mode
      with zero motion events fired moves toast_overlay's opacity to
      the configured level and back, while self.headerbar.get_opacity()
      stays 1.0 throughout, and on_content_hover_enter/leave no longer
      exist on the window at all.
      Fourth follow-up, a real bug report ("It only fades the
      visualizer, not the window behind it"): fading toast_overlay's
      own Gtk.Widget opacity was necessary but not sufficient - by
      default AdwApplicationWindow's own root CSS node paints an
      opaque theme background *underneath* every child first, so a
      faded child was blending toward that solid color, not toward
      the real desktop behind the window (the window's surface itself
      being alpha-capable, confirmed earlier, doesn't help if
      something opaque is still painted into it). Fixed with a new
      `.transparency-active` rule in style.css (`background-color:
      transparent`, no type qualifier - same reasoning as
      .nav-arrow-button, don't assume the exact CSS node name), with
      the class added/removed on `self` in transparency_mode_changed
      alongside the existing opacity fade, rather than left on
      permanently, so there's no visual risk (e.g. a startup frame
      before content_box has stretched to fill the window) for anyone
      not using the feature. Also set the WebView's own
      set_background_color to transparent RGBA(0,0,0,0) once at
      creation (webview.py) as a defensive belt-and-suspenders fix -
      WebKit paints its own opaque backing color wherever the page
      hasn't painted yet otherwise, independent of GTK's own ancestor-
      opacity compositing.
      Verified in the sandbox that the CSS class is added/removed
      correctly on enable/disable and that the webview's background
      color is really alpha 0. Still NOT visually confirmed against a
      real compositor/desktop - this environment has no way to
      screenshot what's behind the window, only that the mechanism
      (transparent window background + transparent webview backing +
      faded content) is now the standard/correct GTK4 approach for
      this effect, matching how transparent-window apps are normally
      built.
      Fifth follow-up, requested ("remove Transparency Mode from the
      main drop down menu, should only be in settings"): removed the
      win.transparency-mode item from window.ui's hamburger menu
      entirely - Preferences > Playback > Transparency Mode's Enabled
      switch (already bound via action-name) is now the only way to
      turn it on/off. Fixed rebuild_mirror_windows_menu's positional
      section2.get_item_link index back down from 5 to 4 (Load Preset,
      Presets submenu, Lock Preset, Shuffle Presets, then Mirror
      Windows submenu again - Transparency Mode no longer sits between
      them). Verified in the sandbox: window construction succeeds
      (the index resolves), the menu's section item labels no longer
      include "Transparency" anywhere, and the action/Preferences
      switch path still works end to end with the menu item gone.
      Sixth follow-up, a real bug report ("when transparency is first
      turned on in settings, complete transparent and opacity slider
      does not help. Toggling it on/off again fixes"): the Opacity
      Level slider's callback only ever did `setattr(self,
      "transparency_opacity", value)` - it never re-applied that value
      to the actual widget while the mode was already on. The *only*
      code path that ever pushed self.transparency_opacity into a real
      opacity change was transparency_mode_changed (toggling the mode
      itself), which is exactly why toggling off/on "fixed" it - that
      was never a fix, it was the only way the value was ever actually
      used. Fixed by having the slider's callback apply the change
      live (self.toast_overlay.set_opacity(value)) whenever the mode
      is currently enabled, also cancelling any in-progress
      animate_opacity() fade first - without that, a fade still
      chasing the *old* target (captured as a local variable when the
      mode was last toggled on) would keep overwriting the manual
      drag on its next tick. Verified in the sandbox: enabling at the
      default (0.0) is still fully invisible as before, but now
      dragging the slider while already enabled changes the real
      opacity immediately (previously a no-op), repeated drags all
      apply, dragging while disabled only stores the value without
      touching opacity, and re-enabling then picks up whatever was
      last stored - covering the original bug report plus every
      adjacent case that fix could have broken.
      Follow-up, requested ("default opacity mode when enabled should
      be 50%"): changed the Opacity Level default from 0.0 ("Fully
      Invisible") to 0.5, both self.transparency_opacity's initial
      value and the slider's own starting position - enabling
      Transparency Mode for the first time now fades to 50% instead of
      fully invisible. Verified in the sandbox that the stored default,
      the slider's starting value, and the actual applied opacity on
      first enable are all 0.5.
- [ ] Follow-up on Pipes' beat-reactive rotation, all requested:
      given its own independent Beat Rotation switch (previously
      bundled under the same "React to Beats" toggle as the pipe-spawn
      burst - renamed to Beat Spawn now that there are three separate
      beat reactions), plus a new Beat Zoom - a brief decaying zoom
      punch-in on a detected beat, added on top of the user's own Zoom
      setting rather than overwriting it, same "decaying velocity/
      boost, not a snap" shape as the rotation kick. Beat detection
      itself (the rolling-average comparison) now always runs
      regardless of which of the three switches are on, so each
      reaction is genuinely independent rather than needing Beat Spawn
      on as a prerequisite for the others to fire; fixed a real bug
      surfaced by this restructuring - disabling Beat Spawn used to
      clear pipes_energy_history, which would have reset Beat
      Rotation/Beat Zoom's own detection state too since they now
      share it.
- [ ] **REVERTED**: the notify::is-active-based popover-dismiss
      safeguard (see below - was meant to close the settings popover
      when the window loses focus, e.g. clicking over on a different
      window) made things *worse*, reported immediately: the settings
      gear stopped opening the popover at all. Root cause: opening a
      popover can itself cause a transient is-active=False
      notification on its own parent window under GTK4/Wayland (a
      known rough edge, not unique to this code), which meant the
      handler was popping the popover back down the instant it opened
      - before it was ever visibly seen, reading as "doesn't open."
      Backed out entirely (both the notify::is-active connection and
      its handler) rather than attempt a patched version (e.g. a
      grace-period delay) in the same breath - a second guess risked
      shipping a second unverified regression on top of the first,
      with no way to click-test either one in this environment. The
      CAPTURE-phase click-gesture fix from before that attempt is
      still in place and presumably still the only safeguard active;
      whether it alone is sufficient, or the original "click a
      different window" report needs a *correctly-implemented*
      version of the focus-loss idea, is still open.
- [ ] X-Y Scope's Color Mode now also defaults to Solid rather than
      Rainbow (matching Oscilloscope and Vector Scope, previously the
      only two exceptions) - requested; its classic look is understood
      to be the plain green Lissajous trace (self.color's own default,
      "#33cc55"), not a rainbow one.
- [ ] Vector Scope's Color Mode now defaults to Solid rather than
- [ ] Vector Scope's Color Mode now defaults to Solid rather than
      Rainbow (Oscilloscope was already the one other exception, for a
      different reason - see its own note) - requested; its
      persistence-trail cloud was judged to read more clearly as one
      coherent shape in a single color than with each dot's color
      cycling independently.
- [ ] VU Meter given the same lagging Peak Hold indicator Peak Meter/
      Spectrum already have (Bars: a hold line above the fill; LED
      Segments: an outlined segment at the hold position; Needle
      skipped - a real analog dial doesn't have a peak-hold needle the
      way bar/LED hardware meters have hold lines) - reuses the same
      generic update_peak_hold/peak_hold_seconds every other hold
      marker in this file already shares.
- [ ] Pipes' camera rotation now reacts to beats too, on request ("x
      and y rotation" - azimuth and elevation both) - the same beat
      detector already driving the pipe-spawn burst (pipes_tick) now
      also kicks pipes_beat_spin_boost (added straight into the
      azimuth auto-rotate speed) and pipes_beat_tilt_velocity (a
      random +/-8deg/sec nudge applied to elevation) on every
      detected hit, both just decaying velocities added on top of the
      normal auto-rotate motion rather than snapping to a specific
      angle - reads as "the rotation reacted to that hit," not a
      jarring jump-cut. Gated the same way plain auto-rotate already
      is (paused during a manual drag, off entirely if Auto-Rotate
      itself is off) rather than fighting either.
- [ ] Fixed a real theme bug across several kinds: axis/reference
      lines (Terrain/Waterfall's draw_3d_axes, X-Y Scope/Vector
      Scope's crosshairs, Oscilloscope's center line, the VU needle's
      dial scale/ticks, Pipes' grid wireframe) and peak-hold markers
      (Peak Meter's hold line, Spectrum's per-bar hold caps in both
      Bars and Smooth styles) were still a plain translucent white
      regardless of theme, left over from before canvas backgrounds
      became Light/Dark/Follow-System-reactive - reported as
      unreadable in light mode (near-white marks on a near-white
      background). Added canvas_foreground_rgba (the counterpart to
      canvas_background_rgb) and switched all of the above to it -
      near-black in light mode, near-white in dark mode, same alpha
      either way. Decorative fills (bar/LED zone colors, the DVD
      wordmark's own text, Tux's eyes, etc.) were deliberately left
      alone - they're not reference marks, and several already carry
      real meaning (VU/Peak Meter's zone colors) that theme-tinting
      would muddy for no reason.
- [ ] Spectrogram given a Speed setting (0.25x-4x, requested) - a
      multiplier on how often update_spectrogram_columns actually
      appends a new column (dividing SPECTROGRAM_FRAME_INTERVAL by it),
      not on how much history SPECTROGRAM_COLUMNS keeps - controls how
      fast it scrolls through that history, not how much of it there
      is.
- [ ] **BUG**: settings popover (the gear button every aux window has)
      still reported as not closing on an outside click. A belt-and-
      suspenders fix was already attempted for this earlier (a
      CAPTURE-phase Gtk.GestureClick on the whole window, popping the
      popover down on any press without claiming the sequence - see
      the "settings gear doesn't open" writeup further down this
      file) and seemed to address the original report, but it's
      apparently still not working, or not working for every kind.
      Not yet re-investigated - candidates worth checking: whether the
      newly-added GestureDrag (rotate) and Gtk.EventControllerScroll
      (zoom) controllers on Terrain/Waterfall/Pipes' drawing_area,
      added since that original fix, interfere with the CAPTURE-phase
      click controller's own event delivery in some way the simpler
      kinds don't hit; or whether the issue was never actually fixed
      for every kind in the first place and this is a re-report of
      the same unresolved bug rather than a new regression. This
      environment has no way to click a real running popover to
      confirm either way.
- [ ] Real, tested fixes/additions from this round, all requested:
      - 3D Waterfall: fixed stray wireframe lines cutting across tall
        peaks (once Height was added) - an earlier version drew every
        cell's fill in one full depth-sorted pass, then every cell's
        outline in a *second*, fully separate pass after it,
        regardless of depth; a farther quad's outline (e.g. the
        mostly-hidden side of a tall peak) could then get painted
        *after*, and on top of, a nearer quad's already-correct fill.
        Interleaved into one pass (fill then outline per quad, in
        depth order) so occlusion is correct again.
      - Spectrogram/Terrain/Waterfall given a "High Level" setting
        (scale_level_for_color) - remaps a chosen level to read as the
        top of the color scale instead of requiring a true 1.0 reading
        to ever show the hottest color, letting the visible color
        range be dialed in against whatever a track's real levels
        typically reach. Defaults to 1.0 (today's original behavior,
        unchanged) rather than a lowered default, so this is purely an
        added control, not a changed default.
      - Pipes given Tube Shading (on by default) - a thin, semi-
        transparent light stroke offset to one side of each segment,
        mimicking a specular highlight along a cylinder - the cheapest
        plausible stand-in for real lighting Cairo has no model for,
        requested as "shading/reflection."
      - Spectrum given a lagging Peak Hold indicator per bar (reuses
        update_peak_hold/peak_hold_seconds, the same generic logic and
        settings field the Peak Meter window already has) and a Style
        choice - Smooth, a continuous Catmull-Rom-spline curve through
        the same per-bar levels (with a real left-to-right rainbow
        gradient fill in Rainbow mode, via Cairo's own LinearGradient,
        since there's no longer a per-bar boundary to color
        independently once it's one filled shape) - alongside the
        original Bars.
      - Every aux window's canvas background is now theme-reactive
        (canvas_background_rgb, reading
        Adw.StyleManager.get_default().dark fresh each draw - the same
        property window.py's own header-tinting already uses) instead
        of one of a few near-identical hardcoded dark grays regardless
        of the app's Light/Dark/Follow System setting. DVD Bounce is
        the one exception - its background is already a user-set
        color (dvd_bg_color), so this would override an explicit
        choice rather than respect it.
- [ ] A "coordinator" window to launch/toggle every visualizer type
      from one place, including the main Butterchurn visualizer
      itself - requested. Today, opening each of the 11 aux window
      kinds (VU Meter/Peak Meter/X-Y Scope/Oscilloscope/Vector Scope/
      Spectrum/Spectrogram/3D Terrain Spectrogram/3D Waterfall/DVD
      Bounce/Pipes) means digging through the hamburger menu's
      "Visualizer Windows" submenu one at a time - that submenu has
      grown to 11 entries and counting, and there's no single place to
      see or manage what's currently open across all of them at once.

      Straightforward to build on existing infrastructure: each aux
      window kind is already backed by one stateful GAction
      (win.show-<kind>, AUX_WINDOW_ACTIONS in window.py) whose state
      already stays correctly in sync in both directions - toggled on
      opens the window, toggled off (or the window's own close button,
      via aux_window_closed) flips the action back off - so a
      coordinator dialog's rows could just be Gtk.Switches bound
      directly via action_name="win.show-<kind>" (GTK's own built-in
      switch-to-stateful-action binding), no new sync/glue code needed
      for the aux-window rows themselves. A dialog in the same style
      as the existing Preferences/Playlists ones (Adw.PreferencesDialog
      or Adw.Dialog + Adw.PreferencesPage, one row per kind, maybe
      grouped - meters/scopes/spectrum-family/screensaver-style) would
      fit the app's existing patterns.

      One real design question, not yet resolved: what "toggling
      Butterchurn" from this window would actually mean, since the
      main visualizer isn't an aux window - it's the primary
      MelangeWindow itself, the one every aux/mirror window's own
      close-request cascade depends on, and closing it quits the
      whole app (on_close_request). Candidates: (a) present()/focus
      it if it's behind other windows or minimized - the least
      surprising option, but a weak fit for a toggle *switch*
      specifically; (b) a genuine pause/resume of Butterchurn's own
      rendering (freeze the WebGL canvas) while leaving the process
      and window alive - more work (would need a JS-side pause hook
      main.js doesn't have today) but a real toggle in the way the aux
      windows' own switches are; (c) toggle fullscreen. Needs deciding
      before implementation, not just picking one blind.
- [ ] Share one FFT computation across the aux windows that each run
      their own right now, requested - Spectrum, Spectrogram, Terrain,
      Waterfall, and Pipes (aux_window.py) each keep an entirely
      separate self.spectrum_buffer and independently run fft() on it,
      even though every one of them is analyzing the exact same live
      audio stream (window.py's forward_audio_to_aux_windows pushes
      identical chunks to all open aux windows via push_audio). With
      several of these kinds open at once - a real scenario now that
      there are five of them - that's the same 2048-sample Hann-
      windowed radix-2 FFT recomputed redundantly, once per window,
      every ~50-80ms each. Real fix would move the FFT up a level -
      computed once per audio chunk in window.py (or a small shared
      helper aux windows pull from) and handed to every open FFT-
      consuming aux window, rather than each one owning its own
      buffer/FFT call - not attempted yet since it means restructuring
      where spectrum_buffer/fft() actually live (out of
      AuxVisualizerWindow and into something MelangeWindow owns and
      distributes), not a small change.
- [ ] Pipes reported as still not reactive enough even after the
      per-band gain compensation, stronger speed/width/turn-chance
      multipliers, and turn-probability-driven-by-band-level changes
      above - not yet revisited in more depth. Worth trying next,
      roughly in order of likely impact: (1) confirming with the user
      whether this is post-rebuild (the gain-compensation fix needs a
      fresh install to take effect) before assuming the fix itself
      fell short; (2) tuning PIPES_BAND_GAINS further against real
      music rather than the synthetic test signal used to verify the
      pipeline mechanically works (a sawtooth-like repeating pattern,
      not representative of real spectral balance); (3) an adaptive
      per-band normalization (track each band's own recent rolling
      max/average and react to *relative* changes within that band's
      own range, rather than comparing against one fixed absolute
      scale calibrated for a full-scale sine) instead of/alongside
      fixed gains, which would adapt to whatever's actually playing
      rather than needing hand-tuned constants at all.
- [ ] 3D Waterfall built - a new aux window kind, distinct from the
      existing 3D Terrain Spectrogram: same rows-of-FFT-magnitude-
      over-time data, same rotatable oblique camera (project_3d_point,
      generalized when Terrain was built to take azimuth/elevation
      explicitly rather than reading a single shared value, exactly so
      more than one such window could exist), and the same render-to-
      cached-surface pattern (waterfall_surface/waterfall_dirty,
      mirroring terrain_surface/terrain_dirty) - but flat rather than
      height-extruded: magnitude reads purely as color
      (gradient_color, its own separate Low/High pair from Terrain's)
      on a flat plane, the same per-cell block-color look the 2D
      Spectrogram's own heatmap already has, just projected through a
      rotatable camera instead of drawn straight onto the canvas.

      Needed per-cell (not per-row, unlike Terrain) depth sorting: a
      flat plane's own rotation can put a far corner of one row closer
      to the camera than a near corner of another once azimuth departs
      from 0, so painter's algorithm has to operate on individual
      quads (row_count-1 x bin_count-1 of them) rather than whole rows
      to stay correct at arbitrary angles. Cell fills are drawn with
      antialiasing off specifically (only for those fills, restored
      right after) - adjacent quads share exact corner coordinates,
      but antialiased edges between differently-colored neighbors
      still left faint seams otherwise; crisp edges read as one
      continuous tiled surface instead. Given a steeper default
      elevation (55° vs Terrain's 28°) and an elevation floor of 10°
      (vs Terrain's -10°) - a flat plane viewed too edge-on is far
      harder to read than a ridge relief is at the same angle, since
      there's no height to hint at the surface's own orientation.
      Verified against real fabricated row data through the actual
      rendering path (not just "no exception") - confirmed varied
      colored output.

      Given a Rainbow palette option on request, alongside the
      existing Low/High Gradient one - a full hue sweep
      (rainbow_color, colorsys.hsv_to_rgb: red at full scale down
      through orange/yellow/green/cyan to blue-violet at silence,
      brightness rising with level) rather than a 2-stop lerp between
      two fixed endpoints, the classic "rainbow spectrum analyzer"
      look. Added to Spectrogram and Terrain too (a Palette dropdown,
      PALETTE_CHOICES, each kind's own field -
      spectrogram_palette/terrain_palette/waterfall_palette), not just
      Waterfall - defaults to Rainbow on all three now, on request
      ("rainbow option for all spectrogram[s]"). Waterfall additionally
      given a Textured toggle (default on) - a thin dark outline
      stroked around every cell after all the fills, in a second pass
      (antialiasing switched back on just for that pass) rather than
      interleaved with the fills, so the surface reads as a tiled grid
      instead of one smoothly blended blob, the way "other audio
      visualizers" tend to render this kind of display; off reverts to
      the original smooth-blend look.

      Then extended further on request ("rainbow color scheme option
      for all the mini visualizers") to every other kind that already
      had a plain Color setting - X-Y Scope, Spectrum, VU Meter
      (needle style only - bars/LED intentionally keep their fixed
      green/yellow/red zone colors, since those carry real meaning
      about headroom/loudness that a rainbow would erase), Oscilloscope,
      and Vector Scope - via a shared Color Mode dropdown
      (COLOR_MODE_CHOICES: Solid/Rainbow, one shared self.color_mode
      field, the same way self.color itself is already shared generic
      state across these kinds) added right alongside each kind's
      existing Color row. What "rainbow" means differs by kind, since
      a continuous single-color stroke can't have a color that changes
      along its own length: Spectrum bars are colored by bar position
      (a stable gradient across frequency, not fluctuating with
      loudness - the classic rainbow-EQ look); X-Y Scope/Oscilloscope
      traces and Vector Scope's dot cloud are colored by sample index
      within the current chunk (a moving "rainbow comet trail" instead
      of one flat trace color), drawn as many short per-point-colored
      segments/dots instead of one batched stroke/fill, which does
      cost more draw calls than the solid-color path; VU Meter's
      needle is colored by its own live level, the same "hue by
      magnitude" idea Terrain/Waterfall/Spectrogram use.

      Oscilloscope specifically defaults to Solid rather than Rainbow
      (unlike every other kind above) - its Time Base can run up to
      2048 points per channel, and rainbow mode there would mean
      thousands of individual stroke() calls every redraw at the same
      unthrottled audio-chunk-driven rate already identified as the
      likely cause of the flat Spectrogram's own reported lag (see
      the **PERF** entry under Urgent) - defaulting it off avoids
      reintroducing that same class of problem by default; still
      selectable for whoever wants the trail look enough to accept the
      cost.

      A follow-up batch after that, all requested: Terrain's rainbow
      coloring was reported as reading "mostly just blue" - traced to
      coloring each ridge by that row's *average* level across every
      bin, when most individual frequency bins are quiet at any given
      moment even when the track isn't, dragging almost every row
      toward the low (blue-violet) end of the hue sweep almost all the
      time. Switched to that row's *peak* level instead - did *any*
      frequency in it hit hard - which uses the rainbow's actual range
      far better; affects the Gradient palette's look too since it's
      the same underlying value, which is a fair trade given both were
      using the same flawed "average" logic before.

      Waterfall given real Height (waterfall_height_scale, a settings
      slider, default 0.5) - each grid point's own Z now comes from
      that exact cell's own level (not a per-row average, unlike
      Terrain), turning the previously flat colored plane into a
      genuine height-mapped mesh without losing its per-cell color/
      texture, combining what were previously two separate ideas
      (Terrain's height, Waterfall's per-cell color) into one surface.
      0 keeps the original flat look.

      Terrain, Waterfall, and Pipes all given Zoom (a settings slider
      plus scroll-wheel support - Gtk.EventControllerScroll added
      alongside each one's existing rotate-drag gesture, clamped to
      0.4x-3.0x, reset by each kind's own Reset View button) and an
      axes/frame option: Terrain and Waterfall get real labeled
      reference axes (draw_3d_axes, a new shared helper - Frequency/
      Time, plus a Level axis when there's a height dimension to show,
      drawn fresh onto the visible frame every time rather than baked
      into either one's cached render surface, the same reasoning
      Vector Scope's own axis crosshair already uses); Pipes gets a
      wireframe outline of its cube grid's own boundary instead (Show
      Grid) - there's no single meaningful "zero" origin for a
      literal X/Y/Z axis triple to anchor at in a walked grid the way
      there is for a magnitude/frequency/time plot.

      Pipes' colors now pulse too (Pulse Color, on by default,
      alongside the existing Pulse Tube Width) - brighter toward
      vivid on a loud moment for that pipe's own band, dimmer toward
      dark on a quiet one, the same band_level already driving speed
      and width, just applied a third way.

      X-Y Scope given a Line Width setting (previously a hardcoded
      1.0).
- [ ] A 3D X-Y Scope and a 3D Vector Scope, requested - not yet built,
      no design decided. The existing X-Y Scope/Vector Scope are both
      flat 2D plots (L vs R, or the rotated Mid/Side goniometer); a
      "3D" version presumably adds time as a third axis - e.g. a
      ribbon/tube tracing L/R (or M/S) through recent history instead
      of only ever showing the current instant, viewed through the
      same rotatable oblique camera (project_3d_point) the other 3D
      kinds already share. Would need its own rolling sample-history
      buffer (closer to Oscilloscope's scope_left/scope_right than the
      plain X-Y Scope/Vector Scope's own "just the current chunk"
      approach) to have enough depth to actually show as a 3D path
      rather than a flat instant.
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

- [ ] Reconsider the aux visualizer windows' architectural strategy,
      requested - specifically: should they split out into a separate
      project, and independent of that, being able to run them
      *without the main Butterchurn visualizer running at all*. Not a
      small ask - real coupling exists today between
      AuxVisualizerWindow (aux_window.py) and MelangeWindow
      (window.py) that any answer here has to reckon with:
      - `AuxVisualizerWindow.__init__(self, primary, kind, **kwargs)`
        takes the main window as `primary` and constructs itself with
        `application=primary.get_application()` - it needs a live
        Adw.Application (currently always MelangeWindow's own) to
        exist at all, and `on_close_request` calls back into
        `primary.aux_window_closed(...)` to keep the main window's
        own tracking dict in sync.
      - Aux windows have **no audio capture of their own** - they're
        purely passive `push_audio(left, right)` receivers. The one
        real GStreamer capture pipeline (`start_system_audio`/
        `on_audio_sample` in window.py) lives entirely inside
        MelangeWindow and feeds both the main webview
        (`send_audio_to_webview`) and every open aux window
        (`forward_audio_to_aux_windows`) from the same chunks - there
        is no path today where audio capture runs without the main
        window (and its webview/Butterchurn) existing first.
      - Every aux window is opened via a `win.show-<kind>`
        `Gio.SimpleAction` registered *on MelangeWindow itself*
        (`AUX_WINDOW_ACTIONS`), tracked in MelangeWindow's own
        `self.aux_windows` dict, and MelangeWindow's own
        `on_close_request` cascades to close every open aux (and
        mirror) window - closing the main window currently means
        closing everything, by design.
      - `main.py`'s `do_activate()` unconditionally creates a
        MelangeWindow (which builds the full WebKit webview and starts
        audio capture as part of its own construction) - there's no
        alternate entry point or flag that skips the webview/Butterchurn
        and starts, say, just audio capture plus an aux-window shell.

      A few candidate directions, roughly in order of how much they'd
      actually change today's structure:
      1. Extract the audio-capture pipeline out of MelangeWindow into
         its own reusable component (an `audio_source.py`-style
         module/class) - this is close to a prerequisite for either of
         the two directions below, since the capture logic is
         currently baked directly into MelangeWindow's own methods
         with no seam to reuse it independently.
      2. Stay one app/repo, but make the main Butterchurn webview
         *optional* at startup - a launch flag or a first-run choice
         between "full visualizer" and "widgets only," where widgets-
         only skips webview.py's WebView entirely, starts just the
         (now-extracted) audio capture, and shows an aux-window
         coordinator shell instead of the main canvas. Directly answers
         "run them without Butterchurn running" while keeping
         everything in one codebase - probably pairs well with the
         separately-logged "coordinator window" idea above, which
         already assumed one shared app.
      3. A genuinely separate project/Flatpak for the aux windows -
         cleanest separation architecturally, but means the audio-
         capture logic and any shared visual/settings-popover
         infrastructure would need to be duplicated or factored into a
         shared library rather than just living in one repo - real
         ongoing maintenance cost (two release cycles, two Flatpak
         manifests) for a cleaner boundary. Worth weighing against #2
         actually solving the stated need (run without Butterchurn)
         without that cost.
      No direction chosen yet - this is a planning note, not a
      decision.
- [ ] A fractal Butterchurn preset - requested, not yet built. A
      native Butterchurn (`.json`) preset (Butterchurn presets are
      HLSL/GLSL-ish shader expressions under the hood, per-pixel/per-
      vertex equations, not baked images) rendering something
      fractal - e.g. a Mandelbrot/Julia-set-style iterated escape-time
      shader, with iteration count/zoom/julia-constant driven off
      bass/mid/treb the way the existing baron/butterchurn-presets
      packs already do for their own effects - rather than an .milk
      file run through the converter, since a from-scratch preset
      doesn't need repairBadShader's workarounds or hit the converter
      compatibility issues noted elsewhere in this file (Paused
      section). Would live alongside the existing bundled packs
      (src/web, per the Building section of README) rather than
      needing any Python/GTK-side changes at all.
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
- [ ] Requested batch of further aux window work, one at a time, each
      committed on its own once done:
      1. Peak Meter - built, see below. A hardware-style peak meter distinct from the
         existing VU Meter: instantaneous per-channel peak (not RMS)
         with a peak-hold indicator (a thin line that jumps to a new
         peak instantly and decays back down slowly on its own timer,
         separate from the bar's own fast-attack/slow-release
         ballistics) and a dBFS scale, since peak and VU-style average
         loudness are genuinely different readings professionals
         watch for different reasons (peak for clipping headroom, VU
         for perceived loudness) - not just a reskin of the VU Meter.
      2. Oscilloscope - built, see below. A time-domain waveform
         (amplitude vs time, both channels), distinct from the
         existing X-Y Scope (which plots L against R, not either
         channel against time).
      3. Vector Scope - built, see below. A proper phase-correlation
         "goniometer" style display (traditionally rotated 45° from a
         plain L/R X-Y plot, so mono material draws a vertical line
         and out-of-phase material spreads horizontally), with
         intensity/persistence trails, M/S axis labels, and a phase
         correlation reading - a more information-dense relative of
         the existing X-Y Scope, not a duplicate of it.
      4. 3D Terrain Map Spectrogram - built, see below. The existing
         Spectrogram's waterfall data (magnitude per frequency bin per
         time column) rendered as an oblique pseudo-3D ridge-line
         terrain (height = magnitude) instead of the existing flat 2D
         heatmap - a new aux window kind, not a mode of the existing
         Spectrogram.
      5. A vertical-orientation setting for the existing (rectangular)
         Spectrogram - built, see below.
      6. Pipes - built, see below. A "3D Pipes"-screensaver-style aux
         window: a handful of colored tubes turning corners and
         filling the window on a simple grid-walk, not audio-analysis-
         driven the way the others are - live audio just modulates
         pipe speed/spawn rate, for consistency with the rest of this
         feature rather than because the reference screensaver itself
         reacts to anything.

      Batch complete - all six items above are now built.

      A few follow-up fixes/polish from live testing after the batch
      landed: Peak Meter/Oscilloscope/Vector Scope/Pipes windows
      weren't opening at all - build_settings_popover() was called
      partway through AuxVisualizerWindow.__init__, before those four
      kinds' own settings state was actually assigned further down in
      the same method, so opening one raised an AttributeError mid-
      construction with nothing catching it (the toggle action's state
      still flipped "on", but the window itself never got created) -
      fixed by deferring the popover build to the true end of
      __init__. Separately, launching Melange was sometimes presenting
      a leftover aux/mirror window instead of the main one -
      do_activate() (main.py) used get_active_window(), which returns
      whichever of the application's windows last had focus, not
      necessarily the real main window; fixed by tracking the main
      window explicitly as self.main_window instead. The Oscilloscope
      also had its R channel drawn at reduced opacity relative to L (a
      deliberate but unrequested stylistic choice) - reported as
      looking wrong, so both are now full opacity. Vector Scope
      gained a Dot Size setting (previously a hardcoded 1.2px).
      Terrain and Pipes' default window size was bumped from 360x220
      to 520x360 - a rotatable 3D view reads as a cramped sliver at
      the smaller size.

      A real, serious bug then turned up in Terrain from live testing
      ("I only see grey" / "does not work"): `render_terrain_surface`
      called `cr.move_to(*points[0])`, splatting a 3-element
      `(screen_x, screen_y, depth)` tuple (project_3d_point's return
      value) into a Cairo method that only accepts 2 args - a
      TypeError on every real frame once any row data existed at all.
      Missed entirely by the earlier direct-construction test (see
      above) because that test never fed real audio/FFT data, so
      terrain_rows stayed empty and this code path never actually ran;
      only caught by re-running that same test with fabricated row
      data force-appended, which is what turned up the traceback.
      GTK/PyGObject swallows an exception raised inside a
      Gtk.DrawingArea's draw_func rather than crashing the app, which
      is why the window still opened - it just never painted anything
      but its own initial dark background fill and (per the report) a
      generic gray fallback where the widget's own snapshot should
      have been. Fixed (`cr.move_to(points[0][0], points[0][1])`, both
      occurrences) and re-verified against real fabricated row data
      through the actual rendering path this time - confirmed varied,
      real gradient-colored output (317 distinct sampled colors, not a
      flat fill) rather than just "no exception was raised."

      Also while addressing this: Spectrogram gained selectable Low/
      Color and High Color settings (gradient_color, a plain 2-stop
      linear interpolation) replacing the previous fixed 4-stop
      black-blue-green-yellow-red thermal colormap (heatmap_color,
      removed - fully superseded, not left as dead code). Terrain's
      ridges are now colored by that row's own average loudness
      through the same two-stop gradient (its own separate Low/High
      Color pair) rather than one flat Color tint across the whole
      terrain regardless of how loud any part of it was - a real
      elevation-map-style visualization now, not just a colored
      wireframe.

      Pipes given three more audio-triggered effects, on request:
      tube width now pulses with each pipe's own band's live level
      (Pulse Tube Width switch - a second, more continuous
      reinforcement of the per-band reactivity on top of speed);
      React to Beats (same rolling-average energy-jump detector as DVD
      Bounce's own, kept separate/self-contained) spawns one bonus
      pipe beyond pipes_max_pipes right on a detected hit, fading back
      to the normal count as it eventually dies out; and slow ambient
      Auto-Rotate (adjustable Rotation Speed) continuously turns the
      camera on its own, pausing cleanly while a manual rotate drag is
      in progress and resuming from wherever that drag left it
      afterward, rather than fighting the drag or snapping back.

      DVD Bounce's icon was reported expanding from its top-left
      corner instead of its center as it pulsed with level/beats -
      dvd_x/dvd_y are the bounding box's corner (what the collision
      math and every draw function already treat them as), so growing
      dvd_size alone visibly grew the box from that corner. Fixed by
      shifting the corner by half of whatever the size just changed by
      on every resize, keeping the box's center fixed across it - a
      purely cosmetic correction, doesn't touch movement or bouncing.

      X-Y Scope given the same Labels toggle every other scope-family
      kind already has, on request - faint crosshair axis lines plus
      L/R labels (L horizontal, R vertical, matching how
      self.left[i]/self.right[i] are actually plotted), shown even
      before any real audio has arrived rather than only once a trace
      exists, unlike the trace itself.

      Two more Terrain reports from live use: drag-to-rotate felt
      reversed (dragging right visibly rotated the opposite way from
      the usual "grab the surface and drag it the way you want it to
      turn" expectation) - negated on_terrain_drag_update's azimuth
      mapping (and on_pipes_drag_update's identical one, same fix,
      same bug). And at some camera angles the outline of a quiet
      (low-level) ridge was hard to distinguish from the near-black
      background - added `ensure_min_brightness`, which scales a color
      up toward white (preserving hue, not a flat per-channel clamp -
      that would erase it) if it's darker than a floor, applied to the
      ridge-line stroke specifically (not the filled body underneath,
      which is left free to read as naturally dark/quiet) so the
      outline always stays visible as a distinct line regardless of
      the chosen Low Color or how quiet that row was.

      A third report ("3d spectrogram colors do not work it is only
      green") not yet resolved - direct testing (both the actual
      gradient_color math against the real default Low/High colors,
      and a real fabricated-data render through render_terrain_surface
      itself, see above) turned up no code path that could produce
      green from the current blue-to-orange defaults, so the cause
      isn't understood yet. Asked the user for a screenshot rather
      than keep guessing blind - this environment has no way to see
      the running app itself.

      Pipes reported as not really reacting to frequencies despite the
      per-band work above - the likely real cause, found on review:
      magnitude_in_band uses the same fixed-reference scale as
      bars_from_magnitudes (calibrated for a full-scale sine's peak-
      bin magnitude) for every band equally, but real music's spectral
      energy rolls off heavily with frequency - a 1000-4000Hz or
      4000-16000Hz bin's raw magnitude sits far below a 20-250Hz one's
      for most tracks. Without compensation, 3 of 4 pipes (everything
      but the bass one) rarely see a high enough band_level to look
      like they're reacting at all - not a formula bug in how
      band_level then drives speed/turning, but the level itself
      almost never getting there for non-bass bands. Fixed with
      PIPES_BAND_GAINS (1.0/1.6/2.8/4.5, same index as PIPES_BANDS) -
      a per-band multiplier compensating for that roll-off, applied in
      update_pipes_band_levels before the ballistics/clamp.

      Also strengthened the reaction itself while addressing this:
      speed's own band_level multiplier raised (1.2 -> 2.5), tube-
      width's pulse raised (0.8 -> 1.5), and turn probability
      (step_pipe, previously a flat 0.25 for every pipe regardless of
      audio) is now itself band_level-driven (0.15 base, up to +0.5 at
      full level and Reactivity) - a pipe visibly changing direction
      more often is a much more perceptible cue at a glance than
      "gliding slightly faster," on top of the two that already
      existed.

      Given a Tube Width settings slider (previously a hardcoded
      constant) and per-band Color pickers (Bass/Low Mid/High Mid/
      Treble, previously PIPES_BAND_COLOR_HEXES was fixed) on request -
      spawn_pipe now reads pipes_band_colors (settings-backed) instead
      of parsing the fixed hex list directly; changing a color only
      affects pipes spawned after that point, same as every other per-
      pipe property.

      Peak Meter built first: instantaneous per-channel |sample| peak
      (no VU_GAIN, unlike the VU Meter - a peak meter exists to show
      real headroom against 0dBFS, and artificially inflating that
      would defeat the point), decayed the same fast-attack/slow-
      release way as the VU Meter's own bars (shared Decay setting).
      Displayed on a proper -60..0 dBFS log scale (db_frac) rather
      than linear - most of a track's dynamic range lives in the top
      ~20dB, which linear would crush into a sliver - with its own
      red-zone threshold (>-3dB) tighter than the VU Meter's, since a
      peak meter's red means "near clipping", not "loud". A peak-hold
      marker (a thin line, separate from the bar's own fill) jumps to
      a new peak instantly and lingers for a settings-adjustable Peak
      Hold Time before falling back down at a fixed rate, never below
      the live reading - the classic hardware behavior, so a brief
      transient stays readable past the very next frame. Labels
      (shared switch/row with VU Meter, generalized from "Frequency
      Labels" to "Labels") shows a dB scale down the left edge plus
      L/R channel labels, same as VU Meter's L/R labels.

      Two more things fixed/added alongside this, from live testing
      feedback: a Background Color setting for DVD Bounce (previously
      a hardcoded dark gray), and a real bug in the settings popover
      itself - clicking outside it was reported to not close it,
      despite Gtk.Popover's own default autohide=True (no code was
      found disabling it, and every other Popover already in this
      codebase - including this exact settings popover once it was
      switched to Gtk.MenuButton, see the earlier "settings gear
      doesn't open" fix above - relies on that same default). Rather
      than leave that unresolved on faith in a default that wasn't
      visibly working, added an explicit belt-and-suspenders fallback:
      a CAPTURE-phase Gtk.GestureClick on the whole aux window that
      pops the settings popover down on any press, without claiming
      the event sequence - so a click that lands on the settings
      button or the popover's own content never reaches this handler
      at all (each owns its own hit region), and everything else
      (drag-to-move, header buttons, the popover's own controls)
      keeps working exactly as before.

      Oscilloscope built second: a real triggered scope, not just a
      naive "plot the last N samples" scroll - a rolling 4096-sample
      buffer per channel (push_audio appends into it, replacing
      self.left/self.right's own "latest chunk only, wholesale-
      replaced" approach that every other kind still uses) is searched
      each frame for a rising zero-crossing near its start
      (find_scope_trigger_index), and the display window is drawn
      starting there. Without that, the fixed-size window plotted
      every frame would slide/jitter left-right randomly, since
      successive audio chunks land at an arbitrary phase relative to
      whatever's being displayed - triggering on the same point in the
      waveform's own cycle each time is what makes a real hardware
      scope's display look "locked" instead of swimming. A Trigger
      switch turns this off (falls back to just the most recent
      window, useful for looking at noise/silence where there's no
      clean crossing to lock onto), and a Time Base slider controls
      how many samples (256 up to half the rolling buffer) are shown
      at once - smaller reads as more zoomed-in/higher frequency
      resolution, larger shows more waveform cycles at once. Two
      channels stacked (L above R, R at reduced opacity, both sharing
      the Color setting) rather than overlaid in one plot, matching
      how the VU Meter/Peak Meter already lay out two channels side by
      side.

      Vector Scope built third: draws in rotated Mid/Side space
      (mid = (L+R)/sqrt(2) on the vertical axis, side = (L-R)/sqrt(2)
      on the horizontal) rather than plain L/R like the existing X-Y
      Scope - mono material collapses to a vertical line (side is
      always 0), fully out-of-phase material to a horizontal one (mid
      is always 0), the standard goniometer reading. Real
      phosphor-style persistence, not just a differently-rotated X-Y
      Scope: an offscreen `cairo.ImageSurface` (vector_surface,
      recreated whenever the drawing area's size changes) accumulates
      across frames - GTK's own draw_func gives a fresh render target
      every call with no memory of the last frame's pixels, so this is
      managed by hand. Each frame, instead of clearing to the
      background, the existing trail is partially overpainted with the
      background color at a low alpha (1 - Persistence, a settings
      slider using the same "higher = slower decay" convention as
      Decay elsewhere in this file) before the new sample points are
      drawn on top - old points fade out over several frames rather
      than vanishing instantly, giving the display density/shape
      instead of a single flickering dot. Drawn as a scatter of small
      dots (not connected line segments the way the X-Y Scope's
      Lissajous trace is) - a goniometer's cloud shape is the
      information, not a path through it. Axis lines/labels and a
      numeric phase-correlation reading (stereo_correlation - Pearson
      correlation of L and R, +1 mono, 0 wide stereo, -1 out of phase
      and a real mono-compatibility risk if sustained) are drawn fresh
      onto the visible frame every time rather than baked into the
      fading trail surface, since they're fixed reference marks, not
      part of the signal being displayed.

      3D Terrain Spectrogram built fourth, on request also given
      drag-to-rotate (azimuth from left/right drag, elevation from
      up/down drag, clamped to -10°..85° so the camera can't flip
      upside-down or go perfectly top-down and look degenerate) -
      the one aux window kind that spends its canvas drag gesture on
      camera rotation instead of the drag-from-anywhere window-move
      every other aux window kind has (still movable via its header
      bar, GTK's native CSD behavior, just not from the canvas). A
      Reset View button in its settings returns to the default 3/4
      oblique angle.

      Same underlying waterfall data as the flat Spectrogram (FFT
      magnitude per frequency bin per time row, via
      bars_from_magnitudes), reused as a row count (TERRAIN_ROWS = 36,
      well under the flat Spectrogram's 200 - a rotatable terrain
      reads fine with far fewer ridge lines, and it's one more
      deliberate perf margin on top of the one below) rather than
      pixel columns. Projected through a simple oblique/orthographic
      rotation (project_terrain_point - azimuth around the height
      axis, then an elevation tilt; no perspective divide, the same
      family of technique classic ridgeline/mountain-range waterfall
      displays use, and far cheaper per point than true perspective
      would be) and rendered as filled, mostly-opaque ridge silhouettes
      (not a wireframe) sorted back-to-front by rotated depth each
      time (render_terrain_surface) - a real, if approximate,
      painter's-algorithm occlusion that looks correct at any camera
      angle, using the same "big filled shape under a ridge line, not
      a path along it" idea classic ridgeline plots use, just
      rotatable here instead of flat.

      Built specifically to avoid repeating the flat Spectrogram's own
      **PERF** issue logged above (redrawing its *entire* history on
      every audio-chunk-driven frame, not just when a new column
      actually arrives) rather than fixing that after the fact:
      terrain_surface is a cached offscreen render, and the expensive
      part (re-project every point, re-sort, re-fill/stroke every row)
      only actually reruns when terrain_dirty is set - a new row
      arriving, a rotation drag, or a settings change (Ridge Points,
      Color) - not on every one of the far-more-frequent
      audio-chunk-driven redraw requests. draw_terrain itself just
      blits that cached surface every time, which is cheap regardless
      of how often it's called.

      A Vertical Orientation setting was then added to the existing
      flat Spectrogram fifth - a settings switch swapping which axis
      carries time versus frequency (render_spectrogram/
      draw_spectrogram_labels both branch on spectrogram_vertical).
      Off (default) is the original behavior unchanged: time left-to-
      right, frequency bottom-to-top, newest column at the right edge.
      On: time top-to-bottom, frequency left-to-right (low frequency
      at the left, same reading direction the Spectrum bars already
      use), newest row at the top scrolling downward - the usual
      convention vertically-oriented waterfalls use elsewhere (SDR
      receiver software, etc.), rather than an arbitrary choice.

      Pipes built sixth and last, closing out this batch. A small
      cubic grid (PIPES_GRID_SIZE = 8) each pipe occupies one cell of
      at a time, moving to an adjacent empty cell every grid-step
      (mostly continuing straight, occasionally turning to one of the
      4 perpendicular directions - never reversing straight back the
      way it came, which would read as backtracking rather than a pipe
      growing) - a dead end (every neighbor occupied or out of bounds)
      kills that pipe, and pipes_max_pipes (settings-adjustable) are
      kept active at all times by spawning fresh ones at random empty
      cells as needed. Segments are never removed once laid, including
      from pipes that have since died - the grid stays filled until
      enough of it (PIPES_RESET_FRACTION = 60%) is occupied, at which
      point everything clears and starts over, the same periodic
      "reset and start fresh" behavior the reference screensaver has.
      Reuses the oblique 3D projection built for the Terrain window
      (project_terrain_point generalized to project_3d_point, taking
      azimuth/elevation as explicit parameters rather than reading a
      single shared self.azimuth/elevation, since Terrain and Pipes
      windows can both be open at once, each with its own camera) and
      the same drag-to-rotate camera control, requested for "the 3d
      ones" - like Terrain, Pipes spends its canvas drag gesture on
      rotation rather than window-move, with a Reset View settings
      button to return to the default angle. Unlike Terrain, there's
      no render-caching here (no terrain_surface/terrain_dirty
      equivalent) - Pipes redraws every ~16ms via its own independent
      GLib timer regardless (same reasoning DVD Bounce's own timer
      already established: it has to keep moving smoothly through
      silence and between audio chunks, not just when audio happens to
      arrive), and unlike the flat Spectrogram's history, total
      segment count here is naturally bounded by the small grid
      volume (512 cells at the default size) rather than an
      independently-growing buffer, so redrawing fresh every tick was
      judged cheap enough not to need the same caching treatment.
      Live audio originally modulated grid-step speed only (one shared
      broadband level, VU-style decayed RMS) - since upgraded (see
      below) to a real per-pipe frequency split, so this no longer
      applies as written; kept for the history.

      Pipes later given per-pipe frequency-band reactivity, on
      request: each pipe is assigned one of four bands
      (PIPES_BANDS - Bass/Low Mid/High Mid/Treble, 20-250/250-1000/
      1000-4000/4000-16000Hz) at spawn, cycled rather than randomly
      picked so a handful of pipes spread evenly across the spectrum
      instead of clustering, and colored to match
      (PIPES_BAND_COLOR_HEXES, same index) so the effect is actually
      visible, not just a hidden behavioral difference. Needed a real
      structural change, not just a new formula: pipes_tick used one
      shared step_timer/speed for every pipe, so each pipe now carries
      its own step_timer and steps independently at a speed driven by
      its own band's level (update_pipes_band_levels - a small,
      throttled FFT, same rolling spectrum_buffer/Hann-window/fft()
      machinery as Spectrum/Spectrogram/Terrain, feeding
      magnitude_in_band, a generalization of bars_from_magnitudes to
      an arbitrary Hz range instead of one of its own log-spaced
      bars). Also given a fade-out transition on reset (automatic or
      the settings Reset button), on request: the batch about to be
      cleared is kept as pipes_fading_segments and drawn alongside the
      new batch at a linearly-decaying alpha (Fade Time,
      settings-adjustable) instead of vanishing instantly, expiring
      once fully faded rather than continuing to be
      projected/sorted/drawn for nothing; a Fade Old Segments switch
      turns this off entirely (reverts to the original instant clear).
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
