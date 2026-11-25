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
import time
import array
import math
from pathlib import Path

gi.require_version("Gst", "1.0")
gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
gi.require_version("Pango", "1.0")

from gi.repository import Gtk, Adw, GLib, Gdk, Gio, Gst, GObject, Pango
from melange.mirror_window import MirrorWindow
from melange.aux_window import AuxVisualizerWindow, update_main_visualizer_palette, reset_all_saved_settings
from melange.mini_visualizer_base import palette_color
from melange.now_playing import NowPlayingWatcher
from melange import projectm_view
from melange.preset_manager import PresetManager
from melange.cursor_hider import FullscreenCursorHider

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

class LineTicker:
    """Scrolls one Now Playing text line (artist/album) that's too long
    to fit: a character ticker like the title's, with the label's width
    fixed while it runs (and the overflow clipped) so the card doesn't
    change width on every tick."""

    SEPARATOR = "   •   "

    def __init__(self, label, get_width_chars, tick_ms):

        self.label = label
        self.get_width_chars = get_width_chars
        self.tick_ms = tick_ms
        self.text = None
        self.timer = None

    def start(self, text):

        self.stop()
        self.text = text

        width = self.get_width_chars()
        scroll_text = text + self.SEPARATOR
        doubled = scroll_text + scroll_text
        offset = [0]

        self.label.set_max_width_chars(-1)
        self.label.set_ellipsize(Pango.EllipsizeMode.NONE)
        self.label.set_overflow(Gtk.Overflow.HIDDEN)

        def tick():
            start = offset[0] % len(scroll_text)
            self.label.set_label(doubled[start:start + width])
            offset[0] += 1
            return True

        # Fix the width to the widest stretch the ticker will ever show,
        # so no later tick can grow the card (a size request is only a
        # minimum).
        attributes = self.label.get_attributes()
        widest = 0

        for start in range(len(scroll_text)):
            layout = self.label.create_pango_layout(doubled[start:start + width])
            layout.set_attributes(attributes)
            widest = max(widest, layout.get_pixel_size()[0])

        self.label.set_size_request(widest, -1)

        tick()

        self.timer = GLib.timeout_add(self.tick_ms, tick)

    def stop(self):

        if self.timer:
            GLib.source_remove(self.timer)
            self.timer = None

        if self.text is not None:
            self.label.set_size_request(-1, -1)
            self.label.set_overflow(Gtk.Overflow.VISIBLE)
            self.label.set_max_width_chars(self.get_width_chars())
            self.label.set_ellipsize(Pango.EllipsizeMode.END)
            self.text = None


class PresetTreeNode(GObject.Object):
    """A folder or a preset in the preset browser's folder tree."""

    __gtype_name__ = "MelangePresetTreeNode"

    def __init__(self, title, preset_name=None, children=None):

        super().__init__()

        self.title = title
        self.preset_name = preset_name  # None for a folder
        self.children = children or []

# Must match the Preferences controls' own defaults (Cycle Interval 30s,
# no jitter, Shuffle on, Transition Blend Time 3s).
DEFAULT_CYCLE_SECONDS = 30.0

# Browse-tree folder for presets opened from files (pinned at the top).
USER_PRESETS_FOLDER = "Your Presets"
DEFAULT_BLEND_SECONDS = 3.0

# current_sink while capturing the microphone instead of a sink monitor.
MICROPHONE_SOURCE = "mic"

# Upper bound on captured audio waiting for the main thread: 0.25s of
# 44.1kHz stereo S16. Anything older is dropped - the visualizers only
# care about recent audio, and the JS PCM queue only keeps 50ms anyway.
MAX_PENDING_AUDIO_BYTES = 44100 * 4 // 4

