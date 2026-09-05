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
import array
import math
from pathlib import Path

gi.require_version("Gst", "1.0")
gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
gi.require_version("Pango", "1.0")

from gi.repository import Gtk, Adw, GLib, Gdk, Gio, Gst, GObject, Pango
from melange.webview import create_webview
from melange.mirror_window import MirrorWindow
from melange.aux_window import AuxVisualizerWindow
from melange.now_playing import NowPlayingWatcher

# key -> (halign, valign) - "top-*"/"bottom-*" pick valign, the second
# word picks halign, same convention as the label text.
NOW_PLAYING_PLACEMENTS = {
    "top-left": (Gtk.Align.START, Gtk.Align.START),
    "top-center": (Gtk.Align.CENTER, Gtk.Align.START),
    "top-right": (Gtk.Align.END, Gtk.Align.START),
    "bottom-left": (Gtk.Align.START, Gtk.Align.END),
    "bottom-center": (Gtk.Align.CENTER, Gtk.Align.END),
    "bottom-right": (Gtk.Align.END, Gtk.Align.END),
}

# kind -> the win.show-* stateful action toggling that aux window,
# shared between the action setup in __init__ and aux_window_closed
# (which flips the action back to unchecked when the window's closed
# via its own close button rather than the menu item).
AUX_WINDOW_ACTIONS = {
    "vu": "show-vu-meter",
    "peak": "show-peak-meter",
    "xy": "show-xy-scope",
    "oscilloscope": "show-oscilloscope",
    "vectorscope": "show-vectorscope",
    "spectrum": "show-spectrum",
    "spectrogram": "show-spectrogram",
    "terrain": "show-terrain",
    "waterfall": "show-waterfall",
    "dvd": "show-dvd-bounce",
    "pipes": "show-pipes",
}

Gst.init(None)

