# window.py
#
# Copyright 2026 Cameron
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program.  If not, see <https://www.gnu.org/licenses/>.
#
# SPDX-License-Identifier: GPL-3.0-or-later

import gi
import json
import subprocess
import threading
import base64
import time
from pathlib import Path

gi.require_version("Gst", "1.0")
gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
gi.require_version("Pango", "1.0")

from gi.repository import Gtk, Adw, GLib, Gdk, Gio, Gst, GObject, Pango
from melange.webview import create_webview

Gst.init(None)

@Gtk.Template(resource_path="/com/cameronlp/Melange/window.ui")
class MelangeWindow(Adw.ApplicationWindow):

    __gtype_name__ = "MelangeWindow"

    content_box = Gtk.Template.Child()
    toast_overlay = Gtk.Template.Child()
    toolbar_view = Gtk.Template.Child()
    headerbar = Gtk.Template.Child()
    menu_button = Gtk.Template.Child()


    def __init__(self, **kwargs):

        super().__init__(**kwargs)

        self.menu_open = False
        self.hide_timer = None
        self.last_mouse_pos = None
        self.mouse_over_toolbar = False
        self.last_scroll_time = 0.0

        self.toolbar_view.set_extend_content_to_top_edge(True)
        self.headerbar.add_css_class("melange-header")

        # Webview

        self.webview = create_webview(
            on_message=self.on_webview_debug_message
        )

        self.content_box.append(
            self.webview
        )

        # Lets the window be dragged from anywhere, not just the
        # header bar. Has to run in the CAPTURE phase and on
        # content_box (the webview's parent) rather than the webview
        # itself - WebKit claims button presses for its own hit
        # testing, so a normal (bubble-phase) gesture on the webview
        # would never see them.
        drag_gesture = Gtk.GestureClick()

        drag_gesture.set_button(Gdk.BUTTON_PRIMARY)
        drag_gesture.set_propagation_phase(Gtk.PropagationPhase.CAPTURE)

        drag_gesture.connect(
            "pressed",
            self.on_window_drag_pressed
        )

        self.content_box.add_controller(drag_gesture)

        self.gst_pipeline = None
        self.current_sink = None
        self.pinned_sink = None
        self.pactl_event_timer = None
        self.webview_ready = False
        self.preset_locked = False
        self.preset_names = []
        self.preset_list_store = None
        self.preset_browser_dialog = None
        self.preset_queue = []
        self.queue_list_store = None
        self.queue_dialog = None

        self.start_system_audio()

        # The "Audio Source" submenu is defined empty in window.ui and
        # populated here since the list of real output devices changes
        # at runtime as things get plugged/unplugged. Index 1, not 0:
        # the theme selector section (window.ui) comes first now.
        section0 = self.menu_button.get_menu_model().get_item_link(
            1, Gio.MENU_LINK_SECTION
        )

        self.audio_source_submenu = section0.get_item_link(
            0, Gio.MENU_LINK_SUBMENU
        )

        self.rebuild_audio_source_menu()

        threading.Thread(
            target=self.watch_audio_changes,
            daemon=True
        ).start()

        # Mouse and Toolbar

        motion = Gtk.EventControllerMotion()

        # Same reasoning as the drag gesture above: WebKit's own hit
        # testing can consume motion events before a default BUBBLE-
        # phase controller on an ancestor widget ever sees them, so
        # this went dead in practice as soon as the pointer was over
        # the webview - which is virtually the whole window.
        motion.set_propagation_phase(Gtk.PropagationPhase.CAPTURE)

        motion.connect(
            "motion",
            self.mouse_move
        )

        toolbar_motion = Gtk.EventControllerMotion()

        toolbar_motion.connect(
            "enter",
            self.toolbar_enter
        )

        toolbar_motion.connect(
            "leave",
            self.toolbar_leave
        )

        # headerbar specifically, not toolbar_view - toolbar_view is
        # the whole AdwToolbarView, header bar strip AND the webview
        # content below it, so attaching there made mouse_over_toolbar
        # true almost any time the pointer was in the window at all,
        # and hide_toolbar bailed out on its very first check forever.
        self.headerbar.add_controller(
            toolbar_motion
        )

        self.add_controller(motion)

        # Escape only ever exits fullscreen (never toggles it on) -
        # a dialog with its own focus (Preferences, the preset
        # browser, ...) sees Escape first and closes itself instead,
        # since key controllers only fire for the currently focused
        # surface.
        escape_controller = Gtk.EventControllerKey()

        escape_controller.connect(
            "key-pressed",
            self.on_key_pressed
        )

        self.add_controller(escape_controller)

        # Same CAPTURE-phase reasoning as the drag gesture and motion
        # controller above - WebKit's own hit testing can otherwise
        # consume the event before a default BUBBLE-phase controller
        # on an ancestor widget ever sees it.
        scroll_controller = Gtk.EventControllerScroll.new(
            Gtk.EventControllerScrollFlags.VERTICAL
        )

        scroll_controller.set_propagation_phase(
            Gtk.PropagationPhase.CAPTURE
        )

        scroll_controller.connect(
            "scroll",
            self.on_scroll
        )

        self.add_controller(scroll_controller)





        # Toolbar Buttons

        self.menu_button.connect(
            "notify::active",
            self.menu_changed
        )

        self.build_sensitivity_control()



        # Audio
        action = Gio.SimpleAction.new_stateful(
            "audio-source",
            GLib.VariantType.new("s"),
            GLib.Variant("s", self.current_sink)
        )

        action.connect(
            "change-state",
            self.audio_source_changed
        )

        self.add_action(action)

        # Preset/fullscreen controls, exposed as actions (rather than
        # just calling run_js straight from the button handlers) so
        # they get real keyboard accelerators via
        # app.set_accels_for_action() in main.py, and so those
        # accelerators show up automatically in the shortcuts dialog
        # via action-name.
        next_action = Gio.SimpleAction.new("next-preset", None)

        next_action.connect(
            "activate",
            lambda action, param: self.next_preset(None)
        )

        self.add_action(next_action)

        previous_action = Gio.SimpleAction.new("previous-preset", None)

        previous_action.connect(
            "activate",
            lambda action, param: self.previous_preset(None)
        )

        self.add_action(previous_action)

        fullscreen_action = Gio.SimpleAction.new("toggle-fullscreen", None)

        fullscreen_action.connect(
            "activate",
            self.toggle_fullscreen
        )

        self.add_action(fullscreen_action)

        load_preset_action = Gio.SimpleAction.new("load-preset", None)

        load_preset_action.connect(
            "activate",
            self.load_preset_clicked
        )

        self.add_action(load_preset_action)

        browse_presets_action = Gio.SimpleAction.new("browse-presets", None)

        browse_presets_action.connect(
            "activate",
            self.browse_presets_clicked
        )

        self.add_action(browse_presets_action)

        show_queue_action = Gio.SimpleAction.new("show-queue", None)

        show_queue_action.connect(
            "activate",
            self.show_queue_clicked
        )

        self.add_action(show_queue_action)

        lock_preset_action = Gio.SimpleAction.new_stateful(
            "lock-preset",
            None,
            GLib.Variant("b", False)
        )

        lock_preset_action.connect(
            "change-state",
            self.lock_preset_changed
        )

        self.add_action(lock_preset_action)

        shuffle_action = Gio.SimpleAction.new_stateful(
            "shuffle-preset",
            None,
            GLib.Variant("b", True)
        )

        shuffle_action.connect(
            "change-state",
            self.shuffle_preset_changed
        )

        self.add_action(shuffle_action)

        preferences_action = Gio.SimpleAction.new("preferences", None)

        preferences_action.connect(
            "activate",
            self.preferences_clicked
        )

        self.add_action(preferences_action)

        self.profiles = self.load_profiles()
        self.build_preferences_dialog()

        self.menu_button.get_popover().add_child(
            self.build_theme_selector(),
            "theme-selector"
        )



    def on_webview_debug_message(self, text):

        if text.startswith("PRESET_NAME:"):
            preset_name = text[len("PRESET_NAME:"):]
            self.set_title(f'Melange - "{preset_name}"')
            return

        # A failed Load Preset (bad MilkDrop conversion, invalid
        # Butterchurn JSON, etc.) otherwise has no user-facing
        # feedback at all - it only ever reached the debug log, so a
        # rejected load just silently looked like nothing happened.
        if text.startswith("LOAD_PRESET_ERROR:"):
            reason = text[len("LOAD_PRESET_ERROR:"):]
            self.show_toast(f"Couldn't load preset: {reason}")
            return

        # The actual preset names only exist in JS (from
        # butterchurn-presets, plus anything loaded via
        # win.load-preset) - this is Python's copy, used to build the
        # native preset browser list. Sent whenever the list changes.
        if text.startswith("PRESET_LIST:"):
            self.preset_names = json.loads(text[len("PRESET_LIST:"):])

            if self.preset_list_store is not None:
                self.preset_list_store.splice(
                    0,
                    self.preset_list_store.get_n_items(),
                    self.preset_names
                )

            return

        # JS owns presetQueue - this is Python's copy, used to build
        # the queue dialog's list. Python never mutates it directly,
        # only calls enqueuePreset/removeQueueItem/moveQueueItem and
        # reflects back whatever announceQueue() reports here, same
        # pattern as PRESET_LIST: above.
        if text.startswith("QUEUE:"):
            self.preset_queue = json.loads(text[len("QUEUE:"):])

            if self.queue_list_store is not None:
                self.queue_list_store.splice(
                    0,
                    self.queue_list_store.get_n_items(),
                    self.preset_queue
                )

            return

        # Sent by the on-canvas nav arrows (index.html/main.js) instead
        # of calling nextPreset()/previousPreset() directly, so the
        # lock check (and the native toast it shows) lives in one
        # place regardless of whether a change was requested via those
        # arrows or the win.next-preset/win.previous-preset keyboard
        # shortcuts.
        if text == "NAV_NEXT":
            self.next_preset(None)
            return

        if text == "NAV_PREVIOUS":
            self.previous_preset(None)
            return

        # Distinct from NAV_NEXT so a beat-triggered advance is
        # actually visible - otherwise it's indistinguishable from a
        # manual click or a normal Cycle Interval tick.
        if text == "BEAT_NAV_NEXT":
            self.next_preset(None)
            self.show_toast("Beat detected - next preset")
            return

        # Drop detection runs alongside whichever beat mode is
        # selected whenever Beat-Driven Cycle is on - a drop is a
        # separate, rare, dramatic event neither mode has any
        # particular affinity for (see checkDrop in main.js).
        if text == "DROP_NAV_NEXT":
            self.next_preset(None)
            self.show_toast("Drop detected - next preset")
            return

        if text != "APP_READY":
            return

        self.webview_ready = True

        # Setting the action's initial state in new_stateful() only
        # sets its internal value - it does not fire "change-state"
        # (that only happens on a real activation, e.g. clicking the
        # menu item), so without this the webview never actually gets
        # told to use system audio until the user opens the menu
        # themselves. "APP_READY" is sent by main.js once
        # window.setAudioSource is actually defined and safe to call.
        action = self.lookup_action("audio-source")

        action.change_state(
            GLib.Variant("s", self.current_sink)
        )


    def toolbar_enter(self, controller, x, y):

        print(f"[{time.monotonic():.3f}] toolbar_enter x={x:.0f} y={y:.0f}")

        self.mouse_over_toolbar = True

        if self.hide_timer:

            GLib.source_remove(
                self.hide_timer
            )

            self.hide_timer = None

        self.toolbar_view.set_reveal_top_bars(True)
        self.set_cursor(None)



    def toolbar_leave(self, controller):

        print(f"[{time.monotonic():.3f}] toolbar_leave")

        self.mouse_over_toolbar = False

        self.hide_timer = GLib.timeout_add_seconds(
            3,
            self.hide_toolbar
        )


    # WebKit re-synthesizes a "motion" event at the cursor's last
    # known position on essentially every animation frame the
    # visualizer renders (confirmed from the log: dozens of events at
    # an identical x/y, ~60Hz) - completely independent of whether the
    # mouse actually moved. Reacting to those made the toolbar
    # perpetually re-reveal itself. The cursor's own real position
    # genuinely not changing is the one reliable signal that a given
    # event is one of these synthetic ones rather than real input.
    MOVEMENT_THRESHOLD_PX = 1

    def mouse_move(self, controller, x, y):

        self.set_cursor(None)

        if self.last_mouse_pos is not None:

            last_x, last_y = self.last_mouse_pos

            if (
                abs(x - last_x) < self.MOVEMENT_THRESHOLD_PX and
                abs(y - last_y) < self.MOVEMENT_THRESHOLD_PX
            ):
                return

        self.last_mouse_pos = (x, y)

        self.toolbar_view.set_reveal_top_bars(True)

        if self.hide_timer:
            GLib.source_remove(self.hide_timer)

        self.hide_timer = GLib.timeout_add_seconds(
            3,
            self.hide_toolbar
        )


    def hide_toolbar(self):

        print(
            f"[{time.monotonic():.3f}] hide_toolbar firing:",
            "mouse_over_toolbar=", self.mouse_over_toolbar,
            "menu_open=", self.menu_open,
            "is_fullscreen=", self.is_fullscreen()
        )

        self.hide_timer = None

        if self.mouse_over_toolbar:
            return False

        if self.menu_open:
            return False

        self.toolbar_view.set_reveal_top_bars(False)

        # Only in fullscreen - windowed mode still needs a visible
        # cursor for ordinary desktop interaction (moving/resizing,
        # other windows, ...).
        if self.is_fullscreen():
            self.set_cursor(Gdk.Cursor.new_from_name("none"))

        return False

    def menu_changed(self, button, param):

        self.menu_open = button.get_active()

        if self.menu_open:
            self.toolbar_view.set_reveal_top_bars(True)












    # Callbacks


    def on_window_drag_pressed(self, gesture, n_press, x, y):

        widget = gesture.get_widget()

        ok, bounds = widget.compute_bounds(self)

        if not ok:
            return

        # Skip the leftmost/rightmost 15% - that's where the on-canvas
        # preset nav arrows live (see index.html's .nav-zone rule).
        # This gesture runs in the CAPTURE phase (see its setup above)
        # so it always sees the press before the webview does; since
        # the whole canvas is one opaque WebKit widget from GTK's
        # perspective (unlike a real header bar, whose buttons are
        # separate widgets that claim their own clicks first),
        # unconditionally starting a move here would eat every click
        # meant for those buttons before WebKit ever sees it.
        width = widget.get_width()

        if width > 0 and (x < width * 0.15 or x > width * 0.85):
            return

        self.get_surface().begin_move(
            gesture.get_current_event_device(),
            gesture.get_current_button(),
            bounds.get_x() + x,
            bounds.get_y() + y,
            gesture.get_current_event_time()
        )


    def toggle_fullscreen(self, action, param):

        if self.is_fullscreen():
            self.unfullscreen()
        else:
            self.fullscreen()

    def on_key_pressed(self, controller, keyval, keycode, state):

        if keyval == Gdk.KEY_Escape and self.is_fullscreen():
            self.unfullscreen()
            return True

        return False

    SCROLL_THROTTLE_SECONDS = 0.35

    def on_scroll(self, controller, dx, dy):

        # A single physical scroll-wheel "click" can report multiple
        # small delta events in quick succession (especially on
        # touchpads/high-res wheels) - without throttling, one click
        # would fire several preset changes instead of one.
        now = time.monotonic()

        if now - self.last_scroll_time < self.SCROLL_THROTTLE_SECONDS:
            return True

        self.last_scroll_time = now

        if dy < 0:
            # Scroll up -> forward
            self.next_preset(None)
        elif dy > 0:
            # Scroll down -> back
            self.previous_preset(None)

        return True

    def lock_preset_changed(self, action, value):

        action.set_state(value)

        self.preset_locked = value.get_boolean()

        self.show_toast(
            "Preset locked" if self.preset_locked else "Preset unlocked"
        )

    def shuffle_preset_changed(self, action, value):

        action.set_state(value)

        enabled = value.get_boolean()

        self.run_js(f"setShuffle({'true' if enabled else 'false'});")

        self.show_toast(
            "Shuffle on" if enabled else "Shuffle off"
        )

    # No separate on/off action - the slider's own bottom end (0)
    # means "off", so there's exactly one control and one state to
    # reason about instead of a toggle plus an interval that could
    # disagree with each other.
    # Shared by all five sliders below: a plain Gtk.Scale (no built-in
    # draw_value bubble - that looked out of place inside a proper
    # Adw.ActionRow) as the row's suffix, with the current value shown
    # as the row's subtitle instead, updated on every change. format_fn
    # takes the raw float and returns that subtitle text.
    def build_slider_row(self, title, min_val, max_val, step, initial, format_fn, on_change, store_as=None):

        row = Adw.ActionRow(title=title)
        row.set_subtitle(format_fn(initial))

        scale = Gtk.Scale.new_with_range(
            Gtk.Orientation.HORIZONTAL,
            min_val,
            max_val,
            step
        )

        scale.set_value(initial)
        scale.set_size_request(160, -1)
        scale.set_valign(Gtk.Align.CENTER)
        scale.set_draw_value(False)

        def value_changed(scale):
            value = scale.get_value()
            row.set_subtitle(format_fn(value))
            on_change(value)

        scale.connect("value-changed", value_changed)

        row.add_suffix(scale)

        # Settings profiles (see profile_fields) need a handle on the
        # actual input widget to read/write its value directly - the
        # row itself only exposes the formatted subtitle text.
        if store_as:
            setattr(self, store_as, scale)

        return row

    def build_cycle_interval_control(self):

        def format_cycle_interval(value):
            return "Off" if value <= 0 else f"{int(value)}s"

        return self.build_slider_row(
            "Cycle Interval",
            0.0, 120.0, 1.0, 0.0,
            format_cycle_interval,
            lambda value: self.run_js(f"setCycleInterval({value});"),
            store_as="cycle_interval_scale"
        )

    def build_cycle_jitter_control(self):

        def format_cycle_jitter(value):
            return "None" if value <= 0 else f"±{int(value)}%"

        return self.build_slider_row(
            "Cycle Interval Jitter",
            0.0, 50.0, 5.0, 0.0,
            format_cycle_jitter,
            lambda value: self.run_js(f"setCycleJitter({value});"),
            store_as="cycle_jitter_scale"
        )

    def build_blend_time_control(self):

        def format_blend_time(value):
            return "Instant" if value <= 0 else f"{value:.1f}s"

        return self.build_slider_row(
            "Transition Blend Time",
            0.0, 10.0, 0.5, 3.0,
            format_blend_time,
            lambda value: self.run_js(f"setBlendTime({value});"),
            store_as="blend_time_scale"
        )

    def build_mesh_size_control(self):

        def format_mesh_size(value):
            return f"{int(value)}x{int(value * 0.75)}"

        # 8-128, matching Butterchurn's own default (48x36) at the
        # midpoint - held to a fixed 4:3 ratio (its default aspect)
        # rather than exposing width/height separately.
        return self.build_slider_row(
            "Mesh Size",
            2.0, 128.0, 1.0, 48.0,
            format_mesh_size,
            lambda value: self.run_js(f"setMeshSize({value});"),
            store_as="mesh_size_scale"
        )

    def build_framerate_control(self):

        def format_framerate(value):
            return "Uncapped" if value >= 60 else f"{int(value)} FPS"

        def framerate_changed(value):

            # The slider's top end means "uncapped" (render on every
            # animation frame), same "boundary value is the special
            # state" shape as the cycle interval's "off" at its bottom.
            fps = 0 if value >= 60 else value
            self.run_js(f"setFramerate({fps});")

        return self.build_slider_row(
            "Framerate",
            10.0, 60.0, 1.0, 60.0,
            format_framerate,
            framerate_changed,
            store_as="framerate_scale"
        )

    def build_toggle_row(self, title, initial, on_change, store_as=None):

        row = Adw.SwitchRow(title=title)
        row.set_active(initial)

        row.connect(
            "notify::active",
            lambda r, param: on_change(r.get_active())
        )

        if store_as:
            setattr(self, store_as, row)

        return row

    def build_anti_aliasing_control(self):

        return self.build_toggle_row(
            "Anti-Aliasing",
            False,
            lambda enabled: self.run_js(
                f"setAntiAliasing({'true' if enabled else 'false'});"
            ),
            store_as="anti_aliasing_row"
        )

    def build_render_scale_control(self):

        def format_render_scale(value):
            return f"{value:.2f}x"

        # Distinct from Mesh Size (the warp-grid resolution): this
        # scales the canvas's actual pixel buffer, trading sharpness
        # for GPU work below 1.0 or supersampling above it. The
        # canvas's CSS size always fills the window regardless
        # (index.html) - only the internal render resolution changes.
        return self.build_slider_row(
            "Render Resolution Scale",
            0.1, 2.0, 0.05, 1.0,
            format_render_scale,
            lambda value: self.run_js(f"setRenderScale({value});"),
            store_as="render_scale_scale"
        )

    def build_beat_cycle_control(self):

        return self.build_toggle_row(
            "Beat-Driven Cycle",
            False,
            lambda enabled: self.run_js(
                f"setBeatCycle({'true' if enabled else 'false'});"
            ),
            store_as="beat_cycle_row"
        )

    def beat_mode_changed(self, row, param):

        mode = "tempo" if row.get_selected() == 1 else "energy"

        self.run_js(f"setBeatMode({json.dumps(mode)});")

    def build_beat_mode_control(self):

        # See docs/beat-detection.md - Energy Threshold works well for
        # a normal playlist but struggles with continuously mixed
        # (e.g. DJ-mixed) audio; Tempo Tracking is aimed at that case.
        row = Adw.ComboRow(title="Beat Detection Mode")

        row.set_model(
            Gtk.StringList.new(["Energy Threshold", "Tempo Tracking"])
        )

        row.set_selected(0)

        row.connect(
            "notify::selected",
            self.beat_mode_changed
        )

        self.beat_mode_row = row

        return row

    def build_beat_sensitivity_control(self):

        def format_beat_sensitivity(value):
            return f"{value:.1f}x"

        # How far above the rolling bass-energy average a hit needs to
        # be to count as a beat - lower triggers more easily (more
        # false positives), higher requires a more pronounced hit.
        return self.build_slider_row(
            "Beat Sensitivity",
            1.1, 3.0, 0.1, 1.4,
            format_beat_sensitivity,
            lambda value: self.run_js(f"setBeatSensitivity({value});"),
            store_as="beat_sensitivity_scale"
        )

    def build_beat_cooldown_control(self):

        def format_beat_cooldown(value):
            return f"{value:.1f}s"

        return self.build_slider_row(
            "Beat Cooldown",
            0.5, 5.0, 0.5, 2.0,
            format_beat_cooldown,
            lambda value: self.run_js(f"setBeatCooldown({value});"),
            store_as="beat_cooldown_scale"
        )

    def build_beat_silence_control(self):

        def format_beat_silence(value):
            return str(int(value))

        return self.build_slider_row(
            "Beat Silence Floor",
            0.0, 150.0, 5.0, 40.0,
            format_beat_silence,
            lambda value: self.run_js(f"setBeatSilenceFloor({value});"),
            store_as="beat_silence_scale"
        )

    def theme_button_toggled(self, button, scheme):

        if button.get_active():
            Adw.StyleManager.get_default().set_color_scheme(scheme)

    def build_theme_selector(self):

        # Round light/dark/follow-system swatches - matches GNOME Text
        # Editor's EditorThemeSelector almost exactly (see .theme-
        # selector-box in style.css), rather than a labeled
        # Preferences row: quick to reach, and the current choice is
        # visible at a glance.
        box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)

        box.add_css_class("theme-selector-box")

        options = [
            ("follow", "Follow System Style", Adw.ColorScheme.DEFAULT),
            ("light", "Light Style", Adw.ColorScheme.FORCE_LIGHT),
            ("dark", "Dark Style", Adw.ColorScheme.FORCE_DARK),
        ]

        group_button = None

        for css_class, tooltip, scheme in options:

            button = Gtk.CheckButton()

            button.add_css_class("theme-selector")
            button.add_css_class(css_class)
            button.set_hexpand(True)
            button.set_halign(Gtk.Align.CENTER)
            button.set_focus_on_click(False)
            button.set_tooltip_text(tooltip)

            if group_button is None:
                group_button = button
            else:
                button.set_group(group_button)

            if scheme == Adw.ColorScheme.DEFAULT:
                button.set_active(True)

            button.connect(
                "toggled",
                self.theme_button_toggled,
                scheme
            )

            box.append(button)

        return box

    def preferences_clicked(self, action, param):

        self.preferences_dialog.present(self)

    def build_preferences_dialog(self):

        audio_group = Adw.PreferencesGroup()
        audio_group.add(self.build_sensitivity_control())

        audio_page = Adw.PreferencesPage(
            title="Audio",
            icon_name="audio-speakers-symbolic"
        )

        audio_page.add(audio_group)

        cycling_group = Adw.PreferencesGroup(title="Cycling")
        cycling_group.add(self.build_cycle_interval_control())
        cycling_group.add(self.build_cycle_jitter_control())
        cycling_group.add(self.build_blend_time_control())

        beat_group = Adw.PreferencesGroup(
            title="Beat Detection",
            description=(
                "Experimental - tuned against synthetic test signals, "
                "not yet validated against a wide range of real music. "
                "See docs/beat-detection.md."
            )
        )
        beat_group.add(self.build_beat_cycle_control())
        beat_group.add(self.build_beat_mode_control())
        beat_group.add(self.build_beat_sensitivity_control())
        beat_group.add(self.build_beat_cooldown_control())
        beat_group.add(self.build_beat_silence_control())

        playback_page = Adw.PreferencesPage(
            title="Playback",
            icon_name="media-playback-start-symbolic"
        )

        playback_page.add(cycling_group)
        playback_page.add(beat_group)

        rendering_group = Adw.PreferencesGroup()
        rendering_group.add(self.build_mesh_size_control())
        rendering_group.add(self.build_framerate_control())
        rendering_group.add(self.build_render_scale_control())
        rendering_group.add(self.build_anti_aliasing_control())

        rendering_page = Adw.PreferencesPage(
            title="Rendering",
            icon_name="preferences-desktop-display-symbolic"
        )

        rendering_page.add(rendering_group)

        profiles_page = self.build_profiles_page()

        # More than one page here is what gives the dialog its top
        # view-switcher (rather than a single flat list) for free.
        # Theme (light/dark/system) lives in the hamburger menu itself
        # instead - see build_theme_selector.
        dialog = Adw.PreferencesDialog()
        dialog.add(audio_page)
        dialog.add(playback_page)
        dialog.add(rendering_page)
        dialog.add(profiles_page)

        self.preferences_dialog = dialog

    # The (key, getter, setter) triples that make up a settings
    # profile. A single list here rather than scattering profile
    # awareness across each build_*_control method - adding a new
    # field to profiles later means adding one line here, not touching
    # every save/load call site.
    def profile_fields(self):

        def scale_field(widget):
            return (widget.get_value, widget.set_value)

        def switch_field(widget):
            return (widget.get_active, widget.set_active)

        def combo_field(widget):
            return (widget.get_selected, widget.set_selected)

        return [
            ("sensitivity", *scale_field(self.sensitivity_scale)),
            ("cycle_interval", *scale_field(self.cycle_interval_scale)),
            ("cycle_jitter", *scale_field(self.cycle_jitter_scale)),
            ("blend_time", *scale_field(self.blend_time_scale)),
            ("mesh_size", *scale_field(self.mesh_size_scale)),
            ("framerate", *scale_field(self.framerate_scale)),
            ("render_scale", *scale_field(self.render_scale_scale)),
            ("anti_aliasing", *switch_field(self.anti_aliasing_row)),
            ("beat_cycle", *switch_field(self.beat_cycle_row)),
            ("beat_mode", *combo_field(self.beat_mode_row)),
            ("beat_sensitivity", *scale_field(self.beat_sensitivity_scale)),
            ("beat_cooldown", *scale_field(self.beat_cooldown_scale)),
            ("beat_silence", *scale_field(self.beat_silence_scale)),
        ]

    def current_profile_values(self):

        return {
            key: getter()
            for key, getter, setter in self.profile_fields()
        }

    def apply_profile_values(self, values):

        # Setting a widget's value re-fires its own existing
        # value-changed/notify::active/notify::selected handler, which
        # already calls run_js(...) - so this reuses the exact same
        # apply path a manual slider drag would, rather than needing
        # its own way to push values into the visualizer.
        for key, getter, setter in self.profile_fields():
            if key in values:
                setter(values[key])

    def profiles_file_path(self):

        config_dir = Path(GLib.get_user_config_dir()) / "melange"
        config_dir.mkdir(parents=True, exist_ok=True)

        return config_dir / "profiles.json"

    def load_profiles(self):

        path = self.profiles_file_path()

        if not path.exists():
            return []

        try:
            with open(path) as f:
                return json.load(f)
        except (OSError, json.JSONDecodeError) as e:
            print("Failed to load settings profiles:", e)
            return []

    def save_profiles(self):

        try:
            with open(self.profiles_file_path(), "w") as f:
                json.dump(self.profiles, f, indent=2)
        except OSError as e:
            print("Failed to save settings profiles:", e)

    def build_profiles_page(self):

        self.profiles_group = Adw.PreferencesGroup(
            title="Saved Profiles",
            description=(
                "Snapshots of every setting on the other tabs - save "
                "the current values under a name, then load or delete "
                "them here."
            )
        )

        save_row = Adw.ActionRow(
            title="Save Current Settings as Profile…",
            activatable=True
        )

        save_row.connect("activated", self.save_profile_clicked)

        save_icon = Gtk.Image.new_from_icon_name("list-add-symbolic")
        save_row.add_suffix(save_icon)

        self.profiles_group.add(save_row)

        # Populated by refresh_profiles_list, tracked separately from
        # save_row so a refresh can remove exactly the profile rows
        # without touching the always-present save row above them.
        self.profile_rows = []
        self.refresh_profiles_list()

        page = Adw.PreferencesPage(
            title="Profiles",
            icon_name="bookmark-new-symbolic"
        )

        page.add(self.profiles_group)

        return page

    def build_profile_row(self, profile):

        row = Adw.ActionRow(title=profile["name"])

        load_button = Gtk.Button(icon_name="document-open-symbolic")
        load_button.add_css_class("flat")
        load_button.set_valign(Gtk.Align.CENTER)
        load_button.set_tooltip_text("Load")

        load_button.connect(
            "clicked",
            lambda b, name=profile["name"]: self.load_profile_by_name(name)
        )

        row.add_suffix(load_button)

        remove_button = Gtk.Button(icon_name="user-trash-symbolic")
        remove_button.add_css_class("flat")
        remove_button.set_valign(Gtk.Align.CENTER)
        remove_button.set_tooltip_text("Delete")

        remove_button.connect(
            "clicked",
            lambda b, name=profile["name"]: self.delete_profile_by_name(name)
        )

        row.add_suffix(remove_button)

        return row

    def save_profile_clicked(self, row):

        entry = Gtk.Entry()
        entry.set_placeholder_text("Profile name")

        dialog = Adw.AlertDialog(
            heading="Save Profile",
            body="Save the current Preferences as a named profile."
        )

        dialog.set_extra_child(entry)
        dialog.add_response("cancel", "Cancel")
        dialog.add_response("save", "Save")
        dialog.set_response_appearance("save", Adw.ResponseAppearance.SUGGESTED)
        dialog.set_default_response("save")
        dialog.set_close_response("cancel")

        dialog.connect(
            "response",
            lambda d, response: self.on_save_profile_response(response, entry)
        )

        dialog.present(self)
        entry.grab_focus()

    def on_save_profile_response(self, response, entry):

        if response != "save":
            return

        name = entry.get_text().strip()

        if not name:
            self.show_toast("Profile needs a name")
            return

        values = self.current_profile_values()

        # Saving over an existing name replaces it rather than
        # silently creating a duplicate entry.
        self.profiles = [p for p in self.profiles if p["name"] != name]
        self.profiles.append({"name": name, "values": values})

        self.save_profiles()
        self.refresh_profiles_list()
        self.show_toast(f"Saved profile \"{name}\"")

    def load_profile_by_name(self, name):

        profile = next(
            (p for p in self.profiles if p["name"] == name),
            None
        )

        if profile is None:
            return

        self.apply_profile_values(profile["values"])
        self.show_toast(f"Loaded profile \"{name}\"")

    def delete_profile_by_name(self, name):

        self.profiles = [p for p in self.profiles if p["name"] != name]

        self.save_profiles()
        self.refresh_profiles_list()
        self.show_toast(f"Deleted profile \"{name}\"")

    def refresh_profiles_list(self):

        for row in self.profile_rows:
            self.profiles_group.remove(row)

        self.profile_rows = [
            self.build_profile_row(profile)
            for profile in self.profiles
        ]

        for row in self.profile_rows:
            self.profiles_group.add(row)

    def show_toast(self, text):

        # Adw.Toast text is parsed as Pango markup, so raw &, <, > in
        # the message (e.g. from a JS error string) breaks parsing and
        # silently produces a toast with no text at all.
        toast = Adw.Toast.new(GLib.markup_escape_text(text))
        toast.set_timeout(2)

        self.toast_overlay.add_toast(toast)

    def browse_presets_clicked(self, action, param):

        if self.preset_browser_dialog is None:
            self.build_preset_browser_dialog()

        self.preset_browser_dialog.present(self)

    def build_preset_browser_dialog(self):

        self.preset_list_store = Gtk.StringList.new(self.preset_names)

        expression = Gtk.PropertyExpression.new(
            Gtk.StringObject,
            None,
            "string"
        )

        string_filter = Gtk.StringFilter.new(expression)
        string_filter.set_match_mode(Gtk.StringFilterMatchMode.SUBSTRING)

        filter_model = Gtk.FilterListModel.new(
            self.preset_list_store,
            string_filter
        )

        selection = Gtk.SingleSelection.new(filter_model)

        factory = Gtk.SignalListItemFactory()

        factory.connect("setup", self.preset_row_setup)
        factory.connect("bind", self.preset_row_bind)

        list_view = Gtk.ListView.new(selection, factory)

        list_view.connect("activate", self.preset_row_activated)

        scrolled = Gtk.ScrolledWindow()
        scrolled.set_child(list_view)
        scrolled.set_vexpand(True)

        search_entry = Gtk.SearchEntry()

        search_entry.connect(
            "search-changed",
            lambda entry: string_filter.set_search(entry.get_text())
        )

        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        box.set_margin_start(12)
        box.set_margin_end(12)
        box.set_margin_top(12)
        box.set_margin_bottom(12)

        box.append(search_entry)
        box.append(scrolled)

        toolbar_view = Adw.ToolbarView()
        toolbar_view.add_top_bar(Adw.HeaderBar())
        toolbar_view.set_content(box)

        dialog = Adw.Dialog()
        dialog.set_title("Presets")
        dialog.set_content_width(560)
        dialog.set_content_height(560)
        dialog.set_child(toolbar_view)

        self.preset_browser_dialog = dialog

    def preset_row_setup(self, factory, list_item):

        box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)

        box.set_margin_start(6)
        box.set_margin_end(6)
        box.set_margin_top(4)
        box.set_margin_bottom(4)

        # max_width_chars keeps the label's requested (natural) size
        # small - without it, GTK sizes the row to fit the full preset
        # name (some are 100+ characters) regardless of ellipsize,
        # pushing the button off the edge of the dialog. hexpand still
        # lets it fill whatever width is actually available.
        label = Gtk.Label(xalign=0, hexpand=True)
        label.set_ellipsize(Pango.EllipsizeMode.END)
        label.set_max_width_chars(1)

        box.append(label)

        queue_button = Gtk.Button(icon_name="list-add-symbolic")

        queue_button.add_css_class("flat")
        queue_button.set_tooltip_text("Add to Queue")

        box.append(queue_button)

        list_item.set_child(box)

        # Stashed on the list_item itself (not the widgets) so bind
        # can find them again - GTK4 ListView recycles these rows for
        # different items as you scroll, so bind gets called many
        # times for the same row/button pair.
        list_item.preset_label = label
        list_item.queue_button = queue_button
        list_item.queue_button_handler = None

    def preset_row_bind(self, factory, list_item):

        string_object = list_item.get_item()
        name = string_object.get_string()

        list_item.preset_label.set_label(name)

        if list_item.queue_button_handler is not None:
            list_item.queue_button.disconnect(list_item.queue_button_handler)

        list_item.queue_button_handler = list_item.queue_button.connect(
            "clicked",
            lambda button: self.enqueue_preset(name)
        )

    def preset_row_activated(self, list_view, position):

        string_object = list_view.get_model().get_item(position)
        name = string_object.get_string()

        self.run_js(f"loadPresetByName({json.dumps(name)});")

        self.preset_browser_dialog.close()

    def enqueue_preset(self, name):

        self.run_js(f"enqueuePreset({json.dumps(name)});")
        self.show_toast(f"Added to queue: {name}")

    def show_queue_clicked(self, action, param):

        if self.queue_dialog is None:
            self.build_queue_dialog()

        self.queue_dialog.present(self)

    def build_queue_dialog(self):

        self.queue_list_store = Gtk.StringList.new(self.preset_queue)

        selection = Gtk.NoSelection.new(self.queue_list_store)

        factory = Gtk.SignalListItemFactory()

        factory.connect("setup", self.queue_row_setup)
        factory.connect("bind", self.queue_row_bind)

        list_view = Gtk.ListView.new(selection, factory)

        scrolled = Gtk.ScrolledWindow()
        scrolled.set_child(list_view)
        scrolled.set_vexpand(True)

        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        box.set_margin_start(12)
        box.set_margin_end(12)
        box.set_margin_top(12)
        box.set_margin_bottom(12)

        box.append(scrolled)

        toolbar_view = Adw.ToolbarView()
        toolbar_view.add_top_bar(Adw.HeaderBar())
        toolbar_view.set_content(box)

        dialog = Adw.Dialog()
        dialog.set_title("Queue")
        dialog.set_content_width(560)
        dialog.set_content_height(480)
        dialog.set_child(toolbar_view)

        self.queue_dialog = dialog

    def queue_row_setup(self, factory, list_item):

        box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=2)

        box.set_margin_start(6)
        box.set_margin_end(6)
        box.set_margin_top(4)
        box.set_margin_bottom(4)

        handle = Gtk.Image.new_from_icon_name("list-drag-handle-symbolic")
        handle.set_margin_end(4)
        handle.set_tooltip_text("Drag to Reorder")
        box.append(handle)

        # Same reasoning as preset_row_setup: without max_width_chars,
        # GTK sizes the row to fit the full (sometimes 100+ character)
        # preset name regardless of ellipsize, pushing the buttons off
        # the edge of the dialog.
        label = Gtk.Label(xalign=0, hexpand=True)
        label.set_ellipsize(Pango.EllipsizeMode.END)
        label.set_max_width_chars(1)

        box.append(label)

        up_button = Gtk.Button(icon_name="go-up-symbolic")
        up_button.add_css_class("flat")
        up_button.set_tooltip_text("Move Up")
        box.append(up_button)

        down_button = Gtk.Button(icon_name="go-down-symbolic")
        down_button.add_css_class("flat")
        down_button.set_tooltip_text("Move Down")
        box.append(down_button)

        remove_button = Gtk.Button(icon_name="user-trash-symbolic")
        remove_button.add_css_class("flat")
        remove_button.set_tooltip_text("Remove")
        box.append(remove_button)

        list_item.set_child(box)

        list_item.queue_label = label
        list_item.up_button = up_button
        list_item.down_button = down_button
        list_item.remove_button = remove_button
        list_item.queue_handlers = []

        # Drag-and-drop reordering. Both controllers read the row's
        # *current* position at the moment they actually fire (not a
        # value captured here at setup time), same reasoning as the
        # button handlers below - GTK4 ListView recycles this exact
        # row widget for different list positions as the list scrolls
        # or changes.
        drag_source = Gtk.DragSource()
        drag_source.set_actions(Gdk.DragAction.MOVE)

        drag_source.connect(
            "prepare",
            lambda source, x, y, li=list_item:
                Gdk.ContentProvider.new_for_value(
                    GObject.Value(int, li.get_position())
                )
        )

        box.add_controller(drag_source)

        drop_target = Gtk.DropTarget.new(GObject.TYPE_INT, Gdk.DragAction.MOVE)

        drop_target.connect(
            "drop",
            lambda target, from_position, x, y, li=list_item:
                self.reorder_queue_item(from_position, li.get_position())
        )

        box.add_controller(drop_target)

    def queue_row_bind(self, factory, list_item):

        string_object = list_item.get_item()

        list_item.queue_label.set_label(string_object.get_string())

        for button, handler in list_item.queue_handlers:
            button.disconnect(handler)

        # get_position() is read fresh inside each callback (rather
        # than captured here) since the row can be reused for a
        # different position without a fresh bind after nearby items
        # are removed/reordered.
        list_item.queue_handlers = [
            (
                list_item.up_button,
                list_item.up_button.connect(
                    "clicked",
                    lambda b, li=list_item: self.move_queue_item(li.get_position(), -1)
                )
            ),
            (
                list_item.down_button,
                list_item.down_button.connect(
                    "clicked",
                    lambda b, li=list_item: self.move_queue_item(li.get_position(), 1)
                )
            ),
            (
                list_item.remove_button,
                list_item.remove_button.connect(
                    "clicked",
                    lambda b, li=list_item: self.remove_queue_item(li.get_position())
                )
            ),
        ]

    def move_queue_item(self, position, delta):

        self.run_js(f"moveQueueItem({position}, {delta});")

    def reorder_queue_item(self, from_position, to_position):

        if from_position == to_position:
            return True

        self.run_js(f"moveQueueItemTo({from_position}, {to_position});")

        return True

    def remove_queue_item(self, position):

        self.run_js(f"removeQueueItem({position});")

    def load_preset_clicked(self, action, param):

        dialog = Gtk.FileDialog()
        dialog.set_title("Load Preset")

        preset_filter = Gtk.FileFilter()
        preset_filter.set_name("Presets (MilkDrop / Butterchurn)")
        preset_filter.add_pattern("*.milk")
        preset_filter.add_pattern("*.json")

        filters = Gio.ListStore.new(Gtk.FileFilter)
        filters.append(preset_filter)
        dialog.set_filters(filters)

        dialog.open(self, None, self.on_preset_file_chosen)

    def on_preset_file_chosen(self, dialog, result, user_data=None):

        try:
            gfile = dialog.open_finish(result)

        except GLib.Error as e:
            print("Load preset cancelled/failed:", e)
            return

        ok, contents, etag = gfile.load_contents(None)

        if not ok:
            print("Failed to read preset file:", gfile.get_path())
            return

        # Base64, not a plain string substitution - MilkDrop preset
        # text is full of quotes/backslashes/newlines that aren't safe
        # to embed directly in a JS string literal.
        encoded = base64.b64encode(contents).decode("ascii")
        name = gfile.get_basename()

        self.run_js(
            f"loadPresetFile({json.dumps(encoded)}, {json.dumps(name)});"
        )

    def next_preset(self, button):

        if self.preset_locked:
            self.show_toast("Preset is locked")
            return

        self.run_js("nextPreset();")

    def previous_preset(self, button):

        if self.preset_locked:
            self.show_toast("Preset is locked")
            return

        self.run_js("previousPreset();")

    def build_sensitivity_control(self):

        def format_sensitivity(value):
            return f"{value:.1f}x"

        return self.build_slider_row(
            "Sensitivity",
            0.0, 4.0, 0.1, 1.0,
            format_sensitivity,
            lambda value: self.run_js(f"setSensitivity({value});"),
            store_as="sensitivity_scale"
        )



    def audio_source_changed(self, action, value):

        source = value.get_string()

        print("Audio source (Python):", source)

        action.set_state(value)

        if source == "mic":
            self.pinned_sink = None
            self.stop_audio_capture()
            self.run_js("setAudioSource('mic')")
            return

        if source == "none":
            self.pinned_sink = None
            self.stop_audio_capture()
            self.run_js("setAudioSource('none')")
            return

        # Anything else is a specific sink's node name, picked from the
        # dynamically built device list - pin capture to that device
        # (see on_pactl_event for what happens if it later disappears).
        self.pinned_sink = source

        if self.current_sink != source:
            self.restart_audio_monitor(source)

        self.run_js("setAudioSource('system')")


    # System Audio Capture (Pipewire)

    def start_system_audio(self, sink_name=None):

        if sink_name is None:
            sink_name = self.get_default_sink()

        self.current_sink = sink_name

        print("Capturing monitor of sink:", sink_name)

        # target-object must be the sink's own node name, not its
        # ".monitor" name (pipewiresrc only matches real PipeWire node
        # names/serials, and the deprecated `path` property only takes
        # numeric ids). stream.capture.sink=true is what actually taps
        # the sink's monitor ports instead of opening a real capture
        # device - without it, WirePlumber may treat this as a
        # microphone request and, on Bluetooth sinks, drop the a2dp
        # connection to the lower quality headset profile.
        self.gst_pipeline = Gst.parse_launch(
            f"""
            pipewiresrc
            target-object="{sink_name}"
            stream-properties="props,stream.capture.sink=true"
            !
            audio/x-raw,format=S16LE,rate=44100,channels=2
            !
            audioconvert
            !
            audioresample
            !
            appsink name=sink emit-signals=true sync=false
            """
        )

        sink = self.gst_pipeline.get_by_name("sink")

        sink.connect(
            "new-sample",
            self.on_audio_sample
        )

        bus = self.gst_pipeline.get_bus()

        bus.add_signal_watch()

        bus.connect(
            "message",
            self.on_gst_message
        )

        self.gst_pipeline.set_state(
            Gst.State.PLAYING
        )


    def on_gst_message(self, bus, message):

        t = message.type

        if t == Gst.MessageType.ERROR:

            err, debug_info = message.parse_error()

            print("GST ERROR:", err, "|", debug_info)

        elif t == Gst.MessageType.WARNING:

            err, debug_info = message.parse_warning()

            print("GST WARNING:", err, "|", debug_info)


    def on_audio_sample(self, sink):

        sample = sink.emit("pull-sample")

        if sample:
            buffer = sample.get_buffer()

            ok, mapinfo = buffer.map(
                Gst.MapFlags.READ
            )

            if ok:
                data = bytes(mapinfo.data)
                buffer.unmap(mapinfo)

                GLib.idle_add(
                    self.send_audio_to_webview,
                    data
                )

        return Gst.FlowReturn.OK



    def send_audio_to_webview(self, data):

        if not self.webview_ready:
            return False

        encoded = base64.b64encode(
            data
        ).decode("ascii")


        self.webview.evaluate_javascript(
            f"receiveAudio('{encoded}')",
            -1,
            None,
            None,
            None,
            self.on_receive_audio_result,
            None
        )

        return False

    def on_receive_audio_result(self, webview, result, user_data):

        try:
            webview.evaluate_javascript_finish(result)

        except Exception as e:
            print("receiveAudio JS error:", e)



    def get_default_sink(self):

        return subprocess.check_output(
            ["pactl", "get-default-sink"]
        ).decode().strip()

    def list_sinks(self):

        raw = subprocess.check_output(
            ["pactl", "-f", "json", "list", "sinks"]
        )

        sinks = json.loads(raw)

        return [
            (sink["name"], sink["description"])
            for sink in sinks
        ]

    def rebuild_audio_source_menu(self):

        self.audio_source_submenu.remove_all()

        devices = Gio.Menu()

        for name, description in self.list_sinks():

            item = Gio.MenuItem.new(description, None)

            item.set_action_and_target_value(
                "win.audio-source",
                GLib.Variant("s", name)
            )

            devices.append_item(item)

        self.audio_source_submenu.append_section(None, devices)

        other = Gio.Menu()

        mic_item = Gio.MenuItem.new("Microphone", None)

        mic_item.set_action_and_target_value(
            "win.audio-source",
            GLib.Variant("s", "mic")
        )

        other.append_item(mic_item)

        none_item = Gio.MenuItem.new("None", None)

        none_item.set_action_and_target_value(
            "win.audio-source",
            GLib.Variant("s", "none")
        )

        other.append_item(none_item)

        self.audio_source_submenu.append_section(None, other)

    def watch_audio_changes(self):

        process = subprocess.Popen(
            ["pactl", "subscribe"],
            stdout=subprocess.PIPE,
            text=True
        )

        for line in process.stdout:

            # "on server" covers default sink/source changes; "on sink"
            # covers devices actually appearing/disappearing (e.g.
            # headphones being switched off), but disconnecting a
            # device fires a whole burst of these (card/sink/module
            # events) within milliseconds of each other. on_pactl_event
            # does several blocking `pactl`/subprocess calls, so running
            # it once per event in that burst was stalling the main
            # thread repeatedly right when a switch happens - hence
            # debouncing to a single run per burst.
            if "on server" in line or "on sink" in line:

                GLib.idle_add(
                    self.schedule_pactl_event
                )

    def schedule_pactl_event(self):

        if self.pactl_event_timer:

            GLib.source_remove(
                self.pactl_event_timer
            )

        self.pactl_event_timer = GLib.timeout_add(
            300,
            self.run_pactl_event
        )

        return False

    def run_pactl_event(self):

        self.pactl_event_timer = None

        self.on_pactl_event()

        return False

    def on_pactl_event(self):

        self.rebuild_audio_source_menu()

        if self.pinned_sink is None:
            return False

        known_sinks = {name for name, _ in self.list_sinks()}

        if self.pinned_sink not in known_sinks:

            # The device the user explicitly picked just disappeared
            # (e.g. headphones switched off/put away). Deliberately NOT
            # auto-restarting capture on some other device here: doing
            # that turned out to be unreliable (a restart triggered
            # from this background watcher - as opposed to one directly
            # triggered by a menu click - would intermittently come up
            # silent, for reasons that trace back to timing in
            # PipeWire's own driver reassignment after a device drops,
            # not anything in this app). Falling back to "None" instead
            # means the only pipeline (re)starts that ever happen are
            # in direct response to the user picking something, which
            # was solid in every test.
            print(
                "Pinned sink",
                self.pinned_sink,
                "disappeared, falling back to None"
            )

            self.lookup_action("audio-source").change_state(
                GLib.Variant("s", "none")
            )

        return False

    def stop_audio_capture(self):

        if self.gst_pipeline:

            self.gst_pipeline.set_state(
                Gst.State.NULL
            )

            self.gst_pipeline = None

        self.current_sink = None

    def restart_audio_monitor(self, sink_name=None):

        if self.gst_pipeline:

            self.gst_pipeline.set_state(
                Gst.State.NULL
            )

            # set_state() only requests the transition - it can finish
            # asynchronously. Without waiting for it here, the new
            # pipeline below gets built (and its pipewiresrc tries to
            # attach) while the old one is still tearing down its
            # PipeWire stream, and the new appsink silently never
            # receives a single sample.
            self.gst_pipeline.get_state(
                Gst.CLOCK_TIME_NONE
            )

        self.start_system_audio(sink_name)

        return False


    def run_js(self, script):

        GLib.idle_add(
            self.webview.evaluate_javascript,
            script,
            -1,
            None,
            None,
            None,
            None
        )