@Gtk.Template(resource_path="/com/cameronlp/Melange/window.ui")
class MelangeWindow(Adw.ApplicationWindow):

    __gtype_name__ = "MelangeWindow"

    content_box = Gtk.Template.Child()
    toast_overlay = Gtk.Template.Child()
    toolbar_view = Gtk.Template.Child()
    headerbar = Gtk.Template.Child()
    menu_button = Gtk.Template.Child()
    fullscreen_button = Gtk.Template.Child()
    sidebar_button = Gtk.Template.Child()

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
        # Opaque on Hover: while Transparency Mode is on, the content
        # fades back to fully opaque when the pointer is over the
        # visualizer and to Opacity Level when it leaves (the original
        # Hover Transparency behaviour, now optional).
        self.transparency_hover_reveal = False
        self.pointer_over_visualizer = False
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
        # With this off, a track without usable artwork shows no art
        # frame at all instead of the generic placeholder icon.
        self.now_playing_show_art_placeholder = True
        self.now_playing_show_time = True
        self.now_playing_show_background = True
        # now_playing_text_size/now_playing_width are always *derived*
        # (see recompute_now_playing_scale) from now_playing_scale, or
        # from the window's own width when now_playing_lock_to_window
        # is on - never set directly by their own slider anymore, one
        # shared Scale control replaced those alongside Album Art Size.
        self.now_playing_scale = 1.0
        # Fine-tunes on top of Scale / Lock to Window Size: text size
        # and card width as separate percentages (1.0 = unchanged).
        self.now_playing_text_factor = 1.0
        self.now_playing_width_factor = 1.0
        self.now_playing_lock_to_window = False
        self.now_playing_text_size = self.NOW_PLAYING_BASE_TEXT_SIZE
        self.now_playing_font_desc = "Sans"
        self.now_playing_width = self.NOW_PLAYING_BASE_WIDTH
        self.now_playing_scroll_long_titles = True
        self.now_playing_scroll_timer = None
        self.now_playing_scroll_offset = 0
        self.now_playing_scroll_title = None
        # Preferences > Cycling: jump to the next preset when the track
        # playing (Now Playing's source) changes.
        self.change_preset_on_new_song = False
        self.last_song_key = None

        # Preferences > Window: the preset's name in the title bar.
        self.show_preset_in_title = True

        # Now Playing > Style: where the text and background colours come
        # from - "custom" (the colour buttons), "theme" (light/dark), or
        # "main" (the main visualizer's, sampled twice a second, eased).
        self.now_playing_text_source = "custom"
        self.now_playing_bg_source = "custom"

        # Now Playing > Style > Placeholder Icon: "note" or "player".
        self.now_playing_placeholder_style = "note"

        # Now Playing > Style: the card's background (Show Background on).
        self.now_playing_bg_color = Gdk.RGBA()
        self.now_playing_bg_color.parse("#000000")
        self.now_playing_bg_opacity = 0.5

        # Now Playing > Style: outline around the text and placeholder.
        self.now_playing_outline = False
        self.now_playing_outline_color = Gdk.RGBA()
        self.now_playing_outline_color.parse("#ffffff")
        self.now_playing_outline_width = 2.0

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
        # Periodic Fade cycle: hidden -> fade in -> shown -> fade out.
        self.now_playing_periodic_hidden_seconds = 30.0
        self.now_playing_periodic_shown_seconds = 15.0
        self.now_playing_periodic_fade_duration = 2.0
        self.now_playing_periodic_timer = None
        self.now_playing_fade_timer = None
        self.toolbar_hide_delay = 3
        # Preferences > Appearance > Toolbar: the back/forward arrows
        # over the visualizer (also hidden while the preset is locked).
        self.show_nav_arrows = True
        self.show_favorite_button = True

        self.toolbar_view.set_extend_content_to_top_edge(True)
        self.headerbar.add_css_class("melange-header")

        # Visualizer: native MilkDrop rendering with libprojectM
        # (projectm_view.py). libprojectM is built into the flatpak; this
        # only fails outside it (e.g. a host build without libprojectM 4)
        # or without a usable OpenGL 3.3 driver.
        available, reason = projectm_view.availability()

        if available:
            self.projectm_view = projectm_view.ProjectMView(
                on_preset_failed=self.on_projectm_preset_failed
            )
            self.projectm_view.on_beat = self.on_projectm_beat
            self.projectm_view.on_colors = self.on_projectm_colors
            visualizer = self.projectm_view
        else:
            print("Visualizer unavailable:", reason)
            self.projectm_view = None
            visualizer = Gtk.Label(
                label="The visualizer couldn't be started (libprojectM unavailable).",
                wrap=True
            )

        self.visualizer = visualizer

        # The preset-nav arrows used to be part of the page itself
        # (HTML buttons drawn on the canvas) - moved to real GTK
        # widgets, overlaid on top of the visualizer, so a mirror window
        # (mirror_window.py) - which shows only the visualizer widget's
        # own rendered content via Gtk.WidgetPaintable - doesn't also
        # show a pair of arrows that don't do anything there (a
        # Gtk.Picture never forwards input back to a paintable's
        # source, so they'd have been inert, confusing clutter).
        webview_overlay = Gtk.Overlay()

        # Fill the space even with the visualizer hidden (Main Visualizer
        # off) - its expand used to be all that stretched this, so the
        # "Main Visualizer Off" page collapsed into a corner.
        webview_overlay.set_hexpand(True)
        webview_overlay.set_vexpand(True)

        webview_overlay.set_child(visualizer)

        # Dropping .milk files onto the visualizer loads them, same as
        # Load Preset….
        drop_target = Gtk.DropTarget.new(Gdk.FileList, Gdk.DragAction.COPY)
        drop_target.set_propagation_phase(Gtk.PropagationPhase.CAPTURE)
        drop_target.connect("drop", self.on_files_dropped)
        webview_overlay.add_controller(drop_target)

        # Opaque on Hover (Transparency Mode). Only the visualizer area,
        # not the header bar, so moving to the menu doesn't change it.
        hover_motion = Gtk.EventControllerMotion()
        hover_motion.set_propagation_phase(Gtk.PropagationPhase.CAPTURE)
        hover_motion.connect("enter", self.on_visualizer_pointer_enter)
        hover_motion.connect("leave", self.on_visualizer_pointer_leave)
        webview_overlay.add_controller(hover_motion)

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

        paused_icon = Gtk.Image.new_from_icon_name("media-playback-pause-symbolic")
        paused_label = Gtk.Label(label="Paused")
        self.paused_badge = Gtk.Box(spacing=6, halign=Gtk.Align.CENTER, valign=Gtk.Align.START)
        self.paused_badge.set_margin_top(56)
        self.paused_badge.add_css_class("paused-badge")
        self.paused_badge.append(paused_icon)
        self.paused_badge.append(paused_label)
        self.paused_badge.set_can_target(False)
        self.paused_badge.set_visible(False)
        webview_overlay.add_overlay(self.paused_badge)

        # Shown in place of the visualizer while it's turned off (menu >
        # Main Visualizer) to run only the mini visualizers.
        self.main_off_page = Adw.StatusPage(
            icon_name="video-display-symbolic",
            title="Main Visualizer Off",
            description="Mini visualizers keep running. Turning it off saves CPU and GPU.",
        )
        turn_on = Gtk.Button(label="Turn On", halign=Gtk.Align.CENTER)
        turn_on.add_css_class("pill")
        turn_on.add_css_class("suggested-action")
        turn_on.connect(
            "clicked",
            lambda button: self.lookup_action("main-visualizer").change_state(GLib.Variant("b", True))
        )
        self.main_off_page.set_child(turn_on)
        self.main_off_page.add_css_class("main-off-page")
        self.main_off_page.set_visible(False)
        webview_overlay.add_overlay(self.main_off_page)

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

        # Shown instead of hiding the whole art frame whenever Show
        # Artwork is on but there's genuinely no usable art - no
        # mpris:artUrl at all (the common case for a source like a
        # browser tab, which usually has none), or the art failed to
        # download/decode. A plain Gtk.Image (not another Picture/
        # IconPaintable on its own) specifically because Gtk.Image is
        # what actually applies the current foreground/symbolic
        # recoloring automatically - a raw IconPaintable drawn via
        # Picture's generic paintable snapshot does not reliably do
        # that on its own.
        self.now_playing_art_placeholder = Gtk.Image.new_from_icon_name(
            "audio-x-generic-symbolic"
        )
        self.now_playing_art_placeholder.add_css_class("now-playing-art-placeholder")
        self.now_playing_art_placeholder.set_valign(Gtk.Align.CENTER)
        self.now_playing_art_placeholder.set_halign(Gtk.Align.CENTER)
        self.now_playing_art_placeholder.set_visible(False)

        now_playing_art_overlay = Gtk.Overlay()
        now_playing_art_overlay.set_child(self.now_playing_art)
        now_playing_art_overlay.add_overlay(self.now_playing_art_placeholder)

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
        self.now_playing_art_frame.set_child(now_playing_art_overlay)
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

        self.now_playing_artist_ticker = LineTicker(
            self.now_playing_artist_label, lambda: self.now_playing_width,
            self.NOW_PLAYING_SCROLL_TICK_MS
        )
        self.now_playing_album_ticker = LineTicker(
            self.now_playing_album_label, lambda: self.now_playing_width,
            self.NOW_PLAYING_SCROLL_TICK_MS
        )

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
        # content_box (the visualizer's parent). Originally needed because
        # WebKit (the old Butterchurn renderer) claimed button presses
        # for its own hit testing; kept since it also works for the
        # GLArea.
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
        self.gst_bus_handler = None
        self.pending_audio = bytearray()
        self.pending_audio_lock = threading.Lock()
        self.audio_flush_scheduled = False
        self.current_sink = None
        self.pinned_sink = None
        self.pactl_event_timer = None
        self.preset_locked = False
        self.paused = False
        self.main_visualizer_on = True
        self.preset_list_store = None
        self.preset_browser = None
        self.browser_view_stack = None
        self.favorites = self.load_favorites()
        self.favorites_list_store = None
        # Presets loaded from files (Load Preset…/drag and drop) - for
        # this session only, like before; the files may move or vanish.
        self.user_loaded_presets = []
        self.queue_list_store = None
        self.playlists = self.load_playlists()
        self.mirror_windows = []
        self.aux_windows = {}
        self.current_preset_name = None
        self.current_playlist_name = None

        # Preset navigation (history, queue, shuffle, auto-cycle) - see
        # preset_manager.py. The catalog is projectM's bundled collection
        # (preset_paths maps each name to its ZipPreset/Path).
        self.presets = PresetManager(
            show=self.show_preset,
            on_queue_changed=lambda queue: self.on_preset_queue_changed()
        )
        self.presets.set_cycle_interval(DEFAULT_CYCLE_SECONDS)
        self.preset_paths = projectm_view.preset_catalog() if self.projectm_view else {}
        self.presets.set_catalog(sorted(self.preset_paths))
        self.restore_user_presets()
        self.presets.set_active(True)
        self.presets.next()

        # Never reused, even as mirrors close - so "Mirror 2" still
        # means the same window it always did rather than shifting
        # around as other mirrors come and go.
        self.next_mirror_number = 1

        self.connect(
            "close-request",
            self.on_close_request
        )

        self.start_system_audio()


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
        # the visualizer - which is virtually the whole window.
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
        # the whole AdwToolbarView, header bar strip AND the visualizer
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

        # Scrolling over the visualizer changes preset. Attached to the
        # visualizer area only (not the whole window), so scrolling in
        # the sidebar's lists and settings scrolls them instead. CAPTURE
        # so the overlaid buttons/Now Playing card don't swallow it.
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

        webview_overlay.add_controller(scroll_controller)





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
        # just calling them straight from the button handlers) so
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

        # Pause (P): freeze the visualizer on its current frame - no
        # rendering, audio ignored, no preset changes. Next/previous
        # resume it.
        # F9 (GNOME's usual sidebar shortcut): show/hide the sidebar.
        toggle_sidebar = Gio.SimpleAction.new("toggle-sidebar", None)
        toggle_sidebar.connect(
            "activate",
            lambda *args: self.sidebar_split.set_show_sidebar(
                not self.sidebar_split.get_show_sidebar()
            )
        )
        self.add_action(toggle_sidebar)

        # Main Visualizer on/off: off hides and pauses projectM (no
        # rendering, no audio into it, no preset changes) while audio
        # still reaches the mini visualizers.
        main_visualizer_action = Gio.SimpleAction.new_stateful(
            "main-visualizer", None, GLib.Variant("b", True)
        )
        main_visualizer_action.connect("change-state", self.main_visualizer_changed)
        self.add_action(main_visualizer_action)

        pause_action = Gio.SimpleAction.new_stateful(
            "pause", None, GLib.Variant("b", False)
        )
        pause_action.connect("change-state", self.pause_changed)
        self.add_action(pause_action)

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
        self.build_settings_pages()
        self.build_preset_browser()
        self.build_sidebar()

        # Pointer disappears after a moment's stillness in fullscreen -
        # only over the visualizer, not while using the sidebar or a menu.
        # The floating back/forward and favorite buttons go at the same
        # moment as the pointer (moving the mouse brings both back).
        self.cursor_hider = FullscreenCursorHider(
            self,
            may_hide=lambda: self.pointer_over_visualizer and not self.menu_button.get_active(),
            on_hide=self.on_pointer_hidden
        )

        self.menu_button.get_popover().add_child(
            self.build_theme_selector(),
            "theme-selector"
        )

        # Remembered between launches (settings.json): put back what was
        # saved, then start saving changes. Reset All Preferences still
        # goes to the real defaults (captured before this, in
        # preference_defaults).
        self.settings_save_timer = None
        self.restore_settings()
        self.watch_settings()

    # Only exists for Now Playing's Lock to Window Size - there's no
    # notify::default-width/height to bind to for a *live* interactive
    # resize in GTK4 (those properties only reflect the initially
    # requested size), so this is the real, always-correct hook.
    # Cheap no-op when the toggle is off, which is the common case.
    def do_size_allocate(self, width, height, baseline):

        Adw.ApplicationWindow.do_size_allocate(self, width, height, baseline)

        if self.now_playing_lock_to_window:
            self.recompute_now_playing_scale()

    def toolbar_enter(self, controller, x, y):

        self.mouse_over_toolbar = True

        if self.hide_timer:

            GLib.source_remove(
                self.hide_timer
            )

            self.hide_timer = None

        self.toolbar_view.set_reveal_top_bars(True)
        self.set_nav_arrows_visible(True)



    def toolbar_leave(self, controller):

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
            "Controls hidden - right-click to show them"
            if self.immersive_mode_enabled
            else "Controls shown"
        )

    # The only way out of Immersive Mode once it's on (see
    # immersive_mode_changed's own comment on the action registration)
    # - does nothing while the mode is off, so this gesture has no
    # effect on ordinary right-clicks.
    def on_content_right_click(self, gesture, n_press, x, y):

        # Everyday options, wherever the controls are (and the way back
        # when Hide Controls is on: it shows checked here).
        menu = Gio.Menu()

        presets = Gio.Menu()
        presets.append("Next Preset", "win.next-preset")
        presets.append("Previous Preset", "win.previous-preset")
        presets.append("Favorite", "win.toggle-favorite")
        menu.append_section(None, presets)

        playback = Gio.Menu()
        playback.append("Pause", "win.pause")
        playback.append("Lock Preset", "win.lock-preset")
        playback.append("Shuffle", "win.shuffle-preset")
        menu.append_section(None, playback)

        view = Gio.Menu()
        view.append("Hide Controls", "win.immersive-mode")
        view.append("Transparency Mode", "win.transparency-mode")
        view.append("Now Playing", "win.now-playing-enabled")
        view.append("Fullscreen", "win.toggle-fullscreen")
        menu.append_section(None, view)

        more = Gio.Menu()
        more.append("Browse Presets…", "win.browse-presets")
        more.append("Load Preset…", "win.load-preset")
        more.append("Preferences…", "win.preferences")
        menu.append_section(None, more)

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
        # Detach it once closed (after the chosen item has run), or every
        # right-click would leave another popover attached.
        popover.connect("closed", lambda p: GLib.idle_add(p.unparent))
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
    # The content opacity Transparency Mode should be heading for right
    # now: fully opaque when the mode is off, or when Opaque on Hover is
    # on and the pointer is over the visualizer; Opacity Level otherwise.
    def transparency_target_opacity(self):

        if not self.transparency_mode_enabled:
            return 1.0

        if self.transparency_hover_reveal and self.pointer_over_visualizer:
            return 1.0

        return self.transparency_opacity

    def on_visualizer_pointer_enter(self, controller, x, y):

        self.pointer_over_visualizer = True

        if self.transparency_mode_enabled and self.transparency_hover_reveal:
            self.animate_opacity(self.transparency_target_opacity())

    def on_visualizer_pointer_leave(self, controller):

        self.pointer_over_visualizer = False

        if self.transparency_mode_enabled and self.transparency_hover_reveal:
            self.animate_opacity(self.transparency_target_opacity())

    def transparency_hover_reveal_changed(self, enabled):

        self.transparency_hover_reveal = enabled

        if self.transparency_mode_enabled:
            self.animate_opacity(self.transparency_target_opacity())

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

        self.animate_opacity(self.transparency_target_opacity())

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
            self.now_playing_artist_ticker.stop()
            self.now_playing_album_ticker.stop()
            return

        song_key = (info["title"], info["artist"])

        if (
            self.change_preset_on_new_song
            and self.last_song_key is not None
            and song_key != self.last_song_key
            and info["title"]
            and not self.preset_locked
        ):
            self.next_preset(None)

        self.last_song_key = song_key

        self.update_now_playing_title(info["title"])
        self.update_now_playing_line(self.now_playing_artist_ticker, info["artist"])
        self.update_now_playing_line(self.now_playing_album_ticker, info["album"])

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

    def now_playing_show_art_placeholder_changed(self, enabled):

        self.now_playing_show_art_placeholder = enabled

        if self.now_playing_info is not None:
            self.load_now_playing_art(self.now_playing_info["art_url"])

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

        for row in getattr(self, "now_playing_bg_rows", ()):
            row.set_visible(enabled)

        if hasattr(self, "now_playing_update_match_rows"):
            self.now_playing_update_match_rows()

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

        self.now_playing_text_size = (
            self.NOW_PLAYING_BASE_TEXT_SIZE * scale * self.now_playing_text_factor
        )
        self.now_playing_width = max(
            10, round(self.NOW_PLAYING_BASE_WIDTH * scale * self.now_playing_width_factor)
        )

        self.apply_now_playing_text_style()
        self.apply_now_playing_width()

        # The width/size change may have pushed the current title
        # across the "too long to fit" threshold either way, and a
        # running ticker's frame/window was sized for the old size.
        self.refresh_now_playing_title()

    def now_playing_scale_changed(self, value):

        self.now_playing_scale = value
        self.recompute_now_playing_scale()

    def now_playing_text_factor_changed(self, value):

        self.now_playing_text_factor = value
        self.recompute_now_playing_scale()

    def now_playing_width_factor_changed(self, value):

        self.now_playing_width_factor = value
        self.recompute_now_playing_scale()

    def build_now_playing_text_size_control(self):

        row = self.build_slider_row(
            "Text Size",
            0.5, 2.0, 0.05, self.now_playing_text_factor,
            lambda value: f"{round(value * 100)}%",
            self.now_playing_text_factor_changed,
            store_as="now_playing_text_size_scale"
        )
        row.set_tooltip_text("Text size on top of Scale")

        return row

    def build_now_playing_max_width_control(self):

        row = self.build_slider_row(
            "Max Width",
            0.5, 3.0, 0.05, self.now_playing_width_factor,
            lambda value: f"{round(value * 100)}%",
            self.now_playing_width_factor_changed,
            store_as="now_playing_max_width_scale"
        )
        row.set_tooltip_text("How wide the text can get before it's shortened or scrolls, on top of Scale")

        return row

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
        self.refresh_now_playing_title()

    # max-width-chars, not a literal pixel width - matches how this
    # card was already sized (set_ellipsize + set_max_width_chars),
    # and scales naturally with Text Size/Font rather than fighting
    # them the way a fixed pixel width would.
    def apply_now_playing_width(self):

        self.now_playing_time_label.set_max_width_chars(self.now_playing_width)

        # Like the title below: a line that's currently scrolling keeps
        # its ticker's unlimited width (refresh_now_playing_lines
        # restarts it at the new size).
        for ticker in (self.now_playing_artist_ticker, self.now_playing_album_ticker):
            if ticker.text is None:
                ticker.label.set_max_width_chars(self.now_playing_width)

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

        # Never smaller than the text column would be with all four
        # lines (title, artist, album, time) showing at the current
        # text size - otherwise hiding fields shrinks the art with them.
        line = self.now_playing_title_label.create_pango_layout("Ag")
        line.set_attributes(self.now_playing_title_label.get_attributes())
        _, line_height = line.get_pixel_size()
        minimum = 4 * line_height + 3 * self.now_playing_text_box.get_spacing()

        size = max(24, natural, minimum)

        self.now_playing_art_frame.set_size_request(size, size)
        self.now_playing_art_placeholder.set_pixel_size(max(12, int(size * 0.5)))

    def now_playing_scroll_long_titles_changed(self, enabled):

        self.now_playing_scroll_long_titles = enabled

        if self.now_playing_info is not None:
            self.update_now_playing_title(self.now_playing_info["title"])
            self.refresh_now_playing_lines()

    # Artist/album: scroll when too long (same Scroll Long Text setting
    # and fit test as the title), otherwise show statically, shortened
    # with "…" if needed.
    def update_now_playing_line(self, ticker, text):

        text = text or ""

        if self.now_playing_scroll_long_titles and text and not self.now_playing_title_fits(text):
            if text != ticker.text:
                ticker.start(text)
        else:
            ticker.stop()
            ticker.label.set_label(text)

    def refresh_now_playing_lines(self):

        if self.now_playing_info is None:
            return

        for ticker, key in (
            (self.now_playing_artist_ticker, "artist"),
            (self.now_playing_album_ticker, "album"),
        ):
            ticker.stop()
            self.update_now_playing_line(ticker, self.now_playing_info[key])

    # Only (re)starts the scroll if the title actually changed from
    # whatever's currently scrolling - on_now_playing_changed can fire
    # repeatedly for the same title (a chatty player resending
    # unrelated property changes), and restarting the animation from
    # scratch every time would make it visibly stutter/reset instead
    # of scrolling smoothly.
    def update_now_playing_title(self, title):

        title = title or "Unknown Title"

        if self.now_playing_scroll_long_titles and not self.now_playing_title_fits(title):

            if title != self.now_playing_scroll_title:
                self.start_now_playing_title_scroll(title)

        else:
            self.stop_now_playing_title_scroll()
            self.now_playing_title_label.set_label(title)
            self.apply_now_playing_title_frame_width()

    # Whether the full title fits in the card's text width, judged by
    # the real rendered width in the current font and size. (It used to
    # compare len(title) with now_playing_width, a character count -
    # but the width limit is "N average characters" wide, so titles
    # with narrow letters scrolled even though they'd fit.) Uses the
    # same per-character width GTK uses for max-width-chars: the wider
    # of the font's approximate character and digit widths.
    def now_playing_title_fits(self, title):

        label = self.now_playing_title_label

        layout = label.create_pango_layout(title)
        layout.set_attributes(label.get_attributes())
        text_width, _ = layout.get_pixel_size()

        font_desc = Pango.FontDescription.from_string(self.now_playing_font_desc)
        font_desc.set_size(int(self.now_playing_text_size * Pango.SCALE))
        metrics = label.get_pango_context().get_metrics(font_desc, None)
        char_width = max(
            metrics.get_approximate_char_width(),
            metrics.get_approximate_digit_width()
        ) / Pango.SCALE

        return text_width <= char_width * self.now_playing_width

    # Re-decides scroll vs. static after the text size, card width or
    # font changes (Scale, Lock to Window Size, Font) - previously only
    # a new title did, so a title that started scrolling kept scrolling
    # at a larger size. Only acts when something actually changed, so
    # window resizes with Lock to Window Size on don't keep restarting
    # a running ticker.
    def refresh_now_playing_title(self):

        key = (self.now_playing_width, self.now_playing_text_size, self.now_playing_font_desc)

        if key == getattr(self, "now_playing_title_layout_key", None):
            return

        self.now_playing_title_layout_key = key

        if self.now_playing_info is None:
            return

        self.stop_now_playing_title_scroll()
        self.update_now_playing_title(self.now_playing_info["title"])
        self.refresh_now_playing_lines()

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

    # Opacity at `elapsed` seconds into a Periodic Fade cycle:
    # hidden for H -> fade in over F -> shown for S -> fade out over F,
    # repeating. Returns (opacity, seconds until the next phase starts).
    # Starts hidden, so turning it on waits before the card appears.
    @staticmethod
    def now_playing_periodic_opacity(elapsed, hidden, shown, fade):

        def ease(t):
            return t * t * (3 - 2 * t)  # smoothstep: no abrupt start/stop

        phases = (
            (hidden, lambda t: 0.0),
            (fade, lambda t: ease(t)),
            (shown, lambda t: 1.0),
            (fade, lambda t: 1.0 - ease(t)),
        )
        cycle = sum(length for length, _ in phases)

        if cycle <= 0:
            return 1.0, None

        position = elapsed % cycle

        for length, opacity in phases:
            if position < length:
                return opacity(position / length), length - position
            position -= length

        return 0.0, 0.0

    def restart_now_playing_periodic_fade(self):

        if self.now_playing_periodic_timer:
            GLib.source_remove(self.now_playing_periodic_timer)
            self.now_playing_periodic_timer = None

        if not self.now_playing_periodic_fade:
            self.now_playing_box.set_opacity(1.0)
            return

        start_time = time.monotonic()

        def tick():

            opacity, remaining = self.now_playing_periodic_opacity(
                time.monotonic() - start_time,
                self.now_playing_periodic_hidden_seconds,
                self.now_playing_periodic_shown_seconds,
                self.now_playing_periodic_fade_duration,
            )

            self.now_playing_box.set_opacity(opacity)

            # Smooth steps only while fading; during the hidden/shown
            # holds (which can last minutes) sleep until the next phase.
            if 0.0 < opacity < 1.0 or remaining is None:
                delay = self.NOW_PLAYING_PERIODIC_TICK_MS
            else:
                delay = max(self.NOW_PLAYING_PERIODIC_TICK_MS, int(remaining * 1000))

            self.now_playing_periodic_timer = GLib.timeout_add(delay, tick)

            return False

        tick()

    def now_playing_periodic_fade_changed(self, enabled):

        self.now_playing_periodic_fade = enabled
        self.restart_now_playing_periodic_fade()

    def now_playing_periodic_hidden_changed(self, value):

        self.now_playing_periodic_hidden_seconds = value
        self.restart_now_playing_periodic_fade()

    def now_playing_periodic_shown_changed(self, value):

        self.now_playing_periodic_shown_seconds = value
        self.restart_now_playing_periodic_fade()

    def now_playing_periodic_fade_duration_changed(self, value):

        self.now_playing_periodic_fade_duration = value
        self.restart_now_playing_periodic_fade()

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

        color, bg = self.now_playing_effective_colors()

        # The no-artwork icon takes the same color as the text.
        if not hasattr(self, "now_playing_placeholder_css"):
            self.now_playing_placeholder_css = Gtk.CssProvider()
            Gtk.StyleContext.add_provider_for_display(
                self.get_display(), self.now_playing_placeholder_css,
                Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION + 1
            )
        # Text Color goes through CSS (not a Pango foreground attribute):
        # GTK draws text shadows in an attribute's color, so with an
        # attribute the Text Outline came out in the text color instead
        # of the outline color.
        css = (
            f".now-playing-art-placeholder {{ color: {color.to_string()}; }}"
            f" .now-playing-text {{ color: {color.to_string()}; }}"
        )

        css += (
            f" .now-playing-card {{ background-color: rgba({round(bg.red * 255)},"
            f" {round(bg.green * 255)}, {round(bg.blue * 255)}, {self.now_playing_bg_opacity:.2f}); }}"
        )

        # Text Outline: a ring of hard-edged shadows around the text and
        # the placeholder icon (meme-caption style), so it reads over any
        # preset. GTK's CSS has no stroke, but 16 offset shadows look
        # the same at these widths.
        if self.now_playing_outline:
            width = self.now_playing_outline_width
            outline = self.now_playing_outline_color.to_string()
            shadows = ", ".join(
                f"{width * math.cos(step * math.pi / 8):.2f}px "
                f"{width * math.sin(step * math.pi / 8):.2f}px 0 {outline}"
                for step in range(16)
            )
            css += (
                # Room for the outline, which would otherwise be clipped at
                # each line's edges.
                f" .now-playing-outlined .now-playing-text {{ text-shadow: {shadows};"
                f" padding: {math.ceil(width)}px; }}"
                f" .now-playing-outlined .now-playing-art-placeholder {{ -gtk-icon-shadow: {shadows}; }}"
            )
            self.now_playing_box.add_css_class("now-playing-outlined")
        else:
            self.now_playing_box.remove_css_class("now-playing-outlined")

        self.now_playing_placeholder_css.load_from_string(css)

        for label in (
            self.now_playing_title_label,
            self.now_playing_artist_label,
            self.now_playing_album_label,
            self.now_playing_time_label
        ):
            label.set_attributes(attrs)
            label.add_css_class("now-playing-text")

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

    # Requested - a source like a browser tab typically has no
    # mpris:artUrl at all, so Show Artwork being on used to just leave
    # an empty gap where the art frame would be rather than showing
    # anything. Also reused for a real art_url that fails to download
    # or decode, so every "no usable art" case looks the same.
    # Now Playing > Style > Placeholder Icon = Player's Icon: the
    # player's own icon if the sandbox can see it (apps installed
    # normally - Flatpak players' icons aren't visible to Melange),
    # else one for the kind of source, else the music note.
    PLACEHOLDER_BROWSERS = (
        "firefox", "mozilla", "chrom", "brave", "epiphany", "webkit", "vivaldi",
        "opera", "edge", "librewolf", "zen", "floorp", "waterfox",
    )
    PLACEHOLDER_VIDEO_PLAYERS = (
        "vlc", "totem", "mpv", "celluloid", "videos", "clapper", "showtime",
        "haruna", "smplayer", "kodi", "mplayer", "dragon",
    )

    def now_playing_placeholder_icon(self):

        default = "audio-x-generic-symbolic"

        if self.now_playing_placeholder_style != "player":
            return default

        info = self.now_playing_info or {}
        theme = Gtk.IconTheme.get_for_display(self.get_display())
        entry = (info.get("desktop_entry") or "").removesuffix(".desktop")

        for name in (f"{entry}-symbolic", entry, f"{entry.lower()}-symbolic", entry.lower()):
            if entry and theme.has_icon(name):
                return name

        text = f"{entry} {info.get('identity', '')}".lower()

        if any(word in text for word in self.PLACEHOLDER_BROWSERS):
            return "web-browser-symbolic"
        if any(word in text for word in self.PLACEHOLDER_VIDEO_PLAYERS):
            return "video-x-generic-symbolic"
        if not info.get("length_us"):
            # No length: usually a live stream or internet radio.
            return "network-wireless-symbolic"

        return default

    def show_now_playing_art_placeholder(self):

        self.now_playing_art.set_paintable(None)
        self.now_playing_art_placeholder.set_from_icon_name(self.now_playing_placeholder_icon())

        if not self.now_playing_show_art_placeholder:
            self.now_playing_art_frame.set_visible(False)
            return

        self.now_playing_art_placeholder.set_visible(True)
        self.now_playing_art_frame.set_visible(True)

    # Guards against a slow/late art fetch for a track that's since
    # been skipped past clobbering whatever's already showing - each
    # call gets a fresh token, and the async callback only applies its
    # result if it's still the most recent one requested.
    def load_now_playing_art(self, art_url):

        self.now_playing_art_token += 1
        token = self.now_playing_art_token

        if not self.now_playing_show_artwork:
            self.now_playing_art_frame.set_visible(False)
            return

        if not art_url:
            self.show_now_playing_art_placeholder()
            return

        def on_loaded(source, result):

            if token != self.now_playing_art_token:
                return

            if not self.now_playing_show_artwork:
                return

            try:
                ok, contents, etag = source.load_contents_finish(result)
            except GLib.Error:
                self.show_now_playing_art_placeholder()
                return

            try:
                texture = Gdk.Texture.new_from_bytes(GLib.Bytes.new(contents))
            except GLib.Error:
                self.show_now_playing_art_placeholder()
                return

            self.now_playing_art_placeholder.set_visible(False)
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

        self.hide_timer = None

        if self.mouse_over_toolbar:
            return False

        if self.menu_open:
            return False

        self.toolbar_view.set_reveal_top_bars(False)
        self.set_nav_arrows_visible(False)

        return False

    def set_nav_arrows_visible(self, visible):

        css_class = "nav-arrow-visible"

        # Each stays hidden when turned off in Preferences, and all three
        # while the preset is locked (requested: a locked view shows no
        # floating buttons; F still toggles the favorite).
        for arrow in (self.prev_arrow_button, self.next_arrow_button):
            arrow.set_visible(self.show_nav_arrows and not self.preset_locked and self.main_visualizer_on)

        self.favorite_button.set_visible(
            self.show_favorite_button and not self.preset_locked and self.main_visualizer_on
        )

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

        # Not over a button (Turn On on the Main Visualizer Off page,
        # the floating buttons...): this runs before the button sees the
        # press, so a window move here used to swallow its click.
        target = widget.pick(x, y, Gtk.PickFlags.DEFAULT)
        if target is not None and (
            isinstance(target, Gtk.Button) or target.get_ancestor(Gtk.Button) is not None
        ):
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

        self.save_settings()

        # Mirror windows have no purpose without this one driving them
        # (a mirror shows this window's own visualizer - see
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

    def on_projectm_colors(self, pixels):

        # Only worth working out while something uses it: a mini
        # visualizer, or Now Playing matching the main visualizer.
        matching = "main" in (self.now_playing_text_source, self.now_playing_bg_source)

        if self.aux_windows or matching:
            update_main_visualizer_palette(pixels)

        if matching:
            self.apply_now_playing_text_style()

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

    # Mirror Windows group in the sidebar's Visualizers section (was a
    # submenu of the main menu). Called whenever mirrors open or close.
    def rebuild_mirror_windows_menu(self):

        group = getattr(self, "mirror_group", None)

        if group is None:
            return

        for row in self.mirror_rows:
            group.remove(row)

        self.mirror_rows = []

        for mirror in self.mirror_windows:
            row = Adw.ActionRow(title=f"Mirror {mirror.mirror_number}")

            show = Gtk.Button(icon_name="window-new-symbolic")
            show.add_css_class("flat")
            show.set_valign(Gtk.Align.CENTER)
            show.set_tooltip_text("Show")
            show.connect("clicked", lambda b, m=mirror: m.present())
            row.add_suffix(show)

            close = Gtk.Button(icon_name="window-close-symbolic")
            close.add_css_class("flat")
            close.set_valign(Gtk.Align.CENTER)
            close.set_tooltip_text("Close")
            close.connect("clicked", lambda b, m=mirror: m.close())
            row.add_suffix(close)

            self.mirror_rows.append(row)

        # Open mirrors go between New Mirror Window and Close All.
        group.remove(self.close_mirrors_row)

        for row in self.mirror_rows:
            group.add(row)

        group.add(self.close_mirrors_row)
        self.close_mirrors_row.set_sensitive(bool(self.mirror_windows))

    def on_key_pressed(self, controller, keyval, keycode, state):

        # Esc closes the sidebar first (then leaves fullscreen).
        if keyval == Gdk.KEY_Escape and self.sidebar_split.get_show_sidebar():
            self.sidebar_split.set_show_sidebar(False)
            return True

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

    def on_pointer_hidden(self):
        """Fullscreen, pointer just hid: the floating buttons and the
        header bar go at the same moment (rather than the header
        waiting for Hide Delay)."""

        self.set_nav_arrows_visible(False)

        if self.is_fullscreen() and not self.mouse_over_toolbar and not self.menu_open:
            if self.hide_timer:
                GLib.source_remove(self.hide_timer)
                self.hide_timer = None
            self.toolbar_view.set_reveal_top_bars(False)

    def main_visualizer_changed(self, action, value):

        action.set_state(value)
        self.main_visualizer_on = value.get_boolean()

        if self.projectm_view is not None:
            view = self.projectm_view
            view.set_paused(self.paused or not self.main_visualizer_on)
            # Hidden = no frames drawn at all. Pausing normally waits to
            # grab one last frame to freeze on, which a hidden view never
            # draws - so don't wait (it kept asking for redraws), and grab
            # a fresh one when it comes back still paused.
            view.freeze_pending = self.main_visualizer_on and self.paused
            view.set_visible(self.main_visualizer_on)
            view.queue_render()

        self.main_off_page.set_visible(not self.main_visualizer_on)
        self.paused_badge.set_visible(self.paused and self.main_visualizer_on)
        self.presets.set_cycle_paused(self.preset_locked or self.paused or not self.main_visualizer_on)
        self.set_nav_arrows_visible(self.toolbar_view.get_reveal_top_bars())

    def pause_changed(self, action, value):

        action.set_state(value)
        self.paused = value.get_boolean()

        if self.projectm_view is not None:
            self.projectm_view.set_paused(self.paused or not self.main_visualizer_on)

        self.presets.set_cycle_paused(self.preset_locked or self.paused or not self.main_visualizer_on)
        self.paused_badge.set_visible(self.paused and self.main_visualizer_on)

        self.show_toast("Paused" if self.paused else "Resumed")

    def resume_if_paused(self):

        if self.paused:
            self.lookup_action("pause").change_state(GLib.Variant("b", False))

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

        self.presets.set_cycle_paused(self.preset_locked or self.paused or not self.main_visualizer_on)
        self.set_nav_arrows_visible(self.toolbar_view.get_reveal_top_bars())

        self.show_toast(
            "Preset locked" if self.preset_locked else "Preset unlocked"
        )

    def shuffle_preset_changed(self, action, value):

        action.set_state(value)

        enabled = value.get_boolean()

        self.presets.set_shuffle(enabled)

        self.show_toast(
            "Shuffle on" if enabled else "Shuffle off"
        )

    def loop_queue_changed(self, action, value):

        action.set_state(value)

        enabled = value.get_boolean()

        self.presets.set_queue_loop(enabled)

        self.show_toast(
            "Queue loop on" if enabled else "Queue loop off"
        )

    def shuffle_queue_changed(self, action, value):

        action.set_state(value)

        enabled = value.get_boolean()

        self.presets.set_queue_shuffle(enabled)

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
            self.presets.set_cycle_interval,
            store_as="cycle_interval_scale"
        )

    def build_cycle_jitter_control(self):

        def format_cycle_jitter(value):
            return "None" if value <= 0 else f"±{int(value)}%"

        return self.build_slider_row(
            "Cycle Interval Jitter",
            0.0, 50.0, 5.0, 0.0,
            format_cycle_jitter,
            self.presets.set_cycle_jitter,
            store_as="cycle_jitter_scale"
        )

    def build_blend_time_control(self):

        def format_blend_time(value):
            return "Instant" if value <= 0 else f"{value:.1f}s"

        return self.build_slider_row(
            "Transition Time",
            0.0, 10.0, 0.5, 3.0,
            format_blend_time,
            self.set_blend_time,
            store_as="blend_time_scale"
        )

    def build_mesh_size_control(self):

        def format_mesh_size(value):
            return f"{int(value)}x{int(value * 0.75)}"

        # Held to a fixed 4:3 ratio rather than exposing width/height
        # separately; 48x36 by default (projectM per-vertex mesh).
        return self.build_slider_row(
            "Mesh Size",
            8.0, 128.0, 1.0, 48.0,
            format_mesh_size,
            lambda value: self.projectm_view and self.projectm_view.set_mesh_size(value),
            store_as="mesh_size_scale"
        )

    def build_framerate_control(self):

        # 10-60 FPS, then one step past 60 = Uncapped (it used to jump
        # from 59 straight to Uncapped).
        def format_framerate(value):
            return "Uncapped" if value >= 61 else f"{int(value)} FPS"

        def framerate_changed(value):

            # The slider's top end means "uncapped" (render on every
            # animation frame), same "boundary value is the special
            # state" shape as the cycle interval's "off" at its bottom.
            fps = 0 if value >= 61 else value

            if self.projectm_view:
                self.projectm_view.set_frame_rate_cap(fps)

        return self.build_slider_row(
            "Framerate",
            10.0, 61.0, 1.0, 61.0,
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

            # Opaque while hovered with Opaque on Hover on, so the new
            # level applies once the pointer leaves.
            self.toast_overlay.set_opacity(self.transparency_target_opacity())

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

        row = self.build_slider_row(
            "Fade Speed",
            0.0, 2.0, 0.1, 0.0,
            format_transparency_fade,
            lambda value: setattr(self, "transparency_fade_ms", value * 1000),
            store_as="transparency_fade_scale"
        )
        row.set_tooltip_text(
            "How long the fade takes when Transparency Mode turns on or off, "
            "and with Opaque on Hover when the mouse enters or leaves"
        )

        return row

    def build_transparency_hover_reveal_control(self):

        row = self.build_toggle_row(
            "Opaque on Hover",
            self.transparency_hover_reveal,
            self.transparency_hover_reveal_changed,
            store_as="transparency_hover_reveal_row"
        )
        row.set_subtitle("Fully visible while the mouse is over the visualizer")

        return row

    # Bound via action-name (like Transparency Mode's own Enabled row)
    # rather than build_toggle_row now that this is a real action -
    # stays in sync with the hamburger menu item automatically in both
    # directions, no extra state to keep them agreeing.
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

    def build_now_playing_show_art_placeholder_control(self):

        row = self.build_toggle_row(
            "Show Placeholder",
            self.now_playing_show_art_placeholder,
            self.now_playing_show_art_placeholder_changed
        )
        row.set_subtitle("Show an icon when a track has no artwork")

        return row

    def build_now_playing_show_artwork_control(self):

        return self.build_toggle_row(
            "Show Artwork", True, self.now_playing_show_artwork_changed
        )

    def build_now_playing_show_time_control(self):

        return self.build_toggle_row(
            "Show Playback Time", True, self.now_playing_show_time_changed
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
            self.build_now_playing_show_art_placeholder_control(),
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

    @staticmethod
    def format_duration(value):

        value = int(round(value))

        if value < 60:
            return f"{value}s"

        minutes, seconds = divmod(value, 60)

        return f"{minutes} min {seconds}s" if seconds else f"{minutes} min"

    # Hidden For / Shown For go up to 5 minutes, so their sliders are
    # squared: the slider position p means p^2 seconds - fine steps for
    # short times, coarse ones for long (just under half is 0-1 min).
    PERIODIC_MAX_SECONDS = 300

    @staticmethod
    def periodic_seconds(position):

        return float(round(position * position))

    def build_now_playing_periodic_fade_controls(self):

        top = math.sqrt(self.PERIODIC_MAX_SECONDS)

        return [
            self.build_slider_row(
                "Hidden For",
                0.0, top, 0.05, math.sqrt(self.now_playing_periodic_hidden_seconds),
                lambda position: self.format_duration(self.periodic_seconds(position)),
                lambda position: self.now_playing_periodic_hidden_changed(self.periodic_seconds(position)),
                store_as="now_playing_periodic_hidden_scale"
            ),
            self.build_slider_row(
                "Shown For",
                1.0, top, 0.05, math.sqrt(self.now_playing_periodic_shown_seconds),
                lambda position: self.format_duration(self.periodic_seconds(position)),
                lambda position: self.now_playing_periodic_shown_changed(self.periodic_seconds(position)),
                store_as="now_playing_periodic_shown_scale"
            ),
            self.build_slider_row(
                "Fade Duration",
                0.2, 10.0, 0.1, self.now_playing_periodic_fade_duration,
                lambda value: f"{value:.1f}s",
                self.now_playing_periodic_fade_duration_changed,
                store_as="now_playing_periodic_fade_duration_scale"
            ),
        ]

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
        for control in self.build_now_playing_periodic_fade_controls():
            row.add_row(control)

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
            "Scroll Long Text",
            True,
            self.now_playing_scroll_long_titles_changed
        )

    def build_now_playing_placeholder_style_control(self):

        styles = [("note", "Music Note"), ("player", "Player's Icon")]
        row = Adw.ComboRow(
            title="Placeholder Icon",
            subtitle="Shown when a track has no artwork",
            model=Gtk.StringList.new([label for _, label in styles]),
        )

        def changed(combo, param):
            self.now_playing_placeholder_style = styles[combo.get_selected()][0]
            if self.now_playing_art_placeholder.get_visible():
                self.show_now_playing_art_placeholder()

        row.connect("notify::selected", changed)
        self.now_playing_placeholder_style_row = row

        return row

    NOW_PLAYING_COLOR_SOURCES = [
        ("custom", "Chosen Color"), ("theme", "System Theme"), ("main", "Main Visualizer"),
    ]

    def now_playing_effective_colors(self):
        """(text, background) to draw with, from each one's source. When
        either follows something (theme, visualizer), text that would be
        too close to the background switches to white or black."""

        def rgba(rgb):
            color = Gdk.RGBA()
            color.red, color.green, color.blue, color.alpha = (*rgb, 1.0)
            return color

        def luminance(color):
            return 0.2126 * color.red + 0.7152 * color.green + 0.0722 * color.blue

        dark_theme = Adw.StyleManager.get_default().get_dark()

        bg = self.now_playing_bg_color
        if self.now_playing_bg_source == "theme":
            bg = rgba((0.12, 0.12, 0.13) if dark_theme else (0.97, 0.97, 0.97))
        elif self.now_playing_bg_source == "main":
            bg = rgba(tuple(c * 0.6 for c in palette_color("main", 0.0)))

        text = self.now_playing_text_color
        if self.now_playing_text_source == "theme":
            text = rgba((1.0, 1.0, 1.0) if dark_theme else (0.12, 0.12, 0.13))
        elif self.now_playing_text_source == "main":
            text = rgba(palette_color("main", 1.0))

        # Keep it readable whenever either colour isn't one you picked -
        # e.g. your white text on a background following a light theme
        # flips to black (and back in dark mode).
        follows = self.now_playing_text_source != "custom" or self.now_playing_bg_source != "custom"
        if follows and abs(luminance(text) - luminance(bg)) < 0.45:
            text = rgba((1.0, 1.0, 1.0) if luminance(bg) < 0.5 else (0.0, 0.0, 0.0))

        return text, bg

    def build_now_playing_color_source_controls(self):

        keys = [key for key, _ in self.NOW_PLAYING_COLOR_SOURCES]
        labels = [label for _, label in self.NOW_PLAYING_COLOR_SOURCES]

        def update_rows():
            self.now_playing_text_color_row.set_visible(self.now_playing_text_source == "custom")
            self.now_playing_bg_rows[0].set_visible(
                self.now_playing_show_background and self.now_playing_bg_source == "custom"
            )
            bg_row.set_visible(self.now_playing_show_background)

        def text_changed(combo, param):
            self.now_playing_text_source = keys[combo.get_selected()]
            update_rows()
            self.apply_now_playing_text_style()

        def bg_changed(combo, param):
            self.now_playing_bg_source = keys[combo.get_selected()]
            update_rows()
            self.apply_now_playing_text_style()

        text_row = Adw.ComboRow(title="Text Follows", model=Gtk.StringList.new(labels))
        text_row.set_subtitle("Also the placeholder icon")
        text_row.connect("notify::selected", text_changed)

        bg_row = Adw.ComboRow(title="Background Follows", model=Gtk.StringList.new(labels))
        bg_row.connect("notify::selected", bg_changed)
        bg_row.set_visible(self.now_playing_show_background)

        self.now_playing_text_source_row = text_row
        self.now_playing_bg_source_row = bg_row
        self.now_playing_update_match_rows = update_rows

        # System Theme follows light/dark as it changes.
        Adw.StyleManager.get_default().connect(
            "notify::dark", lambda *args: self.apply_now_playing_text_style()
        )

        return [text_row, bg_row]

    def build_now_playing_background_controls(self):
        """Color and opacity of the card behind the text (shown while
        Displayed Fields > Show Background is on)."""

        color_row = Adw.ActionRow(title="Background Color")
        button = Gtk.ColorDialogButton(dialog=Gtk.ColorDialog(with_alpha=False))
        button.set_valign(Gtk.Align.CENTER)
        button.set_rgba(self.now_playing_bg_color)

        def color_changed(widget, param):
            self.now_playing_bg_color = widget.get_rgba()
            self.apply_now_playing_text_style()

        button.connect("notify::rgba", color_changed)
        color_row.add_suffix(button)
        self.now_playing_bg_color_button = button

        def opacity_changed(value):
            self.now_playing_bg_opacity = value
            self.apply_now_playing_text_style()

        opacity_row = self.build_slider_row(
            "Background Opacity", 0.0, 1.0, 0.05, self.now_playing_bg_opacity,
            lambda value: f"{value * 100:.0f}%", opacity_changed,
            store_as="now_playing_bg_opacity_scale"
        )

        self.now_playing_bg_rows = (color_row, opacity_row)

        for row in self.now_playing_bg_rows:
            row.set_visible(self.now_playing_show_background)

        return [color_row, opacity_row]

    def build_now_playing_outline_controls(self):

        def outline_changed(enabled):
            self.now_playing_outline = enabled
            color_row.set_visible(enabled)
            width_row.set_visible(enabled)
            self.apply_now_playing_text_style()

        toggle = self.build_toggle_row(
            "Text Outline", self.now_playing_outline, outline_changed,
            store_as="now_playing_outline_row"
        )
        toggle.set_subtitle("An outline around the text and icon, for any background")

        color_row = Adw.ActionRow(title="Outline Color")
        button = Gtk.ColorDialogButton(dialog=Gtk.ColorDialog())
        button.set_valign(Gtk.Align.CENTER)
        button.set_rgba(self.now_playing_outline_color)

        def color_changed(widget, param):
            self.now_playing_outline_color = widget.get_rgba()
            self.apply_now_playing_text_style()

        button.connect("notify::rgba", color_changed)
        color_row.add_suffix(button)
        self.now_playing_outline_color_button = button

        def width_changed(value):
            self.now_playing_outline_width = value
            self.apply_now_playing_text_style()

        width_row = self.build_slider_row(
            "Outline Width", 1.0, 4.0, 0.5, self.now_playing_outline_width,
            lambda value: f"{value:g} px", width_changed,
            store_as="now_playing_outline_width_scale"
        )

        color_row.set_visible(self.now_playing_outline)
        width_row.set_visible(self.now_playing_outline)

        return [toggle, color_row, width_row]

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
        self.now_playing_text_color_row = row

        return row

    # 1-30 s, with the slider's right end (TOOLBAR_HIDE_NEVER) meaning
    # Never - a longer delay leads to "never" (it used to be the left
    # end, 0). Internally toolbar_hide_delay <= 0 still means never.
    TOOLBAR_HIDE_NEVER = 31.0

    def build_toolbar_hide_delay_control(self):

        def format_toolbar_hide_delay(value):
            return "Never" if value >= self.TOOLBAR_HIDE_NEVER else f"{int(value)}s"

        def toolbar_hide_delay_changed(value):
            self.toolbar_hide_delay = 0 if value >= self.TOOLBAR_HIDE_NEVER else value
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
            1.0, self.TOOLBAR_HIDE_NEVER, 1.0, 3.0,
            format_toolbar_hide_delay,
            toolbar_hide_delay_changed,
            store_as="toolbar_hide_delay_scale"
        )

    def build_show_nav_arrows_control(self):

        def show_nav_arrows_changed(enabled):
            self.show_nav_arrows = enabled
            self.set_nav_arrows_visible(self.toolbar_view.get_reveal_top_bars())

        row = self.build_toggle_row(
            "Show Navigation Arrows",
            self.show_nav_arrows,
            show_nav_arrows_changed,
            store_as="show_nav_arrows_row"
        )
        row.set_subtitle("Back and forward buttons over the visualizer")

        return row

    def build_show_favorite_button_control(self):

        def show_favorite_button_changed(enabled):
            self.show_favorite_button = enabled
            self.set_nav_arrows_visible(self.toolbar_view.get_reveal_top_bars())

        row = self.build_toggle_row(
            "Show Favorite Button",
            self.show_favorite_button,
            show_favorite_button_changed,
            store_as="show_favorite_button_row"
        )
        row.set_subtitle("Star button in the visualizer's corner (F also toggles)")

        return row

    def build_change_on_new_song_control(self):

        def changed(enabled):
            self.change_preset_on_new_song = enabled

        row = self.build_toggle_row(
            "New Preset on Song Change", self.change_preset_on_new_song, changed,
            store_as="change_on_new_song_row"
        )
        row.set_subtitle("Uses the track Now Playing follows; not while locked")

        return row

    def build_show_preset_in_title_control(self):

        def changed(enabled):
            self.show_preset_in_title = enabled
            self.set_title(self.build_window_title())

        row = self.build_toggle_row(
            "Show Preset Name in Title", self.show_preset_in_title, changed,
            store_as="show_preset_in_title_row"
        )
        row.set_subtitle("Off shows just \"Melange\"")

        return row

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
            lambda value: self.projectm_view and self.projectm_view.set_render_scale(value),
            store_as="render_scale_scale"
        )

    # Beat-Driven Cycle uses projectM's own beat detection ("hard cuts"):
    # a preset change when the volume jumps by more than Beat Threshold,
    # at most once per Minimum Time Between Cuts.
    def build_beat_cycle_control(self):

        return self.build_toggle_row(
            "Beat-Driven Cycle",
            False,
            lambda enabled: self.projectm_view and self.projectm_view.set_beat_cuts(enabled=enabled),
            store_as="beat_cycle_row"
        )

    def build_beat_threshold_control(self):

        def format_beat_threshold(value):
            return f"{value:.1f}"

        return self.build_slider_row(
            "Beat Threshold",
            0.5, 5.0, 0.1, 2.0,
            format_beat_threshold,
            lambda value: self.projectm_view and self.projectm_view.set_beat_cuts(threshold=value),
            store_as="beat_threshold_scale"
        )

    def build_beat_min_time_control(self):

        def format_beat_min_time(value):
            return f"{int(value)}s"

        return self.build_slider_row(
            "Minimum Time Between Cuts",
            1.0, 60.0, 1.0, 10.0,
            format_beat_min_time,
            lambda value: self.projectm_view and self.projectm_view.set_beat_cuts(min_seconds=value),
            store_as="beat_min_time_scale"
        )

    def theme_button_toggled(self, button, scheme):

        if button.get_active():
            Adw.StyleManager.get_default().set_color_scheme(scheme)
            self.schedule_settings_save()

    # --- remembered settings -------------------------------------------------

    # Menu toggles worth keeping between launches. Not Pause, Lock,
    # Hide Controls, Transparency Mode or Main Visualizer: those are
    # moment-to-moment states, and starting up in them would confuse.
    REMEMBERED_ACTIONS = ("shuffle-preset", "now-playing-enabled", "loop-queue", "shuffle-queue")
    SETTINGS_SAVE_DELAY_MS = 1000
    SETTINGS_VERSION = 2

    def settings_file_path(self):

        config_dir = Path(GLib.get_user_config_dir()) / "melange"
        config_dir.mkdir(parents=True, exist_ok=True)

        return config_dir / "settings.json"

    def keyed_preference_controls(self):
        """(key, widget, getter, setter) for each Preferences control,
        keyed by page and row title so saved values find their control
        again even if the layout changes."""

        keyed = []
        seen = {}

        for widget, getter, setter in self.preference_controls():
            row = widget if isinstance(widget, Adw.PreferencesRow) else widget.get_ancestor(Adw.PreferencesRow)
            page = next(
                (name for name, page in self.settings_pages if widget.is_ancestor(page)),
                "?"
            )
            key = f"{page}/{row.get_title() if row else type(widget).__name__}"
            seen[key] = seen.get(key, 0) + 1
            if seen[key] > 1:
                key = f"{key}#{seen[key]}"
            keyed.append((key, widget, getter, setter))

        return keyed

    @staticmethod
    def encode_preference(value):

        if isinstance(value, Gdk.RGBA):
            return value.to_string()
        if isinstance(value, Pango.FontDescription):
            return value.to_string()
        return value

    @staticmethod
    def decode_preference(saved, current):
        """Saved JSON value -> the current value's type, or None."""

        if isinstance(current, Gdk.RGBA):
            color = Gdk.RGBA()
            return color if isinstance(saved, str) and color.parse(saved) else None
        if isinstance(current, Pango.FontDescription):
            return Pango.FontDescription.from_string(saved) if isinstance(saved, str) else None
        if isinstance(current, bool):
            return saved if isinstance(saved, bool) else None
        if isinstance(current, int):
            return int(saved) if isinstance(saved, (int, float)) and not isinstance(saved, bool) else None
        if isinstance(current, float):
            return float(saved) if isinstance(saved, (int, float)) and not isinstance(saved, bool) else None
        return None

    def restore_settings(self):

        try:
            with open(self.settings_file_path()) as f:
                saved = json.load(f)
        except (OSError, ValueError):
            return

        if not isinstance(saved, dict):
            return

        preferences = saved.get("preferences", {})

        # Version 2: Hidden For / Shown For sliders became squared (their
        # position is the square root of the seconds) - convert older
        # files' plain seconds so the times stay the same.
        if isinstance(preferences, dict) and saved.get("version", 1) < 2:
            for key in ("now-playing/Hidden For", "now-playing/Shown For"):
                value = preferences.get(key)
                if isinstance(value, (int, float)) and not isinstance(value, bool) and value >= 0:
                    preferences[key] = math.sqrt(value)

        if isinstance(preferences, dict):
            for key, widget, getter, setter in self.keyed_preference_controls():
                if key in preferences:
                    value = self.decode_preference(preferences[key], getter())
                    if value is not None:
                        setter(value)

        actions = saved.get("actions", {})
        if isinstance(actions, dict):
            for name in self.REMEMBERED_ACTIONS:
                action = self.lookup_action(name)
                value = actions.get(name)
                if action is not None and isinstance(value, bool) and action.get_state().get_boolean() != value:
                    action.change_state(GLib.Variant("b", value))

        theme = saved.get("theme")
        button = getattr(self, "theme_buttons", {}).get(theme) if isinstance(theme, int) else None
        if button is not None:
            button.set_active(True)

        size = saved.get("window_size")
        if isinstance(size, list) and len(size) == 2 and all(isinstance(n, int) and n > 0 for n in size):
            self.set_default_size(*size)
        if saved.get("maximized") is True:
            self.maximize()

    def watch_settings(self):

        for key, widget, getter, setter in self.keyed_preference_controls():
            if isinstance(widget, Gtk.Scale):
                widget.get_adjustment().connect("value-changed", self.schedule_settings_save)
            elif isinstance(widget, Gtk.ColorDialogButton):
                widget.connect("notify::rgba", self.schedule_settings_save)
            elif isinstance(widget, Gtk.FontDialogButton):
                widget.connect("notify::font-desc", self.schedule_settings_save)
            elif isinstance(widget, (Adw.ComboRow, Gtk.DropDown)):
                widget.connect("notify::selected", self.schedule_settings_save)
            elif isinstance(widget, Adw.SpinRow):
                widget.connect("notify::value", self.schedule_settings_save)
            else:
                widget.connect("notify::active", self.schedule_settings_save)

        for name in self.REMEMBERED_ACTIONS:
            action = self.lookup_action(name)
            if action is not None:
                action.connect("notify::state", self.schedule_settings_save)

    def schedule_settings_save(self, *args):

        if self.settings_save_timer is not None:
            GLib.source_remove(self.settings_save_timer)

        self.settings_save_timer = GLib.timeout_add(self.SETTINGS_SAVE_DELAY_MS, self.save_settings)

    def save_settings(self):

        if getattr(self, "settings_save_timer", None) is not None:
            GLib.source_remove(self.settings_save_timer)
        self.settings_save_timer = None

        data = {
            "version": self.SETTINGS_VERSION,
            "preferences": {
                key: self.encode_preference(getter())
                for key, widget, getter, setter in self.keyed_preference_controls()
            },
            "actions": {
                name: self.lookup_action(name).get_state().get_boolean()
                for name in self.REMEMBERED_ACTIONS if self.lookup_action(name) is not None
            },
            "theme": int(Adw.StyleManager.get_default().get_color_scheme()),
            "maximized": self.is_maximized(),
        }

        if not (self.is_maximized() or self.is_fullscreen()) and self.get_width() > 0:
            data["window_size"] = [self.get_width(), self.get_height()]
        else:
            # Keep the last normal size while maximized/fullscreen.
            try:
                with open(self.settings_file_path()) as f:
                    previous = json.load(f)
                if isinstance(previous, dict) and "window_size" in previous:
                    data["window_size"] = previous["window_size"]
            except (OSError, ValueError):
                pass

        try:
            GLib.file_set_contents(str(self.settings_file_path()), json.dumps(data, indent=1).encode())
        except (OSError, GLib.Error) as e:
            print("Failed to save settings:", e)

        return False

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

            if not hasattr(self, "theme_buttons"):
                self.theme_buttons = {}
            self.theme_buttons[int(scheme)] = button

            button.connect(
                "toggled",
                self.theme_button_toggled,
                scheme
            )

            box.append(button)

        return box

    def preferences_clicked(self, action, param):

        self.show_sidebar_section("settings")

    def build_settings_pages(self):

        audio_group = Adw.PreferencesGroup(title="Audio")
        audio_group.add(self.build_audio_source_control())
        audio_group.add(self.build_sensitivity_control())

        cycling_group = Adw.PreferencesGroup(title="Changing Presets")
        cycling_group.add(self.build_cycle_interval_control())
        cycling_group.add(self.build_cycle_jitter_control())
        cycling_group.add(self.build_blend_time_control())
        cycling_group.add(self.build_change_on_new_song_control())

        self.cycling_group = cycling_group

        beat_group = Adw.PreferencesGroup(
            title="Beat-Driven Cycle",
            description=(
                "Change preset on strong beats, using projectM's own beat "
                "detection. A higher threshold needs a bigger jump in "
                "volume."
            )
        )
        beat_group.add(self.build_beat_cycle_control())
        beat_group.add(self.build_beat_threshold_control())
        beat_group.add(self.build_beat_min_time_control())

        # Turned on/off from the main menu (Ctrl+T); this is its tuning.
        transparency_group = Adw.PreferencesGroup(
            title="Transparency Mode",
            description=(
                "Turn on from the main menu (Ctrl+T). The header bar always "
                "stays fully visible."
            )
        )
        transparency_group.add(self.build_transparency_opacity_control())
        transparency_group.add(self.build_transparency_hover_reveal_control())
        transparency_group.add(self.build_transparency_fade_control())

        # Now Playing has its own page (it used to be one 12-row group on
        # Appearance), split by what you're adjusting.
        np_content_group = Adw.PreferencesGroup(
            title="Content",
            description="Turn Now Playing on from the main menu."
        )
        np_content_group.add(self.build_now_playing_source_control())
        np_content_group.add(self.build_now_playing_displayed_fields_control())
        np_content_group.add(self.build_now_playing_scroll_long_titles_control())

        np_layout_group = Adw.PreferencesGroup(title="Layout")
        np_layout_group.add(self.build_now_playing_placement_control())
        np_layout_group.add(self.build_now_playing_lock_to_window_control())
        np_layout_group.add(self.build_now_playing_scale_control())
        np_layout_group.add(self.build_now_playing_text_size_control())
        np_layout_group.add(self.build_now_playing_max_width_control())

        np_style_group = Adw.PreferencesGroup(title="Style")
        # Order: font; text colour (where it comes from, then your own);
        # outline; background (where it comes from, then colour, opacity);
        # placeholder icon. The "Follows" rows need the colour rows built
        # first, so they're built first and placed above them.
        font_row = self.build_now_playing_font_control()
        text_color_row = self.build_now_playing_text_color_control()
        outline_rows = self.build_now_playing_outline_controls()
        background_rows = self.build_now_playing_background_controls()
        text_source_row, bg_source_row = self.build_now_playing_color_source_controls()

        for row in (
            font_row, text_source_row, text_color_row, *outline_rows,
            bg_source_row, *background_rows,
            self.build_now_playing_placeholder_style_control(),
        ):
            np_style_group.add(row)

        np_visibility_group = Adw.PreferencesGroup(title="Visibility")
        np_visibility_group.add(self.build_now_playing_auto_hide_control())
        np_visibility_group.add(self.build_now_playing_periodic_fade_control())

        toolbar_group = Adw.PreferencesGroup(
            title="Controls",
            description=(
                "The header bar and the buttons over the visualizer hide "
                "after the mouse stops moving. In fullscreen the buttons "
                "also go with the pointer, and they're hidden while the "
                "preset is locked."
            )
        )
        toolbar_group.add(self.build_toolbar_hide_delay_control())
        toolbar_group.add(self.build_show_nav_arrows_control())
        toolbar_group.add(self.build_show_favorite_button_control())
        toolbar_group.add(self.build_show_preset_in_title_control())

        # When presets change (not the presets themselves).
        playback_page = Adw.PreferencesPage(
            title="Cycling",
            icon_name="media-playlist-shuffle-symbolic"
        )

        playback_page.add(cycling_group)
        playback_page.add(beat_group)

        appearance_page = Adw.PreferencesPage(
            title="Window",
            icon_name="preferences-desktop-appearance-symbolic"
        )

        appearance_page.add(toolbar_group)
        appearance_page.add(transparency_group)

        now_playing_page = Adw.PreferencesPage(
            title="Now Playing",
            icon_name="audio-x-generic-symbolic"
        )

        for group in (np_content_group, np_layout_group, np_style_group, np_visibility_group):
            now_playing_page.add(group)

        rendering_group = Adw.PreferencesGroup(title="Rendering")
        rendering_group.add(self.build_mesh_size_control())
        rendering_group.add(self.build_framerate_control())
        rendering_group.add(self.build_render_scale_control())

        # What drives and draws the visuals - Audio and Rendering used to
        # be two separate, very short pages.
        visualizer_page = Adw.PreferencesPage(
            title="Audio & Rendering",
            icon_name="audio-speakers-symbolic"
        )

        visualizer_page.add(audio_group)
        visualizer_page.add(rendering_group)

        profiles_page = self.build_profiles_page()

        # More than one page here is what gives the dialog its top
        # view-switcher (rather than a single flat list) for free.
        # Theme (light/dark/system) lives in the hamburger menu itself
        # instead - see build_theme_selector.
        # Shown in the sidebar's Preferences section (build_sidebar) rather
        # than a dialog, so the visualizer stays visible while changing
        # things.
        self.settings_pages = [
            ("visualizer", visualizer_page),
            ("presets", playback_page),
            ("window", appearance_page),
            ("now-playing", now_playing_page),
            ("profiles", profiles_page),
        ]

        # Starting values of every Preferences control (Reset All
        # Preferences puts them back), and the button for it.
        self.preference_defaults = self.snapshot_preferences()

        reset_group = Adw.PreferencesGroup()
        reset_button = Gtk.Button(label="Reset All Preferences…", halign=Gtk.Align.CENTER)
        reset_button.add_css_class("pill")
        reset_button.add_css_class("destructive-action")
        reset_button.connect("clicked", lambda button: self.confirm_reset_preferences())
        reset_group.add(reset_button)
        profiles_page.add(reset_group)

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

        return [
            ("sensitivity", *scale_field(self.sensitivity_scale)),
            ("cycle_interval", *scale_field(self.cycle_interval_scale)),
            ("cycle_jitter", *scale_field(self.cycle_jitter_scale)),
            ("blend_time", *scale_field(self.blend_time_scale)),
            ("mesh_size", *scale_field(self.mesh_size_scale)),
            ("framerate", *scale_field(self.framerate_scale)),
            ("render_scale", *scale_field(self.render_scale_scale)),
            # Beat settings use new keys: the old Butterchurn beat
            # detector's values (a 1.1-3.0 multiplier, a cooldown) don't
            # mean the same thing for projectM's beat cuts. Keys from
            # older profiles (anti_aliasing, beat_mode, ...) are ignored.
            ("beat_cycle", *switch_field(self.beat_cycle_row)),
            ("beat_threshold", *scale_field(self.beat_threshold_scale)),
            ("beat_min_time", *scale_field(self.beat_min_time_scale)),
        ]

    def current_profile_values(self):

        return {
            key: getter()
            for key, getter, setter in self.profile_fields()
        }

    def apply_profile_values(self, values):

        # Setting a widget's value re-fires its own existing
        # value-changed/notify::active/notify::selected handler, which
        # already applies it to the visualizer - so this reuses the same
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

        self.show_sidebar_section("presets", tab="presets")

    # --- sidebar: presets and settings beside the visualizer -------------

    # The window's content (the toolbar view with the visualizer) moves
    # into an Adw.OverlaySplitView with this sidebar on the right. It
    # pushes the visualizer aside rather than covering it; windows
    # narrower than SIDEBAR_COLLAPSE_WIDTH fall back to overlaying it,
    # and below SIDEBAR_FULL_WIDTH it fills the window.
    SIDEBAR_COLLAPSE_WIDTH = 720
    SIDEBAR_FULL_WIDTH = 500

    def build_sidebar(self):

        content = self.get_content()
        self.set_content(None)

        split = Adw.OverlaySplitView()
        split.set_sidebar_position(Gtk.PackType.END)
        split.set_show_sidebar(False)
        # By default the split view hides the sidebar when it collapses
        # (and shows it on uncollapsing) - so shrinking the window made
        # an open sidebar vanish. Pinned, only the toggle/shortcuts/
        # close button show or hide it.
        split.set_pin_sidebar(True)
        split.set_min_sidebar_width(300)
        split.set_max_sidebar_width(420)
        split.set_sidebar_width_fraction(0.35)
        split.set_content(content)
        self.set_content(split)
        self.sidebar_split = split

        # Presets | Visualizers | Preferences
        sections = Adw.ViewStack()
        sections.add_titled_with_icon(
            self.preset_browser, "presets", "Presets", "view-list-symbolic"
        )
        sections.add_titled_with_icon(
            self.build_visualizers_section(), "visualizers", "Visualizers",
            "video-display-symbolic"
        )
        sections.add_titled_with_icon(
            self.build_settings_section(), "settings", "Preferences",
            "preferences-system-symbolic"
        )
        self.sidebar_sections = sections

        # Top-level sections in a bottom switcher bar (icons + labels),
        # the usual GNOME pattern for narrow views; the tabs *within* a
        # section (Presets/Favorites/Queue/Playlists, settings pages)
        # stay at the top.
        header = Adw.HeaderBar()

        # Where you are: the section name, or in Preferences the page.
        sidebar_title = Adw.WindowTitle()
        header.set_title_widget(sidebar_title)

        def update_sidebar_title(*args):
            section = sections.get_visible_child_name()
            stack = sections if section != "settings" else self.settings_stack
            page = stack.get_page(stack.get_visible_child())
            sidebar_title.set_title(page.get_title() if page else "")

        sections.connect("notify::visible-child", update_sidebar_title)
        self.settings_stack.connect("notify::visible-child", update_sidebar_title)
        update_sidebar_title()

        section_switcher = Adw.ViewSwitcherBar(stack=sections)
        section_switcher.set_reveal(True)

        # The sidebar toggle shows pressed while the sidebar is open; it
        # reopens on whichever section was last used (Presets the first
        # time). Ctrl+F / Q / Ctrl+, jump straight to a section instead.
        split.bind_property(
            "show-sidebar", self.sidebar_button, "active",
            GObject.BindingFlags.BIDIRECTIONAL | GObject.BindingFlags.SYNC_CREATE
        )

        sidebar = Adw.ToolbarView()
        sidebar.add_top_bar(header)
        sidebar.add_bottom_bar(section_switcher)
        sidebar.set_content(sections)
        split.set_sidebar(sidebar)

        # While the sidebar is open, the sidebar toggle and main menu move
        # into the sidebar's header - the same widgets - on its left, in
        # the same order, with the window buttons alone on its right;
        # closing it moves them back. So there's one of each, and the
        # toggle doubles as the sidebar's close button. Fullscreen stays
        # at the far left of the visualizer's header: it's about the
        # visualizer, not the sidebar.
        self.sidebar_header = header
        # Left to right: sidebar toggle, main menu (GNOME puts the main
        # menu last, before the window buttons).
        right_to_left = (self.menu_button, self.sidebar_button)

        def place_header_buttons(*args):
            open_ = split.get_show_sidebar()
            source, target = (
                (self.headerbar, header) if open_ else (header, self.headerbar)
            )

            if all(b.get_ancestor(Adw.HeaderBar) is target for b in right_to_left):
                pass
            elif open_:
                for button in reversed(right_to_left):
                    source.remove(button)
                    target.pack_start(button)
            else:
                for button in right_to_left:
                    source.remove(button)
                    target.pack_end(button)

            self.headerbar.set_show_end_title_buttons(not open_)
            header.set_show_end_title_buttons(open_)

        # Deferred: the toggle can be the button that was just clicked.
        split.connect(
            "notify::show-sidebar",
            lambda *args: GLib.idle_add(lambda: (place_header_buttons(), False)[1])
        )
        place_header_buttons()

        breakpoint = Adw.Breakpoint.new(Adw.BreakpointCondition.parse(
            f"max-width: {self.SIDEBAR_COLLAPSE_WIDTH}sp"
        ))
        breakpoint.add_setter(split, "collapsed", True)
        self.add_breakpoint(breakpoint)

        # Very small windows: the sidebar takes the whole window, like a
        # page (its close button / the toggle goes back). Added last, so
        # it wins over the one above when both match.
        full_width = Adw.Breakpoint.new(Adw.BreakpointCondition.parse(
            f"max-width: {self.SIDEBAR_FULL_WIDTH}sp"
        ))
        full_width.add_setter(split, "collapsed", True)
        full_width.add_setter(split, "min-sidebar-width", 0.0)
        full_width.add_setter(split, "max-sidebar-width", 100000.0)
        full_width.add_setter(split, "sidebar-width-fraction", 1.0)
        self.add_breakpoint(full_width)

    # Mini visualizer windows (aux_window.py), one switch each - bound to
    # the same win.show-* actions, so they always match what's open.
    # Was a Mini Visualizers submenu in the main menu.
    VISUALIZER_ROWS = (
        ("Meters", (
            ("VU Meter", "show-vu-meter"),
            ("Peak Meter", "show-peak-meter"),
        )),
        ("Scopes", (
            ("X-Y Scope", "show-xy-scope"),
            ("Oscilloscope", "show-oscilloscope"),
            ("Vector Scope", "show-vectorscope"),
        )),
        ("Spectrum", (
            ("Spectrum", "show-spectrum"),
            ("Spectrogram", "show-spectrogram"),
            ("3D Terrain Spectrogram", "show-terrain"),
            ("3D Waterfall", "show-waterfall"),
        )),
        ("Fun", (
            ("DVD Bounce", "show-dvd-bounce"),
            ("Pipes", "show-pipes"),
        )),
    )

    def build_visualizers_section(self):

        page = Adw.PreferencesPage()

        # Mirror windows: extra windows showing the same visualizer.
        mirror_group = Adw.PreferencesGroup(
            title="Mirror Windows",
            description="Extra windows showing the same visualizer, e.g. on another screen."
        )

        new_row = Adw.ActionRow(title="New Mirror Window", activatable=True)
        new_row.add_suffix(Gtk.Image.new_from_icon_name("list-add-symbolic"))
        new_row.connect("activated", lambda r: self.activate_action("win.new-mirror-window", None))
        mirror_group.add(new_row)

        close_row = Adw.ActionRow(title="Close All Mirrors", activatable=True)
        close_row.connect("activated", lambda r: self.activate_action("win.close-all-mirrors", None))
        mirror_group.add(close_row)

        self.mirror_group = mirror_group
        self.close_mirrors_row = close_row
        self.mirror_rows = []
        self.rebuild_mirror_windows_menu()
        page.add(mirror_group)

        intro = Adw.PreferencesGroup(
            title="Mini Visualizers",
            description="Small extra windows that react to the same audio."
        )
        page.add(intro)

        for title, rows in self.VISUALIZER_ROWS:
            group = Adw.PreferencesGroup(title=title)

            for label, action in rows:
                group.add(Adw.SwitchRow(title=label, action_name=f"win.{action}"))

            page.add(group)

        reset_group = Adw.PreferencesGroup()
        reset_button = Gtk.Button(label="Reset All Mini Visualizers…", halign=Gtk.Align.CENTER)
        reset_button.add_css_class("pill")
        reset_button.add_css_class("destructive-action")
        reset_button.connect("clicked", lambda button: self.confirm_reset_mini_visualizers())
        reset_group.add(reset_button)
        page.add(reset_group)

        return page

    # --- resets --------------------------------------------------------------

    # Controls a reset leaves alone (a device choice, not a preference).
    RESET_SKIP_TITLES = ("Audio Source",)

    def preference_controls(self):
        """(widget, getter, setter) for every control on the Preferences
        pages except Profiles."""

        def walk(widget):
            yield widget
            child = widget.get_first_child()
            while child is not None:
                yield from walk(child)
                child = child.get_next_sibling()

        def color_copy(rgba):
            copy = Gdk.RGBA()
            copy.red, copy.green, copy.blue, copy.alpha = rgba.red, rgba.green, rgba.blue, rgba.alpha
            return copy

        controls = []

        for name, page in self.settings_pages:
            if name == "profiles":
                continue

            for widget in walk(page):
                row = widget.get_ancestor(Adw.PreferencesRow)
                if row is not None and row.get_title() in self.RESET_SKIP_TITLES:
                    continue

                if isinstance(widget, Adw.SwitchRow):
                    controls.append((widget, widget.get_active, widget.set_active))
                elif isinstance(widget, Adw.ComboRow):
                    controls.append((widget, widget.get_selected, widget.set_selected))
                elif isinstance(widget, Adw.SpinRow):
                    controls.append((widget, widget.get_value, widget.set_value))
                elif isinstance(widget, Gtk.Switch) and widget.get_ancestor(Adw.SwitchRow) is None:
                    controls.append((widget, widget.get_active, widget.set_active))
                elif isinstance(widget, Gtk.Scale):
                    adjustment = widget.get_adjustment()
                    controls.append((widget, adjustment.get_value, adjustment.set_value))
                elif isinstance(widget, Gtk.SpinButton) and widget.get_ancestor(Adw.SpinRow) is None:
                    controls.append((widget, widget.get_value, widget.set_value))
                elif isinstance(widget, Gtk.DropDown) and widget.get_ancestor(Adw.ComboRow) is None:
                    controls.append((widget, widget.get_selected, widget.set_selected))
                elif isinstance(widget, Gtk.ColorDialogButton):
                    controls.append((widget, lambda w=widget: color_copy(w.get_rgba()), widget.set_rgba))
                elif isinstance(widget, Gtk.FontDialogButton):
                    controls.append((widget, lambda w=widget: w.get_font_desc().copy() if w.get_font_desc() else None,
                                     lambda value, w=widget: value is not None and w.set_font_desc(value)))

        return controls

    def snapshot_preferences(self):

        return [(widget, setter, getter()) for widget, getter, setter in self.preference_controls()]

    def reset_preferences(self):

        for widget, setter, value in self.preference_defaults:
            setter(value)

        self.show_toast("Preferences reset")

    def confirm_reset(self, heading, body, label, on_reset):

        dialog = Adw.AlertDialog(heading=heading, body=body)
        dialog.add_response("cancel", "Cancel")
        dialog.add_response("reset", label)
        dialog.set_response_appearance("reset", Adw.ResponseAppearance.DESTRUCTIVE)
        dialog.set_default_response("cancel")
        dialog.set_close_response("cancel")
        dialog.connect("response", lambda d, response: on_reset() if response == "reset" else None)
        dialog.present(self)

    def confirm_reset_preferences(self):

        self.confirm_reset(
            "Reset All Preferences?",
            "Every setting on the Preferences tabs goes back to its default. "
            "Saved Profiles and the audio source aren't changed.",
            "Reset", self.reset_preferences
        )

    def reset_mini_visualizers(self):

        reset_all_saved_settings()

        for aux_window in list(self.aux_windows.values()):
            aux_window.reset_to_defaults()

        self.show_toast("Mini visualizers reset")

    def confirm_reset_mini_visualizers(self):

        self.confirm_reset(
            "Reset All Mini Visualizers?",
            "Every mini visualizer's settings and remembered size go back to "
            "their defaults, including ones that aren't open.",
            "Reset", self.reset_mini_visualizers
        )

    def build_settings_section(self):

        stack = Adw.ViewStack()

        for name, page in self.settings_pages:
            stack.add_titled_with_icon(page, name, page.get_title(), page.get_icon_name())

        switcher = Adw.InlineViewSwitcher(stack=stack)
        switcher.set_display_mode(Adw.InlineViewSwitcherDisplayMode.ICONS)
        switcher.set_homogeneous(True)
        switcher.set_margin_start(12)
        switcher.set_margin_end(12)
        switcher.set_margin_top(6)

        # Icons only fit the sidebar's width; the current page's name is
        # shown in the sidebar's header (build_sidebar) rather than in a
        # row of its own, which crowded the tabs and cost vertical space.
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        box.append(switcher)
        box.append(stack)
        stack.set_vexpand(True)

        self.settings_stack = stack

        return box

    # Opens the sidebar on a section (and preset tab); asking for the
    # section that's already showing closes it again, so the header's
    # Presets button and the shortcuts work as toggles.
    def show_sidebar_section(self, section, tab=None):

        split = self.sidebar_split
        already_showing = (
            split.get_show_sidebar()
            and self.sidebar_sections.get_visible_child_name() == section
            and (tab is None or self.browser_view_stack.get_visible_child_name() == tab)
        )

        if already_showing:
            split.set_show_sidebar(False)
            return

        self.sidebar_sections.set_visible_child_name(section)

        if tab is not None:
            self.browser_view_stack.set_visible_child_name(tab)

        split.set_show_sidebar(True)

    def build_preset_browser(self):

        view_stack = Adw.ViewStack()

        view_stack.add_titled_with_icon(
            self.build_presets_tab(),
            "presets", "Browse", "view-list-symbolic"
        )
        view_stack.add_titled_with_icon(
            self.build_favorites_tab(),
            "favorites", "Favorites", "starred-symbolic"
        )
        view_stack.add_titled_with_icon(
            self.build_queue_tab(),
            "queue", "Queue", "view-continuous-symbolic"
        )
        # Replaced the Loaded tab: presets loaded from files already
        # appear in the Presets tree's "Your Presets" folder and in search.
        view_stack.add_titled_with_icon(
            self.build_playlists_tab(),
            "playlists", "Playlists", "view-list-bullet-symbolic"
        )

        switcher = Adw.InlineViewSwitcher(stack=view_stack)
        switcher.set_display_mode(Adw.InlineViewSwitcherDisplayMode.LABELS)
        switcher.set_homogeneous(True)
        switcher.set_margin_start(12)
        switcher.set_margin_end(12)
        switcher.set_margin_top(6)

        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        box.append(switcher)
        box.append(view_stack)
        view_stack.set_vexpand(True)

        self.preset_browser = box
        self.browser_view_stack = view_stack

    def build_preset_search_list(self, list_store, with_tree=False):

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

        # The Presets tab can also show the active renderer's presets
        # as a folder tree (projectM's collection is organised in
        # Category/Subfolder folders); the flat list is still used for
        # searching and for renderers without folders.
        if with_tree:
            page_stack = Gtk.Stack()
            page_stack.add_named(scrolled, "flat")
            page_stack.add_named(self.build_preset_tree_view(), "tree")
            page_stack.set_vexpand(True)

            self.preset_page_stack = page_stack
            self.preset_search_entry = search_entry

            search_entry.connect(
                "search-changed",
                lambda entry: self.update_preset_browser_page()
            )

            scrolled = page_stack

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

        box = self.build_preset_search_list(self.preset_list_store, with_tree=True)
        self.refresh_preset_tree()

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

    def build_preset_tree_view(self):

        self.preset_tree_root = Gio.ListStore(item_type=PresetTreeNode)

        def create_children(node):

            if not node.children:
                return None

            store = Gio.ListStore(item_type=PresetTreeNode)
            store.splice(0, 0, node.children)

            return store

        tree_model = Gtk.TreeListModel.new(
            self.preset_tree_root, False, False, create_children
        )

        factory = Gtk.SignalListItemFactory()
        factory.connect("setup", self.preset_tree_row_setup)
        factory.connect("bind", self.preset_tree_row_bind)

        list_view = Gtk.ListView.new(Gtk.SingleSelection.new(tree_model), factory)
        list_view.connect("activate", self.preset_tree_row_activated)

        scrolled = Gtk.ScrolledWindow()
        scrolled.set_child(list_view)
        scrolled.set_vexpand(True)

        return scrolled

    def preset_tree_row_setup(self, factory, list_item):

        # Same row as the flat lists, inside a TreeExpander.
        self.preset_row_setup(factory, list_item)

        row_box = list_item.get_child()
        list_item.set_child(None)

        expander = Gtk.TreeExpander()
        expander.set_child(row_box)

        list_item.set_child(expander)
        list_item.tree_expander = expander

    def preset_tree_row_bind(self, factory, list_item):

        tree_row = list_item.get_item()
        node = tree_row.get_item()

        list_item.tree_expander.set_list_row(tree_row)

        is_preset = node.preset_name is not None

        for button in (
            list_item.favorite_button,
            list_item.add_button,
        ):
            button.set_visible(is_preset)

        list_item.remove_button.set_visible(False)

        if is_preset:
            self.bind_preset_row(list_item, node.preset_name)
        else:
            list_item.preset_label.set_label(node.title)

    def preset_tree_row_activated(self, list_view, position):

        tree_row = list_view.get_model().get_item(position)
        node = tree_row.get_item()

        if node.preset_name is None:
            tree_row.set_expanded(not tree_row.get_expanded())
            return

        self.presets.go_to(node.preset_name)

    # The preset collection as a Category > Subfolder > preset tree;
    # presets loaded from files go under "Your Presets".
    def build_preset_tree_nodes(self):

        root = {}

        for name in self.presets.catalog:
            folders = projectm_view.preset_folders(self.preset_paths[name]) or (USER_PRESETS_FOLDER,)
            level = root

            for folder in folders:
                level = level.setdefault(folder, {})

            level.setdefault(None, []).append(name)

        def to_nodes(level):

            # Your Presets first, then the collection's categories.
            folders = sorted(
                (key for key in level if key is not None),
                key=lambda folder: (folder != USER_PRESETS_FOLDER, folder)
            )
            nodes = []

            for folder in folders:
                children = to_nodes(level[folder])
                count = count_presets(level[folder])
                nodes.append(PresetTreeNode(f"{folder} ({count})", children=children))

            for name in sorted(level.get(None, []), key=str.casefold):
                nodes.append(PresetTreeNode(name, preset_name=name))

            return nodes

        def count_presets(level):

            return len(level.get(None, [])) + sum(
                count_presets(sub) for key, sub in level.items() if key is not None
            )

        return to_nodes(root)

    def refresh_preset_tree(self):

        if getattr(self, "preset_tree_root", None) is None:
            return

        nodes = self.build_preset_tree_nodes() or []
        self.preset_tree_root.splice(0, self.preset_tree_root.get_n_items(), nodes)
        self.update_preset_browser_page()

    def update_preset_browser_page(self):

        stack = getattr(self, "preset_page_stack", None)

        if stack is None:
            return

        searching = bool(self.preset_search_entry.get_text())
        has_tree = self.preset_tree_root.get_n_items() > 0

        stack.set_visible_child_name("tree" if has_tree and not searching else "flat")

    def build_favorites_tab(self):

        self.favorites_list_store = Gtk.StringList.new(self.favorites)

        return self.build_preset_search_list(self.favorites_list_store)

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

        # One "Add to…" button (was separate Add to Queue and Add to
        # Playlist… buttons): its menu has Add to Queue first, then the
        # playlists and New Playlist….
        add_button = Gtk.Button(icon_name="list-add-symbolic")

        add_button.add_css_class("flat")
        add_button.set_tooltip_text("Add to Queue or Playlist…")

        box.append(add_button)

        # Only on presets you opened from files (Your Presets): forget
        # it - the file itself isn't touched.
        remove_button = Gtk.Button(icon_name="list-remove-symbolic")
        remove_button.add_css_class("flat")
        remove_button.set_tooltip_text("Remove from Your Presets")
        remove_button.set_visible(False)

        box.append(remove_button)

        list_item.set_child(box)

        # Stashed on the list_item itself (not the widgets) so bind
        # can find them again - GTK4 ListView recycles these rows for
        # different items as you scroll, so bind gets called many
        # times for the same row/button pair.
        list_item.preset_label = label
        list_item.favorite_button = favorite_button
        list_item.favorite_button_handler = None
        list_item.add_button = add_button
        list_item.add_button_handler = None
        list_item.remove_button = remove_button
        list_item.remove_button_handler = None

    def preset_row_bind(self, factory, list_item):

        self.bind_preset_row(list_item, list_item.get_item().get_string())

    # Shared by the flat lists and the folder tree's preset rows.
    def bind_preset_row(self, list_item, name):

        list_item.preset_label.set_label(name)

        # Favorites (and playlists) can name presets that aren't in the
        # collection any more - presets loaded from files only last for
        # the session they were loaded in.
        available = self.presets.contains(name)

        if available:
            list_item.preset_label.remove_css_class("dim-label")
            list_item.preset_label.set_tooltip_text(None)
        else:
            list_item.preset_label.add_css_class("dim-label")
            list_item.preset_label.set_tooltip_text(
                "Not available - the file was moved or deleted; open it "
                "again (Load Preset…) to use it"
            )

        list_item.add_button.set_sensitive(available)

        if list_item.remove_button_handler is not None:
            list_item.remove_button.disconnect(list_item.remove_button_handler)

        list_item.remove_button.set_visible(name in self.user_loaded_presets)
        list_item.remove_button_handler = list_item.remove_button.connect(
            "clicked", lambda button, n=name: self.remove_user_preset(n)
        )

        if list_item.favorite_button_handler is not None:
            list_item.favorite_button.disconnect(list_item.favorite_button_handler)

        list_item.favorite_button.set_icon_name(
            "starred-symbolic" if self.is_favorite(name) else "non-starred-symbolic"
        )

        list_item.favorite_button_handler = list_item.favorite_button.connect(
            "clicked",
            lambda button, n=name: self.favorite_row_clicked(button, n)
        )

        if list_item.add_button_handler is not None:
            list_item.add_button.disconnect(list_item.add_button_handler)

        list_item.add_button_handler = list_item.add_button.connect(
            "clicked",
            lambda button, n=name: self.open_add_to_playlist_popover(
                button, n, include_queue=True
            )
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

        if not self.presets.go_to(name):
            self.show_toast(f"Not available: {name}")

    def enqueue_preset(self, name):

        # Only presets in the current collection can be queued - a
        # favorite/playlist entry for a preset loaded from a file in an
        # earlier session no longer exists (it used to fail silently
        # behind an "Added to queue" toast).
        if not self.presets.enqueue(name):
            self.show_toast(f"Not available: {name}")
            return

        self.show_toast(f"Added to queue: {name}")

    def show_queue_clicked(self, action, param):

        self.show_sidebar_section("presets", tab="queue")

    def show_favorites_clicked(self, action, param):

        self.show_sidebar_section("presets", tab="favorites")

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

        self.presets.move_queue_item(position, delta)

    def reorder_queue_item(self, from_position, to_position):

        if from_position == to_position:
            return True

        self.presets.move_queue_item_to(from_position, to_position)

        return True

    def remove_queue_item(self, position):

        self.presets.remove_queue_item(position)

    # projectm-*.json: named for the projectM preset collection they
    # refer to. favorites.json/playlists.json from the old Butterchurn
    # renderer are left as they were.
    def playlists_file_path(self):

        config_dir = Path(GLib.get_user_config_dir()) / "melange"
        config_dir.mkdir(parents=True, exist_ok=True)

        return config_dir / "projectm-playlists.json"

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

        return config_dir / "projectm-favorites.json"

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

        if not self.show_preset_in_title:
            return title

        if self.current_playlist_name:
            title += f" ({self.current_playlist_name})"

        if self.current_preset_name:
            title += f' - "{self.current_preset_name}"'

        return title

    def show_playlists_clicked(self, action, param):

        self.show_sidebar_section("presets", tab="playlists")

    def build_playlists_tab(self):

        self.playlists_group = Adw.PreferencesGroup()

        new_row = Adw.ActionRow(title="New Playlist…", activatable=True)
        new_row.connect("activated", lambda row: self.new_playlist_from_preset_clicked())
        new_row.add_suffix(Gtk.Image.new_from_icon_name("list-add-symbolic"))
        self.playlists_group.add(new_row)

        # Greyed out (with a reason) while the queue is empty - it used
        # to only flash a "Queue is empty" toast behind the sidebar.
        save_row = Adw.ActionRow(
            title="Save Queue as Playlist…",
            activatable=True
        )
        save_row.connect("activated", self.save_playlist_clicked)
        save_row.add_suffix(
            Gtk.Image.new_from_icon_name("document-save-symbolic")
        )
        self.playlists_group.add(save_row)
        self.save_queue_row = save_row
        self.update_save_queue_row()

        self.playlist_rows = []
        self.expanded_playlists = set()
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

        return scrolled

    # A playlist as an expandable row: open it to see its presets (click
    # one to show it, or remove it); Load puts the playlist in the queue.
    def build_playlist_row(self, playlist):

        name = playlist["name"]
        count = len(playlist["presets"])

        row = Adw.ExpanderRow(
            title=name,
            subtitle=f"{count} preset{'s' if count != 1 else ''}"
        )
        row.set_expanded(name in self.expanded_playlists)
        row.connect(
            "notify::expanded",
            lambda r, param, n=name: (
                self.expanded_playlists.add(n) if r.get_expanded()
                else self.expanded_playlists.discard(n)
            )
        )

        load_button = Gtk.Button(icon_name="media-playback-start-symbolic")
        load_button.add_css_class("flat")
        load_button.set_valign(Gtk.Align.CENTER)
        load_button.set_tooltip_text("Load into Queue")
        load_button.set_sensitive(count > 0)
        load_button.connect("clicked", lambda b, n=name: self.load_playlist_by_name(n))
        row.add_suffix(load_button)

        remove_button = Gtk.Button(icon_name="user-trash-symbolic")
        remove_button.add_css_class("flat")
        remove_button.set_valign(Gtk.Align.CENTER)
        remove_button.set_tooltip_text("Delete Playlist")
        remove_button.connect("clicked", lambda b, n=name: self.delete_playlist_by_name(n))
        row.add_suffix(remove_button)

        if not playlist["presets"]:
            empty = Adw.ActionRow(title="No presets yet")
            empty.set_subtitle("Use the + button on a preset to add it")
            empty.add_css_class("dim-label")
            row.add_row(empty)

        for preset in playlist["presets"]:
            preset_row = Adw.ActionRow(title=preset, activatable=True)
            preset_row.set_title_lines(1)
            preset_row.set_tooltip_text(preset)
            preset_row.connect("activated", lambda r, p=preset: self.presets.go_to(p))

            unavailable = not self.presets.contains(preset)
            if unavailable:
                preset_row.set_subtitle("Not available")
                preset_row.set_activatable(False)

            remove = Gtk.Button(icon_name="list-remove-symbolic")
            remove.add_css_class("flat")
            remove.set_valign(Gtk.Align.CENTER)
            remove.set_tooltip_text("Remove from Playlist")
            remove.connect(
                "clicked",
                lambda b, n=name, p=preset: self.remove_preset_from_playlist(p, n)
            )
            preset_row.add_suffix(remove)

            row.add_row(preset_row)

        return row

    def remove_preset_from_playlist(self, preset, playlist_name):

        playlist = next((p for p in self.playlists if p["name"] == playlist_name), None)

        if playlist is None or preset not in playlist["presets"]:
            return

        playlist["presets"].remove(preset)
        self.save_playlists()
        self.refresh_playlists_list()

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

        # Replaces the queue wholesale (names not in the active
        # renderer's catalog are dropped); the queue dialog updates via
        # on_preset_queue_changed.
        self.presets.set_queue(playlist["presets"])

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

    # name=None creates an empty playlist (Playlists tab's New Playlist…).
    def new_playlist_from_preset_clicked(self, name=None):

        entry = Gtk.Entry()
        entry.set_placeholder_text("Playlist name")

        dialog = Adw.AlertDialog(
            heading="New Playlist",
            body=(
                f'Create a new playlist containing "{name}".' if name else
                "Create an empty playlist. Add presets with the + button on any preset."
            )
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
            "presets": [name] if name else []
        })

        self.save_playlists()
        self.refresh_playlists_list()
        self.show_toast(
            f'Created playlist "{playlist_name}" with "{name}"' if name
            else f'Created playlist "{playlist_name}"'
        )

    def refresh_playlists_list(self):

        # May be called before the Playlists tab has been built.
        if getattr(self, "playlists_group", None) is None:
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
        preset_filter.set_name("MilkDrop presets")
        preset_filter.add_pattern("*.milk")

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

        self.load_preset_file(gfile)

    # Shared by Load Preset… and dropping files onto the window. projectM
    # loads the .milk file from its path; a load that fails is reported
    # through on_projectm_preset_failed.
    def load_preset_file(self, gfile):

        path = gfile.get_path()
        basename = gfile.get_basename() or "preset"

        if path is None or not basename.lower().endswith(".milk"):
            self.show_toast("Only MilkDrop (.milk) presets can be loaded")
            return False

        name = Path(basename).stem
        existing = self.preset_paths.get(name)

        if (
            isinstance(existing, projectm_view.ZipPreset)
            and projectm_view.read_preset_bytes(existing) == projectm_view.read_preset_bytes(path)
        ):
            self.presets.go_to(name)
            self.show_toast(f"Already in the collection: {name}")
            return True

        name = self.add_user_preset(Path(path))
        self.save_user_presets()

        self.refresh_preset_lists()
        self.presets.go_to(name)

        return True

    def add_user_preset(self, path):
        """Adds a .milk file to the catalog (under Your Presets in the
        browser) and returns the name it's listed under."""

        name = path.stem
        existing = self.preset_paths.get(name)

        if existing not in (None, path):
            name = f"{name} (loaded)"

        self.preset_paths[name] = path

        if not self.presets.contains(name):
            self.presets.set_catalog(self.presets.catalog + [name])

        if name not in self.user_loaded_presets:
            self.user_loaded_presets.append(name)

        return name

    def remove_user_preset(self, name):
        """Takes a preset opened from a file off the list (and out of
        Your Presets); the file itself stays where it is."""

        if name not in self.user_loaded_presets:
            return

        self.user_loaded_presets.remove(name)
        self.preset_paths.pop(name, None)
        self.presets.set_catalog([n for n in self.presets.catalog if n != name])
        self.save_user_presets()
        self.refresh_preset_lists()
        self.show_toast(f"Removed \"{name}\" from Your Presets")

    # Presets opened from files are remembered between launches (their
    # paths, in user-presets.json); ones whose file is gone are dropped.
    def user_presets_file_path(self):

        config_dir = Path(GLib.get_user_config_dir()) / "melange"
        config_dir.mkdir(parents=True, exist_ok=True)

        return config_dir / "user-presets.json"

    def save_user_presets(self):

        paths = [
            str(self.preset_paths[name]) for name in self.user_loaded_presets
            if isinstance(self.preset_paths.get(name), Path)
        ]

        try:
            with open(self.user_presets_file_path(), "w") as f:
                json.dump(paths, f, indent=2)
        except OSError as e:
            print("Failed to save opened presets:", e)

    def restore_user_presets(self):

        try:
            with open(self.user_presets_file_path()) as f:
                paths = json.load(f)
        except (OSError, ValueError):
            return

        for entry in paths if isinstance(paths, list) else []:
            if isinstance(entry, str) and Path(entry).is_file():
                self.add_user_preset(Path(entry))

        # Rewrite without the ones that are gone.
        self.save_user_presets()

    def on_files_dropped(self, drop_target, value, x, y):

        files = [
            f for f in value.get_files()
            if (f.get_basename() or "").lower().endswith(".milk")
        ]

        if not files:
            self.show_toast("Drop MilkDrop (.milk) presets to load them")
            return False

        loaded = sum(1 for f in files if self.load_preset_file(f))

        if loaded > 1:
            self.show_toast(f"Loading {loaded} presets")

        return loaded > 0

    def next_preset(self, button):

        if self.preset_locked:
            self.show_toast("Preset is locked")
            return

        self.resume_if_paused()

        self.presets.next()

    def previous_preset(self, button):

        if self.preset_locked:
            self.show_toast("Preset is locked")
            return

        self.resume_if_paused()

        self.presets.previous()

    def set_current_preset_name(self, name):

        self.current_preset_name = name
        self.set_title(self.build_window_title())
        self.update_mirror_titles()
        self.update_favorite_button_icon()

    # The widget mirror windows show (Gtk.WidgetPaintable).
    def visualizer_widget(self):

        return self.visualizer

    @property
    def preset_names(self):

        return self.presets.catalog

    @property
    def preset_queue(self):

        return self.presets.queue

    def show_preset(self, name):

        preset = self.preset_paths.get(name)

        if preset is None or self.projectm_view is None:
            return

        self.projectm_view.load_preset(preset)
        self.set_current_preset_name(name)

    # Beat-Driven Cycle: projectM detected a beat strong enough to
    # change preset (see ProjectMView.apply_settings).
    def on_projectm_beat(self):

        if self.preset_locked or self.paused or not self.main_visualizer_on:
            return

        self.presets.next()
        self.show_toast("Beat detected - next preset")

    def on_projectm_preset_failed(self, preset, reason):

        name = next(
            (n for n, p in self.preset_paths.items() if p == preset),
            getattr(preset, "stem", str(preset))
        )

        self.show_toast(f"Couldn't load preset: {name}")

        if not self.preset_locked:
            self.presets.next()

    def set_blend_time(self, seconds):

        if self.projectm_view is not None:
            self.projectm_view.set_transition_seconds(seconds)

    def update_save_queue_row(self):

        row = getattr(self, "save_queue_row", None)

        if row is None:
            return

        has_queue = bool(self.preset_queue)
        row.set_sensitive(has_queue)
        row.set_subtitle("" if has_queue else "The queue is empty")

    def on_preset_queue_changed(self):

        self.update_save_queue_row()

        if self.queue_list_store is not None:
            self.queue_list_store.splice(
                0, self.queue_list_store.get_n_items(), self.presets.queue
            )

    # Re-fill every preset list UI from the active renderer's data -
    # after a renderer switch or a catalog change.
    def refresh_preset_lists(self):

        for store, items in (
            (self.preset_list_store, self.preset_names),
            (self.favorites_list_store, self.favorites),
            (self.queue_list_store, self.preset_queue),
        ):
            if store is not None:
                store.splice(0, store.get_n_items(), items)

        self.refresh_preset_tree()
        self.refresh_playlists_list()
        self.update_favorite_button_icon()

    def build_sensitivity_control(self):

        def format_sensitivity(value):
            return f"{value:.1f}x"

        return self.build_slider_row(
            "Sensitivity",
            0.0, 4.0, 0.1, 1.0,
            format_sensitivity,
            lambda value: self.projectm_view and self.projectm_view.set_sensitivity(value),
            store_as="sensitivity_scale"
        )



    def audio_source_changed(self, action, value):

        source = value.get_string()

        print("Audio source (Python):", source)

        action.set_state(value)

        if source == "mic":
            self.pinned_sink = None
            self.restart_audio_monitor(microphone=True)
            return

        if source == "none":
            self.pinned_sink = None
            self.stop_audio_capture()
            return

        # Anything else is a specific sink's node name, picked from the
        # dynamically built device list - pin capture to that device
        # (see on_pactl_event for what happens if it later disappears).
        self.pinned_sink = source

        if self.current_sink != source:
            self.restart_audio_monitor(source)


    # System Audio Capture (Pipewire)

    def start_system_audio(self, sink_name=None, microphone=False):

        if microphone:
            # The default input device, through the same pipeline.
            self.current_sink = MICROPHONE_SOURCE
            source_properties = ""
            print("Capturing the default microphone")
        else:
            if sink_name is None:
                sink_name = self.get_default_sink()

            self.current_sink = sink_name
            source_properties = (
                f'target-object="{sink_name}" '
                'stream-properties="props,stream.capture.sink=true"'
            )
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
            {source_properties}
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

        self.gst_bus_handler = bus.connect(
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

                # Runs on GStreamer's streaming thread. This used to
                # GLib.idle_add() a separate callback (holding its own
                # copy of the chunk) for every chunk, ~24 a second.
                # Idle callbacks run at the lowest main-loop priority,
                # so whenever the main thread was busy (e.g. aux
                # windows redrawing) they piled up without limit -
                # measured ~49MB in 5 minutes, and the visualizer fed
                # increasingly stale audio. Now chunks accumulate in
                # one bounded buffer, oldest audio dropped first, and
                # at most one flush is scheduled at a time.
                with self.pending_audio_lock:

                    self.pending_audio.extend(data)

                    excess = len(self.pending_audio) - MAX_PENDING_AUDIO_BYTES

                    if excess > 0:
                        # Whole stereo S16 frames only (4 bytes), so
                        # L/R never swap.
                        del self.pending_audio[:excess + (-excess % 4)]

                    if not self.audio_flush_scheduled:
                        self.audio_flush_scheduled = True
                        GLib.idle_add(
                            self.flush_pending_audio,
                            priority=GLib.PRIORITY_DEFAULT
                        )

        return Gst.FlowReturn.OK

    def flush_pending_audio(self):

        with self.pending_audio_lock:
            data = bytes(self.pending_audio)
            self.pending_audio.clear()
            self.audio_flush_scheduled = False

        if data:
            if self.projectm_view:
                self.projectm_view.push_audio(data)

            if self.aux_windows:
                self.forward_audio_to_aux_windows(data)

        return False



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

    # Longest label the Audio Source submenu will show. GtkPopoverMenu
    # sizes every page to its widest item, so one long sink name made
    # the whole main menu extremely wide, not just the submenu.
    AUDIO_SOURCE_LABEL_MAX_CHARS = 40

    # PipeWire sink descriptions are "<card name> <port name>", for
    # example "500 Series Chipset Family On-Package High Definition
    # Audio (HD Audio) HDMI / DisplayPort 3 Output" (97 chars). Drop
    # the card name (device.description) when that leaves something,
    # so this shows "HDMI / DisplayPort 3 Output" / "Speaker". Sinks
    # whose description is just the device name (e.g. Bluetooth
    # headphones) keep it as is. Falls back to the full description if
    # two sinks would end up with the same short label, then
    # middle-ellipsizes anything still too long, keeping the end
    # (usually the part that tells outputs apart).
    def list_sink_labels(self):

        raw = subprocess.check_output(
            ["pactl", "-f", "json", "list", "sinks"]
        )

        entries = []

        for sink in json.loads(raw):

            description = sink.get("description") or sink["name"]

            card = sink.get("properties", {}).get(
                "device.description", ""
            )

            short = description

            if card and description.startswith(card):
                short = description[len(card):].strip() or description

            entries.append((sink["name"], description, short))

        short_counts = {}

        for _, _, short in entries:
            short_counts[short] = short_counts.get(short, 0) + 1

        limit = self.AUDIO_SOURCE_LABEL_MAX_CHARS
        result = []

        for name, description, short in entries:

            label = short if short_counts[short] == 1 else description

            if len(label) > limit:
                head = (limit - 1) // 2
                tail = limit - 1 - head
                label = label[:head].rstrip() + "…" + label[-tail:].lstrip()

            result.append((name, label))

        return result

    # Audio Source (Settings > Audio): output devices (their monitors),
    # Microphone and None, in a combo row driving win.audio-source.
    # Rebuilt when devices appear/disappear (on_pactl_event).
    def rebuild_audio_source_menu(self):

        self.audio_source_choices = (
            list(self.list_sink_labels())
            + [("mic", "Microphone"), ("none", "None")]
        )

        row = getattr(self, "audio_source_row", None)

        if row is None:
            return

        self.updating_audio_source_row = True
        row.set_model(Gtk.StringList.new(
            [label for _, label in self.audio_source_choices]
        ))
        self.updating_audio_source_row = False

        self.sync_audio_source_row()

    def sync_audio_source_row(self, *args):

        row = getattr(self, "audio_source_row", None)

        if row is None:
            return

        current = self.lookup_action("audio-source").get_state().get_string()
        values = [value for value, _ in self.audio_source_choices]

        if current in values:
            self.updating_audio_source_row = True
            row.set_selected(values.index(current))
            self.updating_audio_source_row = False

    def build_audio_source_control(self):

        row = Adw.ComboRow(title="Audio Source")
        self.audio_source_row = row
        self.updating_audio_source_row = False
        self.rebuild_audio_source_menu()

        def selected_changed(r, param):
            if self.updating_audio_source_row:
                return
            index = r.get_selected()
            if 0 <= index < len(self.audio_source_choices):
                self.lookup_action("audio-source").change_state(
                    GLib.Variant("s", self.audio_source_choices[index][0])
                )

        row.connect("notify::selected", selected_changed)

        # Follow changes made elsewhere (e.g. the automatic fallback to
        # None when the chosen device disappears).
        self.lookup_action("audio-source").connect("notify::state", self.sync_audio_source_row)

        return row

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

        self.teardown_gst_pipeline()

        self.current_sink = None

    # add_signal_watch() in start_system_audio installs a GSource on
    # the bus that keeps it (and the message handler) alive until
    # remove_signal_watch() is called - previously never done, so every
    # stop/restart (e.g. each audio device switch) left the old bus and
    # its watch behind.
    def teardown_gst_pipeline(self):

        if not self.gst_pipeline:
            return

        self.gst_pipeline.set_state(
            Gst.State.NULL
        )

        # set_state() only requests the transition - it can finish
        # asynchronously. Without waiting for it here, a new pipeline
        # built right after (restart_audio_monitor) gets its
        # pipewiresrc attached while the old one is still tearing
        # down its PipeWire stream, and the new appsink silently never
        # receives a single sample.
        self.gst_pipeline.get_state(
            Gst.CLOCK_TIME_NONE
        )

        bus = self.gst_pipeline.get_bus()
        bus.remove_signal_watch()

        if self.gst_bus_handler is not None:
            bus.disconnect(self.gst_bus_handler)
            self.gst_bus_handler = None

        self.gst_pipeline = None

    def restart_audio_monitor(self, sink_name=None, microphone=False):

        self.teardown_gst_pipeline()

        self.start_system_audio(sink_name, microphone=microphone)

        return False