@Gtk.Template(resource_path="/com/cameronlp/Melange/window.ui")
class MelangeWindow(Adw.ApplicationWindow):

    __gtype_name__ = "MelangeWindow"

    content_box = Gtk.Template.Child()
    toast_overlay = Gtk.Template.Child()
    toolbar_view = Gtk.Template.Child()
    headerbar = Gtk.Template.Child()
    menu_button = Gtk.Template.Child()
    fullscreen_button = Gtk.Template.Child()

    # Now Playing's Scale slider is a single multiplier applied to
    # both of these base values (see recompute_now_playing_scale) -
    # unchanged from the old independent Text Size/Text Box Width
    # defaults, so scale=1.0 looks identical to before this setting
    # existed. Album Art isn't listed here since its Auto sizing
    # already measures the real (now-scaled) text column height.
    NOW_PLAYING_BASE_TEXT_SIZE = 13.0
    NOW_PLAYING_BASE_WIDTH = 28
    NOW_PLAYING_MIN_SCALE = 0.5
    NOW_PLAYING_MAX_SCALE = 2.0
    # Matches window.ui's own default-width - Lock to Window Size
    # reads as "no bigger/smaller than usual" at the app's own default
    # window size.
    NOW_PLAYING_REFERENCE_WIDTH = 800

    def __init__(self, **kwargs):

        super().__init__(**kwargs)

        self.menu_open = False
        self.hide_timer = None
        self.last_mouse_pos = None
        self.mouse_over_toolbar = False
        self.last_scroll_time = 0.0
        self.transparency_mode_enabled = False
        self.immersive_mode_enabled = False
        self.transparency_opacity = 0.5
        self.transparency_fade_ms = 0.0
        self.opacity_fade_timer = None
        self.now_playing_enabled = False
        self.now_playing_placement = "bottom-left"
        self.now_playing_info = None
        self.now_playing_art_token = 0
        self.now_playing_source = None
        self.now_playing_players = {}
        self.now_playing_show_title = True
        self.now_playing_show_artist = True
        self.now_playing_show_album = True
        self.now_playing_show_artwork = True
        self.now_playing_show_time = False
        self.now_playing_show_background = True
        # now_playing_text_size/now_playing_width are always *derived*
        # (see recompute_now_playing_scale) from now_playing_scale, or
        # from the window's own width when now_playing_lock_to_window
        # is on - never set directly by their own slider anymore, one
        # shared Scale control replaced those alongside Album Art Size.
        self.now_playing_scale = 1.0
        self.now_playing_lock_to_window = False
        self.now_playing_text_size = self.NOW_PLAYING_BASE_TEXT_SIZE
        self.now_playing_font_desc = "Sans"
        self.now_playing_width = self.NOW_PLAYING_BASE_WIDTH
        self.now_playing_scroll_long_titles = False
        self.now_playing_scroll_timer = None
        self.now_playing_scroll_offset = 0
        self.now_playing_scroll_title = None
        self.now_playing_text_color = Gdk.RGBA()
        self.now_playing_text_color.parse("#ffffff")
        self.now_playing_position_timer = None
        # Off by default - only appears on a genuine change (title/
        # artist/play-pause), then fades back out, rather than staying
        # up the whole time Now Playing's enabled.
        self.now_playing_auto_hide = False
        self.now_playing_auto_hide_seconds = 5.0
        self.now_playing_auto_hide_timer = None
        self.now_playing_last_change_key = None
        # Independent of auto-hide - a continuous ambient breathing
        # effect (sine-wave opacity) rather than an event-triggered
        # one, so the two can be combined even though that's a fairly
        # unusual choice.
        self.now_playing_periodic_fade = False
        self.now_playing_periodic_fade_seconds = 4.0
        self.now_playing_periodic_timer = None
        self.now_playing_fade_timer = None
        self.toolbar_hide_delay = 3

        self.toolbar_view.set_extend_content_to_top_edge(True)
        self.headerbar.add_css_class("melange-header")

        # Webview

        self.webview = create_webview(
            on_message=self.on_webview_debug_message
        )

        # The preset-nav arrows used to be part of the page itself
        # (HTML buttons drawn on the canvas) - moved to real GTK
        # widgets, overlaid on top of the webview, so a mirror window
        # (mirror_window.py) - which shows only the webview widget's
        # own rendered content via Gtk.WidgetPaintable - doesn't also
        # show a pair of arrows that don't do anything there (a
        # Gtk.Picture never forwards input back to a paintable's
        # source, so they'd have been inert, confusing clutter).
        webview_overlay = Gtk.Overlay()
        webview_overlay.set_child(self.webview)

        self.prev_arrow_button = Gtk.Button(icon_name="go-previous-symbolic")
        self.prev_arrow_button.add_css_class("nav-arrow-button")
        self.prev_arrow_button.set_size_request(56, 56)
        self.prev_arrow_button.set_halign(Gtk.Align.START)
        self.prev_arrow_button.set_valign(Gtk.Align.CENTER)
        self.prev_arrow_button.set_margin_start(12)
        self.prev_arrow_button.set_tooltip_text("Previous preset")

        self.prev_arrow_button.connect(
            "clicked",
            lambda button: self.previous_preset(None)
        )

        webview_overlay.add_overlay(self.prev_arrow_button)

        self.next_arrow_button = Gtk.Button(icon_name="go-next-symbolic")
        self.next_arrow_button.add_css_class("nav-arrow-button")
        self.next_arrow_button.set_size_request(56, 56)
        self.next_arrow_button.set_halign(Gtk.Align.END)
        self.next_arrow_button.set_valign(Gtk.Align.CENTER)
        self.next_arrow_button.set_margin_end(12)
        self.next_arrow_button.set_tooltip_text("Next preset")

        self.next_arrow_button.connect(
            "clicked",
            lambda button: self.next_preset(None)
        )

        webview_overlay.add_overlay(self.next_arrow_button)

        # Favorite-toggle for whatever's currently showing - bottom
        # corner rather than top, so it doesn't compete for space with
        # the header bar when that's revealed.
        self.favorite_button = Gtk.Button(icon_name="non-starred-symbolic")
        self.favorite_button.add_css_class("nav-arrow-button")
        self.favorite_button.set_size_request(48, 48)
        self.favorite_button.set_halign(Gtk.Align.END)
        self.favorite_button.set_valign(Gtk.Align.END)
        self.favorite_button.set_margin_end(12)
        self.favorite_button.set_margin_bottom(12)
        self.favorite_button.set_tooltip_text("Add to Favorites")

        self.favorite_button.connect(
            "clicked",
            self.favorite_button_clicked
        )

        webview_overlay.add_overlay(self.favorite_button)

        # Now Playing (now_playing.py) - hidden until a track is
        # actually reported (see on_now_playing_changed), so there's
        # never an empty/placeholder card shown when nothing's playing
        # or the feature's off.
        self.now_playing_box = Gtk.Box(
            orientation=Gtk.Orientation.HORIZONTAL, spacing=8
        )
        self.now_playing_box.add_css_class("now-playing-card")
        self.now_playing_box.set_visible(False)

        self.now_playing_art = Gtk.Picture()
        self.now_playing_art.add_css_class("now-playing-art")
        self.now_playing_art.set_valign(Gtk.Align.FILL)
        self.now_playing_art.set_halign(Gtk.Align.FILL)
        self.now_playing_art.set_content_fit(Gtk.ContentFit.COVER)
        self.now_playing_art.set_can_shrink(True)

        # A real Gtk.Picture's *natural* size comes from the loaded
        # paintable's own intrinsic dimensions (confirmed: a real
        # 500x500 album art texture measured natural=500 even with
        # set_size_request(44, 44) on the picture itself) - size_
        # request only ever raises the *minimum*, it doesn't cap the
        # natural/preferred size from above, so a plain Picture with
        # CENTER alignment (tried first) still gets allocated close to
        # the full image size regardless of set_size_request, which
        # was the actual "album art still does not change size" bug -
        # apply_now_playing_art_size was changing a number nothing
        # downstream of it actually respected as a maximum.
        # Gtk.Overflow.HIDDEN on a plain wrapper doesn't fix this
        # either - confirmed it only clips *rendering*, the wrapper's
        # own measure() still propagates the child's oversized natural
        # size upward. A Gtk.ScrolledWindow with scrolling disabled
        # and propagate-natural-width/height off is the one thing
        # confirmed to actually cap both min *and* natural size to
        # exactly what's requested on the scrolled window itself,
        # regardless of the child's own preferred size - that's what
        # it exists for (a fixed viewport onto content that can be
        # bigger), just not usually used for a static image. The inner
        # Picture fills whatever the frame gives it (FILL, not CENTER)
        # and COVER-crops/scales the real image down to that.
        self.now_playing_art_frame = Gtk.ScrolledWindow()
        self.now_playing_art_frame.set_policy(
            Gtk.PolicyType.NEVER, Gtk.PolicyType.NEVER
        )
        self.now_playing_art_frame.set_propagate_natural_width(False)
        self.now_playing_art_frame.set_propagate_natural_height(False)
        self.now_playing_art_frame.set_valign(Gtk.Align.CENTER)
        self.now_playing_art_frame.set_halign(Gtk.Align.CENTER)
        self.now_playing_art_frame.set_child(self.now_playing_art)
        self.now_playing_art_frame.set_visible(False)

        self.now_playing_box.append(self.now_playing_art_frame)

        self.now_playing_text_box = Gtk.Box(
            orientation=Gtk.Orientation.VERTICAL, spacing=2
        )
        self.now_playing_text_box.set_valign(Gtk.Align.CENTER)

        self.now_playing_title_label = Gtk.Label(xalign=0.0)
        self.now_playing_title_label.add_css_class("now-playing-title")
        self.now_playing_title_label.set_ellipsize(Pango.EllipsizeMode.END)

        # Wrapped for the same reason Album Art needed a
        # Gtk.ScrolledWindow wrapper - Scroll Long Titles feeds the
        # label a different substring every tick, and even at a fixed
        # character count a Label's own *natural* width still varies
        # tick to tick (different glyphs render at different widths),
        # which a plain set_size_request() can't cap (only raises the
        # minimum, confirmed during the Album Art fix). This let the
        # whole card visibly resize every ~300ms while scrolling - see
        # apply_now_playing_title_frame_width/start_now_playing_title_
        # scroll for how the frame's width gets locked during a scroll
        # and released again once it stops.
        self.now_playing_title_frame = Gtk.ScrolledWindow()
        self.now_playing_title_frame.set_policy(
            Gtk.PolicyType.NEVER, Gtk.PolicyType.NEVER
        )
        self.now_playing_title_frame.set_propagate_natural_width(False)
        self.now_playing_title_frame.set_propagate_natural_height(False)
        self.now_playing_title_frame.set_halign(Gtk.Align.START)
        self.now_playing_title_frame.set_child(self.now_playing_title_label)
        self.now_playing_text_box.append(self.now_playing_title_frame)

        self.now_playing_artist_label = Gtk.Label(xalign=0.0)
        self.now_playing_artist_label.add_css_class("now-playing-artist")
        self.now_playing_artist_label.set_ellipsize(Pango.EllipsizeMode.END)
        self.now_playing_text_box.append(self.now_playing_artist_label)

        self.now_playing_album_label = Gtk.Label(xalign=0.0)
        self.now_playing_album_label.add_css_class("now-playing-artist")
        self.now_playing_album_label.set_ellipsize(Pango.EllipsizeMode.END)
        self.now_playing_album_label.set_visible(False)
        self.now_playing_text_box.append(self.now_playing_album_label)

        self.now_playing_time_label = Gtk.Label(xalign=0.0)
        self.now_playing_time_label.add_css_class("now-playing-artist")
        self.now_playing_time_label.set_visible(False)
        self.now_playing_text_box.append(self.now_playing_time_label)

        self.now_playing_box.append(self.now_playing_text_box)

        self.apply_now_playing_placement()
        self.apply_now_playing_text_style()
        self.apply_now_playing_width()
        self.apply_now_playing_art_size()

        webview_overlay.add_overlay(self.now_playing_box)

        # Always watching (event-driven, no polling loop) regardless
        # of whether Now Playing is currently enabled - only the
        # overlay's own visibility is gated on that (see
        # on_now_playing_changed/update_now_playing_visibility), so
        # toggling the setting on doesn't need to (re)establish the
        # D-Bus subscription from scratch.
        self.now_playing_watcher = NowPlayingWatcher(
            self.on_now_playing_changed,
            self.on_now_playing_players_changed
        )

        self.content_box.append(
            webview_overlay
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

        # Immersive Mode's only way back (see on_content_right_click) -
        # same CAPTURE-phase/content_box placement as the drag gesture
        # above and for the same reason (WebKit's own hit-testing
        # would otherwise swallow the press before a bubble-phase
        # gesture on an ancestor ever saw it). A distinct button
        # (SECONDARY vs. the drag gesture's PRIMARY) on the same
        # widget - GTK dispatches gestures by button/state independently,
        # so these don't fight each other.
        right_click_gesture = Gtk.GestureClick()

        right_click_gesture.set_button(Gdk.BUTTON_SECONDARY)
        right_click_gesture.set_propagation_phase(Gtk.PropagationPhase.CAPTURE)

        right_click_gesture.connect(
            "pressed",
            self.on_content_right_click
        )

        self.content_box.add_controller(right_click_gesture)

        self.gst_pipeline = None
        self.current_sink = None
        self.pinned_sink = None
        self.pactl_event_timer = None
        self.webview_ready = False
        self.preset_locked = False
        self.preset_names = []
        self.preset_list_store = None
        self.preset_browser_dialog = None
        self.browser_view_stack = None
        self.favorites = self.load_favorites()
        self.favorites_list_store = None
        # Session-scoped only, unlike favorites - a loaded preset's
        # actual content only ever lives in the webview's own runtime
        # state (JS's resolvedPresets Map), never written to disk, so
        # persisting just the *name* here across restarts would list
        # entries that fail to resolve/load the moment they're picked.
        self.user_loaded_presets = []
        self.user_preset_list_store = None
        self.pending_loaded_preset_name = None
        self.preset_queue = []
        self.queue_list_store = None
        self.playlists = self.load_playlists()
        self.playlists_dialog = None
        self.mirror_windows = []
        self.aux_windows = {}
        self.current_preset_name = None
        self.current_playlist_name = None

        # Never reused, even as mirrors close - so "Mirror 2" still
        # means the same window it always did rather than shifting
        # around as other mirrors come and go.
        self.next_mirror_number = 1

        self.connect(
            "close-request",
            self.on_close_request
        )

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

        # Same empty-in-window.ui, populated-here pattern as Audio
        # Source above, for the same reason: the list of open mirror
        # windows changes at runtime. Top-level section 3 (0=theme
        # selector, 1=Audio Source, 2=Presets/Lock Preset/Shuffle
        # Presets, 3=this one - Mirror Windows + Mini Visualizers,
        # split into its own section from the preset-behavior one
        # above it since they're a different kind of thing - see
        # window.ui) - item 0 within it (Mirror Windows submenu comes
        # before Mini Visualizers), then section 1 *within that
        # submenu* (New Mirror Window/Close All Mirrors are its own
        # static section 0 - see window.ui - so rebuilding this one
        # never touches those).
        # NOTE: this index is positional and brittle - it's broken
        # before (three times now: a Transparency Mode item, then
        # collapsing the old Load Preset item + Presets submenu into
        # one Browse Presets item, then splitting this section in two)
        # without updating this. Any future item added/removed before
        # Mirror Windows, or a new section inserted before this one,
        # needs this bumped again.
        section3 = self.menu_button.get_menu_model().get_item_link(
            3, Gio.MENU_LINK_SECTION
        )

        mirror_windows_submenu = section3.get_item_link(
            0, Gio.MENU_LINK_SUBMENU
        )

        self.open_mirrors_section = mirror_windows_submenu.get_item_link(
            1, Gio.MENU_LINK_SECTION
        )

        self.rebuild_mirror_windows_menu()

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

        # Keeps the button's icon honest regardless of how fullscreen
        # was entered/exited (the header button, F11, or Escape all
        # end up here rather than needing to be updated individually).
        self.connect(
            "notify::fullscreened",
            self.update_fullscreen_button_icon
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

        show_favorites_action = Gio.SimpleAction.new("show-favorites", None)

        show_favorites_action.connect(
            "activate",
            self.show_favorites_clicked
        )

        self.add_action(show_favorites_action)

        show_playlists_action = Gio.SimpleAction.new("show-playlists", None)

        show_playlists_action.connect(
            "activate",
            self.show_playlists_clicked
        )

        self.add_action(show_playlists_action)

        loop_queue_action = Gio.SimpleAction.new_stateful(
            "loop-queue",
            None,
            GLib.Variant("b", False)
        )

        loop_queue_action.connect(
            "change-state",
            self.loop_queue_changed
        )

        self.add_action(loop_queue_action)

        shuffle_queue_action = Gio.SimpleAction.new_stateful(
            "shuffle-queue",
            None,
            GLib.Variant("b", False)
        )

        shuffle_queue_action.connect(
            "change-state",
            self.shuffle_queue_changed
        )

        self.add_action(shuffle_queue_action)

        new_mirror_window_action = Gio.SimpleAction.new("new-mirror-window", None)

        new_mirror_window_action.connect(
            "activate",
            self.new_mirror_window_clicked
        )

        self.add_action(new_mirror_window_action)

        close_all_mirrors_action = Gio.SimpleAction.new("close-all-mirrors", None)

        close_all_mirrors_action.connect(
            "activate",
            self.close_all_mirrors_clicked
        )

        self.add_action(close_all_mirrors_action)

        toggle_favorite_action = Gio.SimpleAction.new("toggle-favorite", None)

        toggle_favorite_action.connect(
            "activate",
            self.toggle_favorite_action_activated
        )

        self.add_action(toggle_favorite_action)

        # One parameterized action rather than a separate action per
        # open mirror - avoids having to register/unregister actions
        # dynamically in step with the menu itself as mirrors come
        # and go, since the menu items built in
        # rebuild_mirror_windows_menu just target this one action with
        # a different integer each.
        present_mirror_action = Gio.SimpleAction.new(
            "present-mirror",
            GLib.VariantType.new("i")
        )

        present_mirror_action.connect(
            "activate",
            self.present_mirror_clicked
        )

        self.add_action(present_mirror_action)

        transparency_mode_action = Gio.SimpleAction.new_stateful(
            "transparency-mode",
            None,
            GLib.Variant("b", False)
        )

        transparency_mode_action.connect(
            "change-state",
            self.transparency_mode_changed
        )

        self.add_action(transparency_mode_action)

        # Unlike Transparency Mode, this needs a real menu entry (not
        # Preferences-only) - once it's on, the header bar (and
        # therefore the hamburger menu that would otherwise reach
        # Preferences) is gone, so the *only* way back has to be
        # reachable without the header - the right-click context menu
        # (on_content_right_click) - and the only way to turn it ON in
        # the first place is from the still-visible header before that
        # happens, i.e. a real menu item.
        immersive_mode_action = Gio.SimpleAction.new_stateful(
            "immersive-mode",
            None,
            GLib.Variant("b", False)
        )

        immersive_mode_action.connect(
            "change-state",
            self.immersive_mode_changed
        )

        self.add_action(immersive_mode_action)

        now_playing_enabled_action = Gio.SimpleAction.new_stateful(
            "now-playing-enabled",
            None,
            GLib.Variant("b", False)
        )

        now_playing_enabled_action.connect(
            "change-state",
            self.now_playing_enabled_changed
        )

        self.add_action(now_playing_enabled_action)

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

        # Experimental (see TODO.md) - VU Meter/X-Y Scope aux windows,
        # each a plain stateful boolean toggle (same pattern as
        # lock-preset/shuffle-preset above) rather than a "New Window"
        # action like Mirror Windows uses, since only one of each
        # makes sense open at a time.
        for kind, action_name in AUX_WINDOW_ACTIONS.items():

            aux_action = Gio.SimpleAction.new_stateful(
                action_name,
                None,
                GLib.Variant("b", False)
            )

            aux_action.connect(
                "change-state",
                lambda action, value, kind=kind: self.aux_window_toggled(action, value, kind)
            )

            self.add_action(aux_action)

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

    # Only exists for Now Playing's Lock to Window Size - there's no
    # notify::default-width/height to bind to for a *live* interactive
    # resize in GTK4 (those properties only reflect the initially
    # requested size), so this is the real, always-correct hook.
    # Cheap no-op when the toggle is off, which is the common case.
    def do_size_allocate(self, width, height, baseline):

        Adw.ApplicationWindow.do_size_allocate(self, width, height, baseline)

        if self.now_playing_lock_to_window:
            self.recompute_now_playing_scale()

    def on_webview_debug_message(self, text):

        if text.startswith("PRESET_NAME:"):
            preset_name = text[len("PRESET_NAME:"):]
            self.current_preset_name = preset_name
            self.set_title(self.build_window_title())
            self.update_mirror_titles()
            self.update_favorite_button_icon()
            return

        # A failed Load Preset (bad MilkDrop conversion, invalid
        # Butterchurn JSON, etc.) otherwise has no user-facing
        # feedback at all - it only ever reached the debug log, so a
        # rejected load just silently looked like nothing happened.
        if text.startswith("LOAD_PRESET_ERROR:"):
            reason = text[len("LOAD_PRESET_ERROR:"):]
            self.show_toast(f"Couldn't load preset: {reason}")
            self.pending_loaded_preset_name = None
            return

        # The actual preset names only exist in JS (from
        # butterchurn-presets, plus anything loaded via
        # win.load-preset) - this is Python's copy, used to build the
        # native preset browser list. Sent whenever the list changes.
        if text.startswith("PRESET_LIST:"):
            new_names = json.loads(text[len("PRESET_LIST:"):])

            # Confirms the load Python just asked for actually landed
            # in JS's own list, rather than trusting on_preset_file_
            # chosen's dispatch alone - that call site has no way to
            # know yet whether the parse/conversion on the other side
            # is going to succeed. A name only ever reaches here (a
            # PRESET_LIST: re-announcement) via that one success path
            # in loadPresetFile, so genuinely new + previously pending
            # is enough to confirm it rather than guess.
            if (
                self.pending_loaded_preset_name
                and self.pending_loaded_preset_name not in self.preset_names
                and self.pending_loaded_preset_name in new_names
                and self.pending_loaded_preset_name not in self.user_loaded_presets
            ):
                self.user_loaded_presets.append(self.pending_loaded_preset_name)

                if self.user_preset_list_store is not None:
                    self.user_preset_list_store.splice(
                        0,
                        self.user_preset_list_store.get_n_items(),
                        self.user_loaded_presets
                    )

            self.pending_loaded_preset_name = None
            self.preset_names = new_names

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

        # Sent by the cycle timer (main.js scheduleCycleTick) instead
        # of calling nextPreset() directly, so the lock check (and the
        # native toast it shows) lives in one place regardless of
        # whether a change was requested via auto-cycling or the
        # win.next-preset keyboard shortcut/GTK nav arrow.
        if text == "NAV_NEXT":
            self.next_preset(None)
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
        self.set_nav_arrows_visible(True)
        self.set_cursor(None)



    def toolbar_leave(self, controller):

        print(f"[{time.monotonic():.3f}] toolbar_leave")

        self.mouse_over_toolbar = False

        self.schedule_toolbar_hide()


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

        self.reveal_toolbar()


    def reveal_toolbar(self):

        # Immersive Mode overrides the normal auto-hide/reveal cycle
        # entirely - it should stay hidden regardless of mouse
        # movement or toolbar hover, unlike ordinary auto-hide (which
        # this same method also drives). This one guard is enough:
        # mouse_move/toolbar_enter/toolbar_leave all funnel through
        # here or schedule_toolbar_hide, neither of which does
        # anything while immersive_mode_enabled is set.
        if self.immersive_mode_enabled:
            return

        self.toolbar_view.set_reveal_top_bars(True)
        self.set_nav_arrows_visible(True)

        self.schedule_toolbar_hide()

    # Shared by reveal_toolbar and toolbar_leave - also fixes a latent
    # bug toolbar_leave used to have on its own (assigned a new
    # hide_timer without cancelling whatever it was already holding,
    # e.g. if the mouse briefly re-entered and left the toolbar before
    # the first timer fired - now always cancelled first, same as
    # everywhere else in this file that reschedules a GLib timer).
    # toolbar_hide_delay <= 0 ("Never") means don't schedule at all -
    # reveal_toolbar's own set_reveal_top_bars(True) above is then the
    # only thing keeping it visible, which is exactly "stays visible".
    def schedule_toolbar_hide(self):

        if self.hide_timer:
            GLib.source_remove(self.hide_timer)
            self.hide_timer = None

        if self.toolbar_hide_delay <= 0:
            return

        self.hide_timer = GLib.timeout_add_seconds(
            int(self.toolbar_hide_delay),
            self.hide_toolbar
        )

    def immersive_mode_changed(self, action, value):

        action.set_state(value)

        self.immersive_mode_enabled = value.get_boolean()

        if self.immersive_mode_enabled:

            if self.hide_timer:
                GLib.source_remove(self.hide_timer)
                self.hide_timer = None

            self.toolbar_view.set_reveal_top_bars(False)
            self.set_nav_arrows_visible(False)

        else:
            # reveal_toolbar's own immersive_mode_enabled guard is why
            # the flag above has to be cleared first - otherwise this
            # would immediately no-op.
            self.reveal_toolbar()

        self.show_toast(
            "Immersive Mode enabled - right-click to exit"
            if self.immersive_mode_enabled
            else "Immersive Mode disabled"
        )

    # The only way out of Immersive Mode once it's on (see
    # immersive_mode_changed's own comment on the action registration)
    # - does nothing while the mode is off, so this gesture has no
    # effect on ordinary right-clicks.
    def on_content_right_click(self, gesture, n_press, x, y):

        if not self.immersive_mode_enabled:
            return

        menu = Gio.Menu()
        menu.append("Exit Immersive Mode", "win.immersive-mode")

        popover = Gtk.PopoverMenu.new_from_model(menu)
        popover.set_parent(self.content_box)
        popover.set_has_arrow(False)

        # Gdk.Rectangle(x=..., y=..., ...) silently ignores every
        # constructor keyword (a PyGObject boxed-type limitation - it
        # even warns "arguments passed will be ignored" if you look for
        # it), so that always built a (0, 0, 0, 0) rectangle regardless
        # of the real click position - the popover was always pointed
        # at content_box's own top-left corner instead of the cursor.
        # Fields have to be set individually after construction instead.
        point = Gdk.Rectangle()
        point.x = int(x)
        point.y = int(y)
        point.width = 1
        point.height = 1

        popover.set_pointing_to(point)
        popover.popup()

    OPACITY_FADE_TICK_MS = 16

    # Applied to toast_overlay (the ToolbarView's "content", i.e.
    # everything below the header bar) rather than self (the whole
    # window) - deliberately excludes the header bar so it - and its
    # hamburger menu, the only way to turn this back off - always
    # stays fully visible/reachable, regardless of the configured
    # opacity level. Instant by default (transparency_fade_ms == 0) -
    # only steps through a GLib timer, same repeating-timeout shape as
    # the DVD/Pipes tick timers, when a duration is actually set.
    def animate_opacity(self, target):

        if self.opacity_fade_timer:
            GLib.source_remove(self.opacity_fade_timer)
            self.opacity_fade_timer = None

        duration_ms = self.transparency_fade_ms

        if duration_ms <= 0:
            self.toast_overlay.set_opacity(target)
            return

        start = self.toast_overlay.get_opacity()
        start_time = time.monotonic()

        def step():

            elapsed_ms = (time.monotonic() - start_time) * 1000
            t = min(1.0, elapsed_ms / duration_ms)

            self.toast_overlay.set_opacity(start + (target - start) * t)

            if t >= 1.0:
                self.opacity_fade_timer = None
                return False

            return True

        self.opacity_fade_timer = GLib.timeout_add(
            self.OPACITY_FADE_TICK_MS, step
        )

    # No longer hover-triggered - a flat, always-on transparency level
    # for the content area while the mode is on, changed on request
    # ("general transparency mode... even when the mouse is there
    # there is no fading effect"). Fades to the configured opacity on
    # enable and back to fully opaque on disable, both via the same
    # Fade Speed setting, rather than one of the two being an instant
    # special case - there's no more mouse-position edge case to guard
    # against now that on/off is the only transition.
    def transparency_mode_changed(self, action, value):

        action.set_state(value)

        self.transparency_mode_enabled = value.get_boolean()

        # See .transparency-active in style.css - without this, fading
        # toast_overlay's opacity just blends toward the window's own
        # opaque background instead of the real desktop behind it.
        if self.transparency_mode_enabled:
            self.add_css_class("transparency-active")
        else:
            self.remove_css_class("transparency-active")

        self.animate_opacity(
            self.transparency_opacity
            if self.transparency_mode_enabled
            else 1.0
        )

        self.show_toast(
            "Transparency Mode enabled"
            if self.transparency_mode_enabled
            else "Transparency Mode disabled"
        )

    # Runs on every NowPlayingWatcher report, regardless of whether
    # the feature is currently enabled - keeping self.now_playing_info
    # up to date unconditionally means flipping Enabled back on
    # doesn't need to wait for the next track change to show anything.
    # Shown for both Playing and Paused (not just Playing) - requested,
    # since pausing to e.g. answer the door shouldn't make the overlay
    # vanish and reappear.
    def on_now_playing_changed(self, info):

        self.now_playing_info = info
        self.update_now_playing_visibility()

        if info is None:
            self.stop_now_playing_position_timer()
            self.stop_now_playing_title_scroll()
            return

        self.update_now_playing_title(info["title"])
        self.now_playing_artist_label.set_label(info["artist"])
        self.now_playing_album_label.set_label(info["album"])

        self.apply_now_playing_field_visibility()
        self.load_now_playing_art(info["art_url"])
        self.restart_now_playing_position_timer()

        if self.now_playing_auto_hide:

            change_key = (info["title"], info["artist"], info["status"])

            if change_key != self.now_playing_last_change_key:
                self.now_playing_last_change_key = change_key
                self.show_now_playing_transient()

    # {bus_name: display name} for every currently known MPRIS player -
    # feeds the Source Adw.ComboRow (built once the dialog opens, kept
    # in sync afterwards by rebuilding its model here on every change)
    # rather than it being a fixed snapshot from whenever Preferences
    # happened to first be built.
    def on_now_playing_players_changed(self, identities):

        self.now_playing_players = identities

        if hasattr(self, "now_playing_source_row"):
            self.rebuild_now_playing_source_model()

    def update_now_playing_visibility(self):

        self.now_playing_box.set_visible(
            self.now_playing_enabled and self.now_playing_info is not None
        )

    # One label/widget per optional field, each independently toggled
    # by its own Preferences switch and (title/artist/art) also hidden
    # if the current track simply doesn't have that data - "Show
    # Artist" being on doesn't mean showing an empty line when a
    # track has no artist tag.
    def apply_now_playing_field_visibility(self):

        info = self.now_playing_info

        if info is None:
            return

        self.now_playing_title_label.set_visible(
            self.now_playing_show_title and bool(info["title"])
        )

        self.now_playing_artist_label.set_visible(
            self.now_playing_show_artist and bool(info["artist"])
        )

        self.now_playing_album_label.set_visible(
            self.now_playing_show_album and bool(info["album"])
        )

        self.apply_now_playing_art_size()

    def now_playing_show_album_changed(self, enabled):

        self.now_playing_show_album = enabled
        self.apply_now_playing_field_visibility()

    def now_playing_show_title_changed(self, enabled):

        self.now_playing_show_title = enabled
        self.apply_now_playing_field_visibility()

    def now_playing_show_artist_changed(self, enabled):

        self.now_playing_show_artist = enabled
        self.apply_now_playing_field_visibility()

    def now_playing_show_artwork_changed(self, enabled):

        self.now_playing_show_artwork = enabled

        if self.now_playing_info is not None:
            self.load_now_playing_art(self.now_playing_info["art_url"])

    def now_playing_show_background_changed(self, enabled):

        self.now_playing_show_background = enabled

        if enabled:
            self.now_playing_box.add_css_class("now-playing-card")
        else:
            self.now_playing_box.remove_css_class("now-playing-card")

    def now_playing_show_time_changed(self, enabled):

        self.now_playing_show_time = enabled
        self.restart_now_playing_position_timer()

    # Text Size and Text Box Width used to be two independent sliders
    # (plus a third for Album Art Size) - consolidated into one Scale
    # multiplier on request, to cut down the sheer number of Now
    # Playing settings. now_playing_text_size/now_playing_width stay
    # as real attributes (read all over: apply_now_playing_text_style,
    # apply_now_playing_width, the scroll-marquee logic) - this is now
    # the only place that ever assigns them.
    def recompute_now_playing_scale(self):

        if self.now_playing_lock_to_window:
            width = self.get_width() or self.NOW_PLAYING_REFERENCE_WIDTH
            scale = width / self.NOW_PLAYING_REFERENCE_WIDTH
            scale = max(
                self.NOW_PLAYING_MIN_SCALE,
                min(self.NOW_PLAYING_MAX_SCALE, scale)
            )
        else:
            scale = self.now_playing_scale

        self.now_playing_text_size = self.NOW_PLAYING_BASE_TEXT_SIZE * scale
        self.now_playing_width = max(
            10, round(self.NOW_PLAYING_BASE_WIDTH * scale)
        )

        self.apply_now_playing_text_style()
        self.apply_now_playing_width()

        # The width/size change may have pushed the current title
        # across the "too long to fit" threshold either way -
        # re-evaluate rather than leaving a scroll running (or not
        # running) against a now-stale size.
        if self.now_playing_info is not None:
            self.update_now_playing_title(self.now_playing_info["title"])

    def now_playing_scale_changed(self, value):

        self.now_playing_scale = value
        self.recompute_now_playing_scale()

    def now_playing_lock_to_window_changed(self, active):

        self.now_playing_lock_to_window = active

        if hasattr(self, "now_playing_scale_row"):
            self.now_playing_scale_row.set_visible(not active)

        self.recompute_now_playing_scale()

    def now_playing_text_color_changed(self, rgba):

        self.now_playing_text_color = rgba
        self.apply_now_playing_text_style()

    def now_playing_font_changed(self, font_desc_string):

        self.now_playing_font_desc = font_desc_string
        self.apply_now_playing_text_style()

    # max-width-chars, not a literal pixel width - matches how this
    # card was already sized (set_ellipsize + set_max_width_chars),
    # and scales naturally with Text Size/Font rather than fighting
    # them the way a fixed pixel width would.
    def apply_now_playing_width(self):

        for label in (
            self.now_playing_artist_label,
            self.now_playing_album_label,
            self.now_playing_time_label
        ):
            label.set_max_width_chars(self.now_playing_width)

        # The title label is skipped here while it's actively
        # scrolling (part of the "scroll long titles does not work
        # after... box width are changed" bug) - start_now_playing_
        # title_scroll deliberately overrides it to unlimited/no-
        # ellipsize, and unconditionally resetting it here every time
        # the width changes clobbered that override without ever
        # re-applying it, since update_now_playing_title only restarts
        # the scroll for an actually-*different* title (a width change
        # alone doesn't change what's playing). now_playing_width_
        # changed still re-evaluates scrolling itself right after
        # calling this, which is what actually needs to react to the
        # new width (the visible window size is read fresh every tick).
        if self.now_playing_scroll_title is None:
            self.now_playing_title_label.set_max_width_chars(self.now_playing_width)

        self.apply_now_playing_title_frame_width()

    # Locks the title row's own frame width so the Scroll Long Titles
    # marquee doesn't visibly resize the whole card every tick - a
    # no-op while actively scrolling, since start_now_playing_title_
    # scroll/stop_now_playing_title_scroll own the frame's width for
    # that duration instead (see the comment on now_playing_title_
    # frame's construction for why a wrapper is needed at all here).
    def apply_now_playing_title_frame_width(self):

        if self.now_playing_scroll_title is not None:
            return

        _, natural, _, _ = self.now_playing_title_label.measure(
            Gtk.Orientation.HORIZONTAL, -1
        )
        self.now_playing_title_frame.set_size_request(natural, -1)

    # Requested ("it should be limited by the box size... in general
    # it should match the height of the lines of now playing text") -
    # measures now_playing_text_box's own actual natural height (title
    # + whichever of artist/album/time are currently visible, at the
    # current Text Size/Font) via Gtk.Widget.measure() rather than
    # computing it from font metrics by hand, so it stays correct
    # across every combination of Show Title/Artist/Time and Scale
    # without this needing to know anything about how tall a line of
    # text actually renders. No manual override anymore (that used to
    # be a separate Album Art Size slider, removed when Text Size/Text
    # Box Width/Album Art Size were consolidated into one Scale
    # control) - Auto sizing already tracks Scale for free since it
    # measures the real, now-scaled text column.
    def apply_now_playing_art_size(self):

        _, natural, _, _ = self.now_playing_text_box.measure(
            Gtk.Orientation.VERTICAL, -1
        )
        size = max(24, natural)

        self.now_playing_art_frame.set_size_request(size, size)

    def now_playing_scroll_long_titles_changed(self, enabled):

        self.now_playing_scroll_long_titles = enabled

        if self.now_playing_info is not None:
            self.update_now_playing_title(self.now_playing_info["title"])

    # Only (re)starts the scroll if the title actually changed from
    # whatever's currently scrolling - on_now_playing_changed can fire
    # repeatedly for the same title (a chatty player resending
    # unrelated property changes), and restarting the animation from
    # scratch every time would make it visibly stutter/reset instead
    # of scrolling smoothly.
    def update_now_playing_title(self, title):

        title = title or "Unknown Title"

        if self.now_playing_scroll_long_titles and len(title) > self.now_playing_width:

            if title != self.now_playing_scroll_title:
                self.start_now_playing_title_scroll(title)

        else:
            self.stop_now_playing_title_scroll()
            self.now_playing_title_label.set_label(title)
            self.apply_now_playing_title_frame_width()

    NOW_PLAYING_SCROLL_TICK_MS = 300
    NOW_PLAYING_SCROLL_SEPARATOR = "   •   "

    # A text-based marquee (rotating which substring is shown) rather
    # than actually animating pixel position - reuses Text Box Width's
    # existing character-count sizing as the visible window directly,
    # instead of needing real Pango/pixel measurement of the label's
    # rendered width just to know how far there is to scroll. A
    # reasonable approximation given this card's sizing is already
    # character-count-based everywhere else (proportional fonts mean
    # a fixed character count isn't a perfectly constant pixel width,
    # but close enough for a scrolling ticker).
    def start_now_playing_title_scroll(self, title):

        self.stop_now_playing_title_scroll()

        self.now_playing_scroll_title = title
        self.now_playing_scroll_offset = 0

        # max-width-chars/ellipsize are for the *static* display case
        # only - while actively scrolling, each tick already feeds the
        # label exactly the number of characters meant to be shown, so
        # GTK's own width-based truncation just fights that instead of
        # helping: confirmed (bug report - "scroll long titles does
        # not work after the font size or box width are changed") that
        # the underlying rotation keeps advancing correctly regardless
        # of font size, but at a large enough font the character-count
        # window's real pixel width can exceed what's actually
        # available, and ellipsize then clips the already-correctly-
        # rotating text down to a near-static truncated string - looks
        # completely broken even though the content underneath is
        # still cycling every tick. Restored to the normal static
        # constraints in stop_now_playing_title_scroll.
        self.now_playing_title_label.set_max_width_chars(-1)
        self.now_playing_title_label.set_ellipsize(Pango.EllipsizeMode.NONE)

        scroll_text = title + self.NOW_PLAYING_SCROLL_SEPARATOR
        doubled = scroll_text + scroll_text

        def tick():

            n = len(scroll_text)
            offset = self.now_playing_scroll_offset % n

            self.now_playing_title_label.set_label(
                doubled[offset:offset + self.now_playing_width]
            )

            self.now_playing_scroll_offset += 1

            return True

        tick()

        # Lock the frame to this one reference width for the whole
        # scroll run - a same-length substring's *natural* width still
        # varies tick to tick (different glyphs), which used to make
        # the whole card visibly resize every ~300ms. Any given tick's
        # content that renders wider than this reference gets clipped
        # by the frame instead (a ScrolledWindow with both scrollbar
        # policies NEVER just crops, no scrollbar ever appears) -
        # reads as a clean, stable-width ticker rather than jitter.
        _, natural, _, _ = self.now_playing_title_label.measure(
            Gtk.Orientation.HORIZONTAL, -1
        )
        self.now_playing_title_frame.set_size_request(natural, -1)

        self.now_playing_scroll_timer = GLib.timeout_add(
            self.NOW_PLAYING_SCROLL_TICK_MS, tick
        )

    def stop_now_playing_title_scroll(self):

        if self.now_playing_scroll_timer:
            GLib.source_remove(self.now_playing_scroll_timer)
            self.now_playing_scroll_timer = None

        if self.now_playing_scroll_title is not None:
            self.now_playing_title_label.set_max_width_chars(self.now_playing_width)
            self.now_playing_title_label.set_ellipsize(Pango.EllipsizeMode.END)
            self.now_playing_scroll_title = None
            self.apply_now_playing_title_frame_width()
        else:
            self.now_playing_scroll_title = None

    NOW_PLAYING_FADE_TICK_MS = 16

    # Shared by both auto-hide (fading now_playing_box out after a
    # delay) and, indirectly, periodic fade - a single opacity-
    # animation helper for this widget, same shape as window.py's own
    # animate_opacity (which targets toast_overlay for Transparency
    # Mode, a different widget/purpose entirely).
    def animate_now_playing_box_opacity(self, target, duration_ms):

        if self.now_playing_fade_timer:
            GLib.source_remove(self.now_playing_fade_timer)
            self.now_playing_fade_timer = None

        if duration_ms <= 0:
            self.now_playing_box.set_opacity(target)
            return

        start = self.now_playing_box.get_opacity()
        start_time = time.monotonic()

        def step():

            elapsed_ms = (time.monotonic() - start_time) * 1000
            t = min(1.0, elapsed_ms / duration_ms)

            self.now_playing_box.set_opacity(start + (target - start) * t)

            if t >= 1.0:
                self.now_playing_fade_timer = None
                return False

            return True

        self.now_playing_fade_timer = GLib.timeout_add(
            self.NOW_PLAYING_FADE_TICK_MS, step
        )

    # Called whenever on_now_playing_changed sees a genuine change
    # (title/artist/play-pause) while Auto-Hide is on - snaps back to
    # fully visible immediately (a track changing is exactly the
    # moment you want to see it, not fade into view) and (re)starts
    # the countdown to fade back out.
    def show_now_playing_transient(self):

        if self.now_playing_auto_hide_timer:
            GLib.source_remove(self.now_playing_auto_hide_timer)
            self.now_playing_auto_hide_timer = None

        self.animate_now_playing_box_opacity(1.0, 0)

        if self.now_playing_auto_hide_seconds > 0:
            self.now_playing_auto_hide_timer = GLib.timeout_add(
                int(self.now_playing_auto_hide_seconds * 1000),
                self.hide_now_playing_transient
            )

    def hide_now_playing_transient(self):

        self.now_playing_auto_hide_timer = None
        self.animate_now_playing_box_opacity(0.0, 500)

        return False

    def now_playing_auto_hide_changed(self, enabled):

        self.now_playing_auto_hide = enabled

        if enabled:
            # Treat whatever's already showing (if anything) as a
            # fresh change, so turning this on doesn't require an
            # actual track change before it does anything.
            self.now_playing_last_change_key = None
            if self.now_playing_info is not None:
                self.show_now_playing_transient()
        else:
            if self.now_playing_auto_hide_timer:
                GLib.source_remove(self.now_playing_auto_hide_timer)
                self.now_playing_auto_hide_timer = None
            self.animate_now_playing_box_opacity(1.0, 0)

    def now_playing_auto_hide_seconds_changed(self, value):

        self.now_playing_auto_hide_seconds = value

    NOW_PLAYING_PERIODIC_TICK_MS = 33

    # A continuous sine-wave opacity cycle rather than a discrete
    # show/hold/hide state machine - one formula covers the whole
    # "periodically fades in and out" cycle with no extra state to
    # track, and reads as a smooth breathing effect rather than a
    # sudden blink.
    def restart_now_playing_periodic_fade(self):

        if self.now_playing_periodic_timer:
            GLib.source_remove(self.now_playing_periodic_timer)
            self.now_playing_periodic_timer = None

        if not self.now_playing_periodic_fade:
            self.now_playing_box.set_opacity(1.0)
            return

        start_time = time.monotonic()

        def tick():

            elapsed = time.monotonic() - start_time
            period = max(0.5, self.now_playing_periodic_fade_seconds)
            phase = (elapsed % period) / period

            opacity = 0.5 - 0.5 * math.cos(2 * math.pi * phase)

            self.now_playing_box.set_opacity(opacity)

            return True

        self.now_playing_periodic_timer = GLib.timeout_add(
            self.NOW_PLAYING_PERIODIC_TICK_MS, tick
        )

    def now_playing_periodic_fade_changed(self, enabled):

        self.now_playing_periodic_fade = enabled
        self.restart_now_playing_periodic_fade()

    def now_playing_periodic_fade_seconds_changed(self, value):

        self.now_playing_periodic_fade_seconds = value

    # Title/artist/time all share one size+color+font (family, plus
    # bold/italic - see build_now_playing_font_control's FACE level)
    # setting rather than three independent sets - set via Pango
    # attributes directly rather than injecting per-instance CSS,
    # since GtkLabel already exposes exactly this as a first-class,
    # simpler API for "this label, this text run, these attributes".
    # font_desc (family + weight/style, no size - Text Size is this
    # app's own separate control) and the pixel size are combined into
    # one Pango.FontDescription/AttrFontDesc rather than kept as
    # separate Family/Size attributes, since that's the only way to
    # also carry bold/italic (there's no standalone "AttrBold"/
    # "AttrItalic" the way there is for size/family/foreground).
    def apply_now_playing_text_style(self):

        attrs = Pango.AttrList()

        font_desc = Pango.FontDescription.from_string(self.now_playing_font_desc)
        font_desc.set_size(int(self.now_playing_text_size * Pango.SCALE))

        attrs.insert(Pango.attr_font_desc_new(font_desc))

        color = self.now_playing_text_color

        attrs.insert(Pango.attr_foreground_new(
            int(color.red * 65535),
            int(color.green * 65535),
            int(color.blue * 65535)
        ))

        # Foreground color and its alpha are two separate Pango
        # attribute types - the Text Color picker already lets
        # choosing a translucent color (GTK4's color dialog has an
        # alpha slider by default), but picking one had zero visible
        # effect until this was added, since alpha was never read at
        # all before.
        attrs.insert(Pango.attr_foreground_alpha_new(
            int(color.alpha * 65535)
        ))

        for label in (
            self.now_playing_title_label,
            self.now_playing_artist_label,
            self.now_playing_album_label,
            self.now_playing_time_label
        ):
            label.set_attributes(attrs)

        self.apply_now_playing_art_size()
        self.apply_now_playing_title_frame_width()

    NOW_PLAYING_POSITION_TICK_MS = 1000

    def restart_now_playing_position_timer(self):

        self.stop_now_playing_position_timer()

        info = self.now_playing_info

        if not (self.now_playing_show_time and info and info["length_us"]):
            self.now_playing_time_label.set_visible(False)
            return

        self.update_now_playing_position()

        self.now_playing_position_timer = GLib.timeout_add(
            self.NOW_PLAYING_POSITION_TICK_MS,
            self.update_now_playing_position
        )

    def stop_now_playing_position_timer(self):

        if self.now_playing_position_timer:
            GLib.source_remove(self.now_playing_position_timer)
            self.now_playing_position_timer = None

    # A fresh Properties.Get every tick (rather than interpolating
    # locally between rarer fetches) - simpler, and this is a once-a-
    # second local D-Bus round trip, cheap enough not to bother
    # optimizing away.
    def update_now_playing_position(self):

        info = self.now_playing_info

        if info is None:
            self.now_playing_time_label.set_visible(False)
            self.apply_now_playing_art_size()
            return False

        position_us = self.now_playing_watcher.get_position_us(info["bus_name"])

        if position_us is None:
            self.now_playing_time_label.set_visible(False)
            self.apply_now_playing_art_size()
            return False

        self.now_playing_time_label.set_label(
            f"{self.format_now_playing_time(position_us)} / "
            f"{self.format_now_playing_time(info['length_us'])}"
        )
        self.now_playing_time_label.set_visible(True)
        self.apply_now_playing_art_size()

        return True

    def format_now_playing_time(self, microseconds):

        total_seconds = max(0, int(microseconds // 1_000_000))
        minutes, seconds = divmod(total_seconds, 60)

        return f"{minutes}:{seconds:02d}"

    # Guards against a slow/late art fetch for a track that's since
    # been skipped past clobbering whatever's already showing - each
    # call gets a fresh token, and the async callback only applies its
    # result if it's still the most recent one requested.
    def load_now_playing_art(self, art_url):

        self.now_playing_art_token += 1
        token = self.now_playing_art_token

        if not art_url or not self.now_playing_show_artwork:
            self.now_playing_art_frame.set_visible(False)
            return

        def on_loaded(source, result):

            if token != self.now_playing_art_token:
                return

            if not self.now_playing_show_artwork:
                return

            try:
                ok, contents, etag = source.load_contents_finish(result)
            except GLib.Error:
                self.now_playing_art_frame.set_visible(False)
                return

            try:
                texture = Gdk.Texture.new_from_bytes(GLib.Bytes.new(contents))
            except GLib.Error:
                self.now_playing_art_frame.set_visible(False)
                return

            self.now_playing_art.set_paintable(texture)
            self.now_playing_art_frame.set_visible(True)

        # Gio.File.load_contents_async transparently handles both
        # file:// (the common case - most players cache art locally)
        # and http(s):// (network share is already granted) through
        # the same GVfs-backed API, so no separate download path is
        # needed for either.
        Gio.File.new_for_uri(art_url).load_contents_async(None, on_loaded)

    # A real win.now-playing-enabled action (not just a Preferences
    # toggle) now, matching Transparency Mode/Immersive Mode - a
    # hamburger menu item alongside them.
    def now_playing_enabled_changed(self, action, value):

        action.set_state(value)

        self.now_playing_enabled = value.get_boolean()
        self.update_now_playing_visibility()

    # Was previously given an extra-large top margin (56px vs the
    # normal 12) specifically to avoid sitting under the header bar -
    # removed, since the header bar already floats over this same
    # content area (extend_content_to_top_edge) and fades away on its
    # own; reserving space against it defeated the point of a floating
    # header and just left dead space at the top instead ("many of the
    # apps leave space for the top bar... can this be fixed, since the
    # top bar fades away and will not block the view" - reported
    # directly about this). Top and bottom now use the same plain 12px
    # margin, symmetric with each other.
    def apply_now_playing_placement(self):

        halign, valign = NOW_PLAYING_PLACEMENTS[self.now_playing_placement]

        self.now_playing_box.set_halign(halign)
        self.now_playing_box.set_valign(valign)

        is_top = valign == Gtk.Align.START

        self.now_playing_box.set_margin_top(12 if is_top else 0)
        self.now_playing_box.set_margin_bottom(0 if is_top else 12)

        # Always both, regardless of halign - requested ("when it
        # reaches the right side, there should be the same gap as
        # there is on the left"): a wide enough title (especially with
        # Text Box Width turned up) can make the card's natural size
        # reach the window's edge on whichever side it's aligned away
        # from, and that edge had no margin reserved for it at all
        # before this.
        self.now_playing_box.set_margin_start(12)
        self.now_playing_box.set_margin_end(12)

    def now_playing_placement_changed(self, placement_key):

        self.now_playing_placement = placement_key
        self.apply_now_playing_placement()

    # "Auto" (index 0) maps to None (the watcher's own best-guess
    # heuristic); every other row is a specific bus name. Rebuilt
    # (rather than just appended to) whenever the known-player set
    # changes, since a player disappearing needs its row gone too -
    # see rebuild_now_playing_source_model.
    def now_playing_source_changed(self, bus_name):

        self.now_playing_source = bus_name
        self.now_playing_watcher.set_preferred_source(bus_name)

    def rebuild_now_playing_source_model(self):

        row = self.now_playing_source_row

        bus_names = list(self.now_playing_players.keys())
        labels = ["Auto"] + [self.now_playing_players[n] for n in bus_names]

        row.set_model(Gtk.StringList.new(labels))

        if self.now_playing_source in bus_names:
            row.set_selected(1 + bus_names.index(self.now_playing_source))
        else:
            # The previously-selected source is gone - falls back to
            # Auto both here and in the watcher itself (NowPlayingWatcher.
            # _remove_player already does the latter), so the row and
            # the actual active behavior never disagree with each other.
            self.now_playing_source = None
            row.set_selected(0)

    # GTK4 dropped the old X11-style "urgency hint" entirely (Wayland
    # deliberately restricts apps from grabbing attention that way -
    # no OS-level window shake/flash API exists to call into here). A
    # CSS keyframe animation on the header bar itself (see
    # .melange-header.attention-flash in style.css) is the standard
    # GNOME-native substitute: pulses to the accent color a few times,
    # then the class is removed once the animation's done playing.
    ATTENTION_FLASH_DURATION_MS = 1300

    def flash_attention(self):

        self.headerbar.add_css_class("attention-flash")

        GLib.timeout_add(
            self.ATTENTION_FLASH_DURATION_MS,
            self.stop_attention_flash
        )

    def stop_attention_flash(self):

        self.headerbar.remove_css_class("attention-flash")

        return False

    # Called by a mirror window's "Find Main Window" button/double-
    # click - present() alone can raise/focus this window, but if the
    # toolbar had already auto-hidden it'd come to the front looking
    # empty, and it's easy to lose track of *which* now-focused window
    # is actually the one you were looking for on a multi-monitor
    # setup. Revealing the toolbar and flashing it fixes both.
    def bring_to_attention(self):

        self.present()
        self.reveal_toolbar()
        self.flash_attention()


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
        self.set_nav_arrows_visible(False)

        # Only in fullscreen - windowed mode still needs a visible
        # cursor for ordinary desktop interaction (moving/resizing,
        # other windows, ...).
        if self.is_fullscreen():
            self.set_cursor(Gdk.Cursor.new_from_name("none"))

        return False

    def set_nav_arrows_visible(self, visible):

        css_class = "nav-arrow-visible"

        buttons = (
            self.prev_arrow_button,
            self.next_arrow_button,
            self.favorite_button
        )

        for button in buttons:

            if visible:
                button.add_css_class(css_class)
            else:
                button.remove_css_class(css_class)

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

        # Skip the leftmost/rightmost 15% - that's where the nav-arrow
        # buttons live (see the Gtk.Overlay setup above). This gesture
        # runs in the CAPTURE phase (see its setup above), which fires
        # before descendant widgets get a chance to claim the press -
        # without this carve-out, a click on either arrow button would
        # both start dragging the window *and* trigger the button, an
        # ambiguous double-effect from one click.
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

    def update_fullscreen_button_icon(self, window, param):

        self.fullscreen_button.set_icon_name(
            "view-restore-symbolic"
            if self.is_fullscreen()
            else "view-fullscreen-symbolic"
        )

    def on_close_request(self, window):

        # Mirror windows have no purpose without this one driving them
        # (a mirror shows this window's own webview - see
        # mirror_window.py - so there's nothing left to display once
        # it's gone). Closing them here rather than leaving them open
        # avoids orphaned windows showing a frozen last frame forever.
        for mirror in list(self.mirror_windows):
            mirror.close()

        # Same reasoning as the mirror windows above - an aux window
        # only ever shows this window's own live audio (see
        # aux_window.py), so there's nothing left to feed it once this
        # window is gone.
        for aux_window in list(self.aux_windows.values()):
            aux_window.close()

        # Returning False here (the usual "let the default handler
        # run" convention for this signal) was found - while building
        # the mirror-window close path above - to leave the window
        # fully alive rather than actually closing it. Destroying from
        # an idle callback (deferring past this signal's own emission)
        # and returning True is what actually works; see the identical
        # fix and its verification in mirror_window.py.
        GLib.idle_add(self.destroy)

        return True

    def new_mirror_window_clicked(self, action, param):

        mirror = MirrorWindow(
            primary=self,
            mirror_number=self.next_mirror_number
        )

        self.next_mirror_number += 1

        self.mirror_windows.append(mirror)

        # So a newly opened mirror shows the right title immediately,
        # rather than a placeholder until the next preset change.
        mirror.update_title(self.current_preset_name)

        mirror.present()

        self.rebuild_mirror_windows_menu()

    def update_mirror_titles(self):

        for mirror in self.mirror_windows:
            mirror.update_title(self.current_preset_name)

    def close_all_mirrors_clicked(self, action, param):

        if not self.mirror_windows:
            self.show_toast("No mirror windows open")
            return

        # mirror.close() (via MirrorWindow.on_close_request) removes
        # each one from self.mirror_windows as it closes - iterate a
        # copy so that mutation doesn't skip entries.
        for mirror in list(self.mirror_windows):
            mirror.close()

    def present_mirror_clicked(self, action, parameter):

        mirror_number = parameter.get_int32()

        for mirror in self.mirror_windows:

            if mirror.mirror_number == mirror_number:
                mirror.present()
                return

    def aux_window_toggled(self, action, value, kind):

        action.set_state(value)

        if value.get_boolean():

            if kind not in self.aux_windows:

                aux_window = AuxVisualizerWindow(self, kind)
                self.aux_windows[kind] = aux_window
                aux_window.present()

            else:
                self.aux_windows[kind].present()

        else:

            aux_window = self.aux_windows.pop(kind, None)

            if aux_window is not None:
                aux_window.close()

    def aux_window_closed(self, kind):

        self.aux_windows.pop(kind, None)

        action = self.lookup_action(AUX_WINDOW_ACTIONS[kind])

        if action is not None:
            action.set_state(GLib.Variant("b", False))

    def forward_audio_to_aux_windows(self, data):

        if not self.aux_windows:
            return False

        samples = array.array("h")
        samples.frombytes(data)

        left = [s / 32768.0 for s in samples[0::2]]
        right = [s / 32768.0 for s in samples[1::2]]

        for aux_window in self.aux_windows.values():
            aux_window.push_audio(left, right)

        return False

    def rebuild_mirror_windows_menu(self):

        self.open_mirrors_section.remove_all()

        if not self.mirror_windows:

            # No bound action, so GTK shows this as an inert label
            # rather than a clickable-but-broken item - same reasoning
            # as leaving an Audio Source entry unbound when there's
            # nothing to select there.
            self.open_mirrors_section.append_item(
                Gio.MenuItem.new("No mirror windows open", None)
            )

            return

        for mirror in self.mirror_windows:

            item = Gio.MenuItem.new(
                f"Mirror {mirror.mirror_number}",
                None
            )

            item.set_action_and_target_value(
                "win.present-mirror",
                GLib.Variant("i", mirror.mirror_number)
            )

            self.open_mirrors_section.append_item(item)

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

        # Auto-cycle's own JS-side timer (setCyclePaused, main.js) is
        # actually paused here, not just relied on to have its result
        # blocked - real bug report: next_preset()'s own lock check
        # was already enough to stop it from ever *advancing* while
        # locked, but the timer itself kept ticking the whole time,
        # so "Preset is locked" kept re-toasting on every single
        # interval instead of just once. Blend Time's own control is
        # included in the greyed-out group even though it's not
        # cycling-specific by itself, since it's only ever relevant to
        # a preset *change* actually happening.
        self.cycling_group.set_sensitive(not self.preset_locked)

        self.run_js(
            f"setCyclePaused({'true' if self.preset_locked else 'false'});"
        )

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

    def loop_queue_changed(self, action, value):

        action.set_state(value)

        enabled = value.get_boolean()

        self.run_js(f"setQueueLoop({'true' if enabled else 'false'});")

        self.show_toast(
            "Queue loop on" if enabled else "Queue loop off"
        )

    def shuffle_queue_changed(self, action, value):

        action.set_state(value)

        enabled = value.get_boolean()

        self.run_js(f"setQueueShuffle({'true' if enabled else 'false'});")

        self.show_toast(
            "Queue shuffle on" if enabled else "Queue shuffle off"
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
            0.0, 120.0, 1.0, 30.0,
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

    def build_transparency_opacity_control(self):

        def format_transparency_opacity(value):
            return "Fully Invisible" if value <= 0 else f"{int(round(value * 100))}% Opaque"

        # Bug: previously only stored the value, never re-applied it -
        # so dragging this while Transparency Mode was already on did
        # nothing until the mode was toggled off and back on (the only
        # other place that ever read self.transparency_opacity into an
        # actual opacity change). Now applies it live, bypassing (and
        # cancelling) any in-progress fade so a manual drag always
        # wins over a stale animate_opacity() timer still chasing
        # whatever target was in effect when the mode was last toggled.
        def transparency_opacity_changed(value):

            self.transparency_opacity = value

            if not self.transparency_mode_enabled:
                return

            if self.opacity_fade_timer:
                GLib.source_remove(self.opacity_fade_timer)
                self.opacity_fade_timer = None

            self.toast_overlay.set_opacity(value)

        return self.build_slider_row(
            "Opacity Level",
            0.0, 1.0, 0.05, 0.5,
            format_transparency_opacity,
            transparency_opacity_changed,
            store_as="transparency_opacity_scale"
        )

    def build_transparency_fade_control(self):

        def format_transparency_fade(value):
            return "Instant" if value <= 0 else f"{value:.1f}s"

        return self.build_slider_row(
            "Fade Speed",
            0.0, 2.0, 0.1, 0.0,
            format_transparency_fade,
            lambda value: setattr(self, "transparency_fade_ms", value * 1000),
            store_as="transparency_fade_scale"
        )

    # Bound via action-name (like Transparency Mode's own Enabled row)
    # rather than build_toggle_row now that this is a real action -
    # stays in sync with the hamburger menu item automatically in both
    # directions, no extra state to keep them agreeing.
    def build_now_playing_enabled_control(self):

        row = Adw.SwitchRow(
            title="Enabled",
            action_name="win.now-playing-enabled"
        )

        self.now_playing_enabled_row = row

        return row

    NOW_PLAYING_PLACEMENT_LABELS = [
        ("top-left", "Top Left"),
        ("top-center", "Top Center"),
        ("top-right", "Top Right"),
        ("bottom-left", "Bottom Left"),
        ("bottom-center", "Bottom Center"),
        ("bottom-right", "Bottom Right"),
    ]

    def build_now_playing_placement_control(self):

        keys = [key for key, label in self.NOW_PLAYING_PLACEMENT_LABELS]
        labels = [label for key, label in self.NOW_PLAYING_PLACEMENT_LABELS]

        row = Adw.ComboRow(
            title="Placement",
            model=Gtk.StringList.new(labels)
        )

        row.set_selected(keys.index(self.now_playing_placement))

        row.connect(
            "notify::selected",
            lambda r, param: self.now_playing_placement_changed(
                keys[r.get_selected()]
            )
        )

        self.now_playing_placement_row = row

        return row

    # Starts as just ["Auto"] - rebuild_now_playing_source_model fills
    # in real players as NowPlayingWatcher discovers them (which may
    # well be before this row even exists, if one's already running
    # when Preferences is first opened - on_now_playing_players_changed
    # checks hasattr(self, "now_playing_source_row") for exactly that
    # ordering, and this calls the same rebuild once more here so a
    # dialog opened after that point isn't stuck showing just "Auto").
    def build_now_playing_source_control(self):

        row = Adw.ComboRow(
            title="Source",
            subtitle="Which app to show, when more than one is playing",
            model=Gtk.StringList.new(["Auto"])
        )

        self.now_playing_source_row = row

        self.rebuild_now_playing_source_model()

        row.connect(
            "notify::selected",
            lambda r, param: self.now_playing_source_changed(
                None if r.get_selected() == 0
                else list(self.now_playing_players.keys())[r.get_selected() - 1]
            )
        )

        return row

    def build_now_playing_show_title_control(self):

        return self.build_toggle_row(
            "Show Title", True, self.now_playing_show_title_changed
        )

    def build_now_playing_show_artist_control(self):

        return self.build_toggle_row(
            "Show Artist", True, self.now_playing_show_artist_changed
        )

    def build_now_playing_show_album_control(self):

        return self.build_toggle_row(
            "Show Album", True, self.now_playing_show_album_changed
        )

    def build_now_playing_show_artwork_control(self):

        return self.build_toggle_row(
            "Show Artwork", True, self.now_playing_show_artwork_changed
        )

    def build_now_playing_show_time_control(self):

        return self.build_toggle_row(
            "Show Playback Time", False, self.now_playing_show_time_changed
        )

    def build_now_playing_show_background_control(self):

        return self.build_toggle_row(
            "Show Background", True, self.now_playing_show_background_changed
        )

    # One collapsed row instead of 6 always-visible toggle rows - the
    # individual build_now_playing_show_*_control methods are unchanged
    # and still used, just nested inside this Adw.ExpanderRow instead
    # of added to the Preferences group directly.
    def build_now_playing_displayed_fields_control(self):

        row = Adw.ExpanderRow(title="Displayed Fields")

        for sub_row in (
            self.build_now_playing_show_title_control(),
            self.build_now_playing_show_artist_control(),
            self.build_now_playing_show_album_control(),
            self.build_now_playing_show_artwork_control(),
            self.build_now_playing_show_time_control(),
            self.build_now_playing_show_background_control()
        ):
            row.add_row(sub_row)

        return row

    # Text Size/Album Art Size/Text Box Width used to be three
    # independent sliders - consolidated into one Scale multiplier on
    # request (see recompute_now_playing_scale). Hidden while Lock to
    # Window Size is on, since sizing is then fully automatic.
    def build_now_playing_scale_control(self):

        def format_scale(value):
            return f"{round(value * 100)}%"

        row = self.build_slider_row(
            "Scale",
            self.NOW_PLAYING_MIN_SCALE, self.NOW_PLAYING_MAX_SCALE,
            0.05, self.now_playing_scale,
            format_scale,
            self.now_playing_scale_changed,
            store_as="now_playing_scale_scale"
        )

        # build_slider_row's store_as= captures the inner Gtk.Scale,
        # not the Adw.ActionRow itself - a separate handle is needed
        # here so now_playing_lock_to_window_changed can hide/show the
        # whole row, not just the scale widget inside it.
        self.now_playing_scale_row = row
        row.set_visible(not self.now_playing_lock_to_window)

        return row

    def build_now_playing_lock_to_window_control(self):

        return self.build_toggle_row(
            "Lock to Window Size",
            self.now_playing_lock_to_window,
            self.now_playing_lock_to_window_changed
        )

    def build_now_playing_auto_hide_seconds_control(self):

        def format_auto_hide_seconds(value):
            return f"{value:.1f}s"

        return self.build_slider_row(
            "Auto-Hide Delay",
            1.0, 20.0, 0.5, 5.0,
            format_auto_hide_seconds,
            self.now_playing_auto_hide_seconds_changed,
            store_as="now_playing_auto_hide_seconds_scale"
        )

    # Toggle-row-plus-separate-slider-row collapsed into one
    # Adw.ExpanderRow with its own built-in enable switch - the switch
    # is the same on/off state now_playing_auto_hide_changed always
    # drove, and expanding/collapsing (auto-synced to the switch, so
    # no separate click is needed to see it) reveals the Auto-Hide
    # Delay slider only when it's actually relevant.
    def build_now_playing_auto_hide_control(self):

        row = Adw.ExpanderRow(
            title="Auto-Hide",
            show_enable_switch=True,
            enable_expansion=self.now_playing_auto_hide,
            expanded=self.now_playing_auto_hide
        )

        def on_enable_changed(r, param):
            active = r.get_enable_expansion()
            r.set_expanded(active)
            self.now_playing_auto_hide_changed(active)

        row.connect("notify::enable-expansion", on_enable_changed)
        row.add_row(self.build_now_playing_auto_hide_seconds_control())

        return row

    def build_now_playing_periodic_fade_seconds_control(self):

        def format_periodic_fade_seconds(value):
            return f"{value:.1f}s"

        return self.build_slider_row(
            "Fade Interval",
            1.0, 15.0, 0.5, 4.0,
            format_periodic_fade_seconds,
            self.now_playing_periodic_fade_seconds_changed,
            store_as="now_playing_periodic_fade_seconds_scale"
        )

    # Same ExpanderRow-with-switch treatment as Auto-Hide above.
    def build_now_playing_periodic_fade_control(self):

        row = Adw.ExpanderRow(
            title="Periodic Fade",
            show_enable_switch=True,
            enable_expansion=self.now_playing_periodic_fade,
            expanded=self.now_playing_periodic_fade
        )

        def on_enable_changed(r, param):
            active = r.get_enable_expansion()
            r.set_expanded(active)
            self.now_playing_periodic_fade_changed(active)

        row.connect("notify::enable-expansion", on_enable_changed)
        row.add_row(self.build_now_playing_periodic_fade_seconds_control())

        return row

    # Level=FACE restricts the native GTK4 font picker to choosing a
    # typeface plus its style (Regular/Bold/Italic/Bold Italic, per
    # request) but not a point size - that's this app's own separate
    # Text Size slider, so a full font-with-size dialog would just be
    # a confusing second place to set the same thing from. The size
    # FontDialogButton reports is stripped before storing (unset_
    # fields(Pango.FontMask.SIZE)) so apply_now_playing_text_style's
    # own size always wins regardless of whatever this dialog's own
    # default/leftover size field happens to be.
    def build_now_playing_font_control(self):

        row = Adw.ActionRow(title="Font")

        button = Gtk.FontDialogButton(dialog=Gtk.FontDialog())
        button.set_level(Gtk.FontLevel.FACE)
        button.set_valign(Gtk.Align.CENTER)

        button.set_font_desc(
            Pango.FontDescription.from_string(self.now_playing_font_desc)
        )

        def on_font_changed(b, param):

            desc = b.get_font_desc()
            desc.unset_fields(Pango.FontMask.SIZE)
            self.now_playing_font_changed(desc.to_string())

        button.connect("notify::font-desc", on_font_changed)

        row.add_suffix(button)

        self.now_playing_font_button = button

        return row

    def build_now_playing_scroll_long_titles_control(self):

        return self.build_toggle_row(
            "Scroll Long Titles",
            False,
            self.now_playing_scroll_long_titles_changed
        )

    def build_now_playing_text_color_control(self):

        row = Adw.ActionRow(title="Text Color")

        button = Gtk.ColorDialogButton(dialog=Gtk.ColorDialog())
        button.set_valign(Gtk.Align.CENTER)
        button.set_rgba(self.now_playing_text_color)

        button.connect(
            "notify::rgba",
            lambda b, param: self.now_playing_text_color_changed(b.get_rgba())
        )

        row.add_suffix(button)

        self.now_playing_text_color_button = button

        return row

    def build_toolbar_hide_delay_control(self):

        def format_toolbar_hide_delay(value):
            return "Never" if value <= 0 else f"{int(value)}s"

        def toolbar_hide_delay_changed(value):
            self.toolbar_hide_delay = value
            # Re-arms immediately against the new delay rather than
            # waiting for the next mouse move/toolbar leave - changing
            # the setting while the toolbar happens to already be
            # hidden-and-waiting (or newly set to Never while a timer
            # is still pending) should take effect right away.
            if self.mouse_over_toolbar:
                return
            self.schedule_toolbar_hide()

        return self.build_slider_row(
            "Hide Delay",
            0.0, 10.0, 1.0, 3.0,
            format_toolbar_hide_delay,
            toolbar_hide_delay_changed,
            store_as="toolbar_hide_delay_scale"
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

        self.cycling_group = cycling_group

        beat_group = Adw.PreferencesGroup(
            title="Beat Detection",
            description=(
                "Tuned against synthetic test signals, not yet "
                "validated against a wide range of real music. See "
                "docs/beat-detection.md."
            )
        )
        beat_group.add(self.build_beat_cycle_control())
        beat_group.add(self.build_beat_mode_control())
        beat_group.add(self.build_beat_sensitivity_control())
        beat_group.add(self.build_beat_cooldown_control())
        beat_group.add(self.build_beat_silence_control())

        transparency_group = Adw.PreferencesGroup(
            title="Transparency Mode",
            description="The header bar always stays fully visible."
        )

        # Bound straight to the action via action-name (Adw.SwitchRow
        # implements Gtk.Actionable, same as the loop/shuffle queue
        # Gtk.ToggleButtons elsewhere in this file) rather than a
        # build_toggle_row callback - stays in sync automatically with
        # the hamburger menu's own toggle in both directions, with no
        # extra state to keep them agreeing.
        transparency_enable_row = Adw.SwitchRow(
            title="Enabled",
            action_name="win.transparency-mode"
        )

        transparency_group.add(transparency_enable_row)
        transparency_group.add(self.build_transparency_opacity_control())
        transparency_group.add(self.build_transparency_fade_control())

        now_playing_group = Adw.PreferencesGroup(title="Now Playing")
        now_playing_group.add(self.build_now_playing_enabled_control())
        now_playing_group.add(self.build_now_playing_source_control())
        now_playing_group.add(self.build_now_playing_placement_control())
        now_playing_group.add(self.build_now_playing_displayed_fields_control())
        now_playing_group.add(self.build_now_playing_lock_to_window_control())
        now_playing_group.add(self.build_now_playing_scale_control())
        now_playing_group.add(self.build_now_playing_auto_hide_control())
        now_playing_group.add(self.build_now_playing_periodic_fade_control())
        now_playing_group.add(self.build_now_playing_scroll_long_titles_control())
        now_playing_group.add(self.build_now_playing_font_control())
        now_playing_group.add(self.build_now_playing_text_color_control())

        toolbar_group = Adw.PreferencesGroup(
            title="Toolbar",
            description=(
                "How soon the header bar and nav arrows auto-hide "
                "after the mouse stops moving."
            )
        )
        toolbar_group.add(self.build_toolbar_hide_delay_control())

        playback_page = Adw.PreferencesPage(
            title="Playback",
            icon_name="media-playback-start-symbolic"
        )

        playback_page.add(cycling_group)

        # Its own page rather than a group folded into Playback -
        # requested ("move the beat detection to an experimental
        # section") - a dedicated Experimental page also gives a real
        # home for any other feature that needs the same "works, but
        # not validated/finished" framing later, rather than each one
        # bolting an "Experimental" description onto whatever page it
        # happens to fit into otherwise.
        experimental_page = Adw.PreferencesPage(
            title="Experimental",
            icon_name="applications-science-symbolic"
        )

        experimental_page.add(beat_group)

        appearance_page = Adw.PreferencesPage(
            title="Appearance",
            icon_name="preferences-desktop-appearance-symbolic"
        )

        appearance_page.add(toolbar_group)
        appearance_page.add(transparency_group)
        appearance_page.add(now_playing_group)

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
        dialog.add(appearance_page)
        dialog.add(rendering_page)
        dialog.add(experimental_page)
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

        self.browser_view_stack.set_visible_child_name("presets")
        self.preset_browser_dialog.present(self)

    def build_preset_browser_dialog(self):

        view_stack = Adw.ViewStack()

        view_stack.add_titled_with_icon(
            self.build_presets_tab(),
            "presets", "Presets", "view-list-symbolic"
        )

        view_stack.add_titled_with_icon(
            self.build_favorites_tab(),
            "favorites", "Favorites", "starred-symbolic"
        )

        view_stack.add_titled_with_icon(
            self.build_loaded_presets_tab(),
            "loaded", "Loaded", "document-open-symbolic"
        )

        view_stack.add_titled_with_icon(
            self.build_queue_tab(),
            "queue", "Queue", "view-continuous-symbolic"
        )

        switcher = Adw.ViewSwitcher()
        switcher.set_stack(view_stack)
        switcher.set_policy(Adw.ViewSwitcherPolicy.WIDE)

        header = Adw.HeaderBar()
        header.set_title_widget(switcher)

        toolbar_view = Adw.ToolbarView()
        toolbar_view.add_top_bar(header)
        toolbar_view.set_content(view_stack)

        dialog = Adw.Dialog()
        dialog.set_title("Presets")
        dialog.set_content_width(560)
        dialog.set_content_height(560)
        dialog.set_child(toolbar_view)

        self.preset_browser_dialog = dialog
        self.browser_view_stack = view_stack

    # Shared by the Presets and Favorites tabs - same search/filter/
    # row-rendering behavior, only the backing Gtk.StringList differs.
    def build_preset_search_list(self, list_store):

        expression = Gtk.PropertyExpression.new(
            Gtk.StringObject,
            None,
            "string"
        )

        string_filter = Gtk.StringFilter.new(expression)
        string_filter.set_match_mode(Gtk.StringFilterMatchMode.SUBSTRING)

        filter_model = Gtk.FilterListModel.new(
            list_store,
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

        return box

    def build_presets_tab(self):

        self.preset_list_store = Gtk.StringList.new(self.preset_names)

        box = self.build_preset_search_list(self.preset_list_store)

        # Moved here from the hamburger menu on request ("Load preset
        # should be in the preset tab") - same win.load-preset action,
        # just reachable from inside the browser now rather than the
        # menu. At the bottom (append, not prepend) rather than above
        # the search entry - requested as a "better spot", out of the
        # way of the tab's main job (browsing/searching the existing
        # list) rather than competing with it for top-of-tab attention.
        load_button = Gtk.Button(
            label="Load Preset…",
            action_name="win.load-preset"
        )
        load_button.set_halign(Gtk.Align.END)

        box.append(load_button)

        return box

    def build_favorites_tab(self):

        self.favorites_list_store = Gtk.StringList.new(self.favorites)

        return self.build_preset_search_list(self.favorites_list_store)

    # Only presets loaded via win.load-preset (the file picker), not
    # the bundled packs - session-scoped, see the comment on
    # self.user_loaded_presets in __init__ for why this deliberately
    # isn't persisted to disk the way Favorites is.
    def build_loaded_presets_tab(self):

        self.user_preset_list_store = Gtk.StringList.new(self.user_loaded_presets)

        return self.build_preset_search_list(self.user_preset_list_store)

    def preset_row_setup(self, factory, list_item):

        box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=2)

        box.set_margin_start(6)
        box.set_margin_end(6)
        box.set_margin_top(4)
        box.set_margin_bottom(4)

        # max_width_chars keeps the label's requested (natural) size
        # small - without it, GTK sizes the row to fit the full preset
        # name (some are 100+ characters) regardless of ellipsize,
        # pushing the buttons off the edge of the dialog. hexpand still
        # lets it fill whatever width is actually available.
        label = Gtk.Label(xalign=0, hexpand=True)
        label.set_ellipsize(Pango.EllipsizeMode.END)
        label.set_max_width_chars(1)

        box.append(label)

        favorite_button = Gtk.Button(icon_name="non-starred-symbolic")

        favorite_button.add_css_class("flat")
        favorite_button.set_tooltip_text("Add to Favorites")

        box.append(favorite_button)

        queue_button = Gtk.Button(icon_name="list-add-symbolic")

        queue_button.add_css_class("flat")
        queue_button.set_tooltip_text("Add to Queue")

        box.append(queue_button)

        playlist_button = Gtk.Button(icon_name="bookmark-new-symbolic")

        playlist_button.add_css_class("flat")
        playlist_button.set_tooltip_text("Add to Playlist…")

        box.append(playlist_button)

        list_item.set_child(box)

        # Stashed on the list_item itself (not the widgets) so bind
        # can find them again - GTK4 ListView recycles these rows for
        # different items as you scroll, so bind gets called many
        # times for the same row/button pair.
        list_item.preset_label = label
        list_item.favorite_button = favorite_button
        list_item.favorite_button_handler = None
        list_item.queue_button = queue_button
        list_item.queue_button_handler = None
        list_item.playlist_button = playlist_button
        list_item.playlist_button_handler = None

    def preset_row_bind(self, factory, list_item):

        string_object = list_item.get_item()
        name = string_object.get_string()

        list_item.preset_label.set_label(name)

        if list_item.favorite_button_handler is not None:
            list_item.favorite_button.disconnect(list_item.favorite_button_handler)

        list_item.favorite_button.set_icon_name(
            "starred-symbolic" if self.is_favorite(name) else "non-starred-symbolic"
        )

        list_item.favorite_button_handler = list_item.favorite_button.connect(
            "clicked",
            lambda button, n=name: self.favorite_row_clicked(button, n)
        )

        if list_item.queue_button_handler is not None:
            list_item.queue_button.disconnect(list_item.queue_button_handler)

        list_item.queue_button_handler = list_item.queue_button.connect(
            "clicked",
            lambda button: self.enqueue_preset(name)
        )

        if list_item.playlist_button_handler is not None:
            list_item.playlist_button.disconnect(list_item.playlist_button_handler)

        list_item.playlist_button_handler = list_item.playlist_button.connect(
            "clicked",
            lambda button, n=name: self.open_add_to_playlist_popover(button, n)
        )

    def favorite_row_clicked(self, button, name):

        favorited = self.toggle_favorite(name)

        # refresh_favorites_list (called by toggle_favorite) already
        # handles the Favorites tab - this row's own icon still needs
        # a direct update for the Presets tab case, where the row
        # stays in place rather than being spliced out.
        button.set_icon_name(
            "starred-symbolic" if favorited else "non-starred-symbolic"
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

        if self.preset_browser_dialog is None:
            self.build_preset_browser_dialog()

        self.browser_view_stack.set_visible_child_name("queue")
        self.preset_browser_dialog.present(self)

    def show_favorites_clicked(self, action, param):

        if self.preset_browser_dialog is None:
            self.build_preset_browser_dialog()

        self.browser_view_stack.set_visible_child_name("favorites")
        self.preset_browser_dialog.present(self)

    def build_queue_tab(self):

        self.queue_list_store = Gtk.StringList.new(self.preset_queue)

        selection = Gtk.NoSelection.new(self.queue_list_store)

        factory = Gtk.SignalListItemFactory()

        factory.connect("setup", self.queue_row_setup)
        factory.connect("bind", self.queue_row_bind)

        list_view = Gtk.ListView.new(selection, factory)

        scrolled = Gtk.ScrolledWindow()
        scrolled.set_child(list_view)
        scrolled.set_vexpand(True)

        # Loop/Shuffle/Playlists used to live in this tab's own dialog
        # header, back when it was its own separate dialog - now a
        # small button row above the list instead, since the Presets/
        # Favorites/Queue tabs share one header (the tab switcher)
        # rather than each page bringing its own.
        controls = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        controls.set_halign(Gtk.Align.END)

        loop_button = Gtk.ToggleButton(
            icon_name="media-playlist-repeat-symbolic",
            tooltip_text="Loop Queue",
            action_name="win.loop-queue"
        )

        controls.append(loop_button)

        shuffle_button = Gtk.ToggleButton(
            icon_name="media-playlist-shuffle-symbolic",
            tooltip_text="Shuffle Queue",
            action_name="win.shuffle-queue"
        )

        controls.append(shuffle_button)

        playlists_button = Gtk.Button(
            icon_name="view-list-symbolic",
            tooltip_text="Playlists…",
            action_name="win.show-playlists"
        )

        controls.append(playlists_button)

        unload_playlist_button = Gtk.Button(
            icon_name="media-eject-symbolic",
            tooltip_text="Unload Playlist"
        )

        unload_playlist_button.connect(
            "clicked",
            self.unload_playlist_clicked
        )

        controls.append(unload_playlist_button)

        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        box.set_margin_start(12)
        box.set_margin_end(12)
        box.set_margin_top(12)
        box.set_margin_bottom(12)

        box.append(controls)
        box.append(scrolled)

        return box

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

    def playlists_file_path(self):

        config_dir = Path(GLib.get_user_config_dir()) / "melange"
        config_dir.mkdir(parents=True, exist_ok=True)

        return config_dir / "playlists.json"

    def load_playlists(self):

        path = self.playlists_file_path()

        if not path.exists():
            return []

        try:
            with open(path) as f:
                return json.load(f)
        except (OSError, json.JSONDecodeError) as e:
            print("Failed to load playlists:", e)
            return []

    def save_playlists(self):

        try:
            with open(self.playlists_file_path(), "w") as f:
                json.dump(self.playlists, f, indent=2)
        except OSError as e:
            print("Failed to save playlists:", e)

    def favorites_file_path(self):

        config_dir = Path(GLib.get_user_config_dir()) / "melange"
        config_dir.mkdir(parents=True, exist_ok=True)

        return config_dir / "favorites.json"

    def load_favorites(self):

        path = self.favorites_file_path()

        if not path.exists():
            return []

        try:
            with open(path) as f:
                return json.load(f)
        except (OSError, json.JSONDecodeError) as e:
            print("Failed to load favorites:", e)
            return []

    def save_favorites(self):

        try:
            with open(self.favorites_file_path(), "w") as f:
                json.dump(self.favorites, f, indent=2)
        except OSError as e:
            print("Failed to save favorites:", e)

    def is_favorite(self, name):

        return name in self.favorites

    # Unlike the queue/playlists, favorites are a purely Python-side
    # concept - JS never needs to know what's favorited (it doesn't
    # drive any playback decision the way the queue does), so there's
    # no debug-message round trip here, just a direct list mutation +
    # save, same as the profiles/playlists file-backed lists.
    def toggle_favorite(self, name):

        if name in self.favorites:
            self.favorites.remove(name)
            favorited = False
        else:
            self.favorites.append(name)
            favorited = True

        self.save_favorites()
        self.refresh_favorites_list()

        if name == self.current_preset_name:
            self.update_favorite_button_icon()

        return favorited

    def refresh_favorites_list(self):

        if self.favorites_list_store is not None:
            self.favorites_list_store.splice(
                0,
                self.favorites_list_store.get_n_items(),
                self.favorites
            )

    def favorite_button_clicked(self, button):

        self.toggle_favorite_action_activated(None, None)

    def toggle_favorite_action_activated(self, action, param):

        if not self.current_preset_name:
            return

        favorited = self.toggle_favorite(self.current_preset_name)

        self.show_toast(
            f'Added to favorites: "{self.current_preset_name}"'
            if favorited
            else f'Removed from favorites: "{self.current_preset_name}"'
        )

    def update_favorite_button_icon(self):

        starred = (
            self.current_preset_name is not None
            and self.is_favorite(self.current_preset_name)
        )

        self.favorite_button.set_icon_name(
            "starred-symbolic" if starred else "non-starred-symbolic"
        )

    def build_window_title(self):

        title = "Melange"

        if self.current_playlist_name:
            title += f" ({self.current_playlist_name})"

        if self.current_preset_name:
            title += f' - "{self.current_preset_name}"'

        return title

    def show_playlists_clicked(self, action, param):

        if self.playlists_dialog is None:
            self.build_playlists_dialog()

        self.playlists_dialog.present(self)

    def build_playlists_dialog(self):

        self.playlists_group = Adw.PreferencesGroup()

        save_row = Adw.ActionRow(
            title="Save Current Queue as Playlist…",
            activatable=True
        )

        save_row.connect("activated", self.save_playlist_clicked)
        save_row.add_suffix(
            Gtk.Image.new_from_icon_name("document-save-symbolic")
        )

        self.playlists_group.add(save_row)

        # Tracked separately from save_row, same reasoning as
        # profile_rows in build_profiles_page - a refresh needs to
        # remove exactly the playlist rows, not the always-present
        # save row above them.
        self.playlist_rows = []
        self.refresh_playlists_list()

        clamp = Adw.Clamp()
        clamp.set_child(self.playlists_group)
        clamp.set_margin_start(12)
        clamp.set_margin_end(12)
        clamp.set_margin_top(12)
        clamp.set_margin_bottom(12)

        scrolled = Gtk.ScrolledWindow()
        scrolled.set_child(clamp)
        scrolled.set_vexpand(True)

        toolbar_view = Adw.ToolbarView()
        toolbar_view.add_top_bar(Adw.HeaderBar())
        toolbar_view.set_content(scrolled)

        dialog = Adw.Dialog()
        dialog.set_title("Playlists")
        dialog.set_content_width(420)
        dialog.set_content_height(480)
        dialog.set_child(toolbar_view)

        self.playlists_dialog = dialog

    def build_playlist_row(self, playlist):

        count = len(playlist["presets"])

        row = Adw.ActionRow(
            title=playlist["name"],
            subtitle=f"{count} preset{'s' if count != 1 else ''}"
        )

        load_button = Gtk.Button(icon_name="document-open-symbolic")
        load_button.add_css_class("flat")
        load_button.set_valign(Gtk.Align.CENTER)
        load_button.set_tooltip_text("Load")

        load_button.connect(
            "clicked",
            lambda b, name=playlist["name"]: self.load_playlist_by_name(name)
        )

        row.add_suffix(load_button)

        remove_button = Gtk.Button(icon_name="user-trash-symbolic")
        remove_button.add_css_class("flat")
        remove_button.set_valign(Gtk.Align.CENTER)
        remove_button.set_tooltip_text("Delete")

        remove_button.connect(
            "clicked",
            lambda b, name=playlist["name"]: self.delete_playlist_by_name(name)
        )

        row.add_suffix(remove_button)

        return row

    def save_playlist_clicked(self, row):

        if not self.preset_queue:
            self.show_toast("Queue is empty")
            return

        entry = Gtk.Entry()
        entry.set_placeholder_text("Playlist name")

        dialog = Adw.AlertDialog(
            heading="Save Playlist",
            body="Save the current Queue as a named playlist."
        )

        dialog.set_extra_child(entry)
        dialog.add_response("cancel", "Cancel")
        dialog.add_response("save", "Save")
        dialog.set_response_appearance("save", Adw.ResponseAppearance.SUGGESTED)
        dialog.set_default_response("save")
        dialog.set_close_response("cancel")

        dialog.connect(
            "response",
            lambda d, response: self.on_save_playlist_response(response, entry)
        )

        dialog.present(self)
        entry.grab_focus()

    def on_save_playlist_response(self, response, entry):

        if response != "save":
            return

        name = entry.get_text().strip()

        if not name:
            self.show_toast("Playlist needs a name")
            return

        # Saving over an existing name replaces it rather than
        # silently creating a duplicate entry.
        self.playlists = [p for p in self.playlists if p["name"] != name]

        self.playlists.append({
            "name": name,
            "presets": list(self.preset_queue)
        })

        self.save_playlists()
        self.refresh_playlists_list()
        self.show_toast(f"Saved playlist \"{name}\"")

    def load_playlist_by_name(self, name):

        playlist = next(
            (p for p in self.playlists if p["name"] == name),
            None
        )

        if playlist is None:
            return

        # setQueue (JS) replaces the queue wholesale, then reports back
        # via announceQueue()/QUEUE: same as every other queue mutation
        # - Python's self.preset_queue and queue_list_store update from
        # that round trip, not directly here.
        self.run_js(f"setQueue({json.dumps(playlist['presets'])});")

        self.current_playlist_name = name
        self.set_title(self.build_window_title())

        self.show_toast(f"Loaded playlist \"{name}\"")

    def unload_playlist_clicked(self, button):

        if self.current_playlist_name is None:
            self.show_toast("No playlist loaded")
            return

        name = self.current_playlist_name

        # Only clears the name association (and the title showing it)
        # - the queue itself is left exactly as it is, same as how
        # loading a playlist doesn't ask first before replacing
        # whatever was queued. If you also want an empty queue,
        # clearing it is a separate, already-existing action (removing
        # items, or loading a different/empty playlist).
        self.current_playlist_name = None
        self.set_title(self.build_window_title())

        self.show_toast(f"Unloaded playlist \"{name}\"")

    def delete_playlist_by_name(self, name):

        self.playlists = [p for p in self.playlists if p["name"] != name]

        self.save_playlists()
        self.refresh_playlists_list()
        self.show_toast(f"Deleted playlist \"{name}\"")

    # Per-row "add to playlist" popover (Presets/Favorites tabs) - a
    # flat list of buttons built fresh on every click rather than a
    # persistent Gio.Menu, since which playlists exist can change
    # between clicks and there's no menu-model equivalent of "list of
    # playlist names" to keep in sync otherwise.
    def open_add_to_playlist_popover(self, button, name, include_queue=False):

        popover = Gtk.Popover()
        popover.set_parent(button)
        popover.connect("closed", lambda p: p.unparent())

        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        box.set_margin_start(6)
        box.set_margin_end(6)
        box.set_margin_top(6)
        box.set_margin_bottom(6)

        if include_queue:
            queue_row = Gtk.Button(label="Add to Queue")
            queue_row.add_css_class("flat")

            row_label = queue_row.get_child()
            if isinstance(row_label, Gtk.Label):
                row_label.set_xalign(0)

            queue_row.connect(
                "clicked",
                lambda b, n=name: self.add_to_queue_row_clicked(popover, n)
            )

            box.append(queue_row)
            box.append(Gtk.Separator())

        if not self.playlists:
            empty_label = Gtk.Label(label="No playlists yet")
            empty_label.add_css_class("dim-label")
            box.append(empty_label)
        else:
            for playlist in self.playlists:
                playlist_row = Gtk.Button(label=playlist["name"])
                playlist_row.add_css_class("flat")

                row_label = playlist_row.get_child()
                if isinstance(row_label, Gtk.Label):
                    row_label.set_xalign(0)
                    row_label.set_ellipsize(Pango.EllipsizeMode.END)
                    row_label.set_max_width_chars(1)

                playlist_row.connect(
                    "clicked",
                    lambda b, n=name, p=playlist["name"]:
                        self.add_to_playlist_row_clicked(popover, n, p)
                )

                box.append(playlist_row)

            box.append(Gtk.Separator())

        new_playlist_row = Gtk.Button(label="New Playlist…")
        new_playlist_row.add_css_class("flat")

        new_playlist_row.connect(
            "clicked",
            lambda b, n=name: self.new_playlist_row_clicked(popover, n)
        )

        box.append(new_playlist_row)

        popover.set_child(box)
        popover.popup()

    def add_to_queue_row_clicked(self, popover, name):

        popover.popdown()
        self.enqueue_preset(name)

    def add_to_playlist_row_clicked(self, popover, name, playlist_name):

        popover.popdown()
        self.add_preset_to_playlist(name, playlist_name)

    def new_playlist_row_clicked(self, popover, name):

        popover.popdown()
        self.new_playlist_from_preset_clicked(name)

    def add_preset_to_playlist(self, name, playlist_name):

        playlist = next(
            (p for p in self.playlists if p["name"] == playlist_name),
            None
        )

        if playlist is None:
            return

        if name in playlist["presets"]:
            self.show_toast(f'"{name}" is already in "{playlist_name}"')
            return

        playlist["presets"].append(name)

        self.save_playlists()
        self.refresh_playlists_list()
        self.show_toast(f'Added to "{playlist_name}": {name}')

    def new_playlist_from_preset_clicked(self, name):

        entry = Gtk.Entry()
        entry.set_placeholder_text("Playlist name")

        dialog = Adw.AlertDialog(
            heading="New Playlist",
            body=f'Create a new playlist containing "{name}".'
        )

        dialog.set_extra_child(entry)
        dialog.add_response("cancel", "Cancel")
        dialog.add_response("create", "Create")
        dialog.set_response_appearance("create", Adw.ResponseAppearance.SUGGESTED)
        dialog.set_default_response("create")
        dialog.set_close_response("cancel")

        dialog.connect(
            "response",
            lambda d, response: self.on_new_playlist_from_preset_response(
                response, entry, name
            )
        )

        dialog.present(self)
        entry.grab_focus()

    def on_new_playlist_from_preset_response(self, response, entry, name):

        if response != "create":
            return

        playlist_name = entry.get_text().strip()

        if not playlist_name:
            self.show_toast("Playlist needs a name")
            return

        # Saving over an existing name replaces it, same as
        # on_save_playlist_response - deliberately consistent rather
        # than silently creating a duplicate or refusing.
        self.playlists = [p for p in self.playlists if p["name"] != playlist_name]

        self.playlists.append({
            "name": playlist_name,
            "presets": [name]
        })

        self.save_playlists()
        self.refresh_playlists_list()
        self.show_toast(f'Created playlist "{playlist_name}" with "{name}"')

    def refresh_playlists_list(self):

        # May be called before the Playlists dialog has ever been
        # built (e.g. from the new per-row "add to playlist" button) -
        # nothing to refresh yet in that case.
        if self.playlists_dialog is None:
            return

        for row in self.playlist_rows:
            self.playlists_group.remove(row)

        self.playlist_rows = [
            self.build_playlist_row(playlist)
            for playlist in self.playlists
        ]

        for row in self.playlist_rows:
            self.playlists_group.add(row)

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

        # Confirmed successful (or not) in on_webview_debug_message's
        # own PRESET_LIST:/LOAD_PRESET_ERROR: handling - this call
        # site only knows the load was *dispatched*, not that it
        # actually parsed/converted successfully on the JS side.
        self.pending_loaded_preset_name = name

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

                if self.aux_windows:
                    GLib.idle_add(
                        self.forward_audio_to_aux_windows,
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
