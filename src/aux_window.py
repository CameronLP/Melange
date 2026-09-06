# aux_window.py
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

import colorsys
import math
import cmath
import random
import time
from collections import deque

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
gi.require_version("GdkPixbuf", "2.0")

from gi.repository import Gtk, Adw, Gdk, Gio, GLib, GdkPixbuf
import cairo

AUX_WINDOW_TITLES = {
    "vu": "VU Meter",
    "xy": "X-Y Scope",
    "oscilloscope": "Oscilloscope",
    "vectorscope": "Vector Scope",
    "spectrum": "Spectrum",
    "spectrogram": "Spectrogram",
    "terrain": "3D Terrain Spectrogram",
    "waterfall": "3D Waterfall",
    "peak": "Peak Meter",
    "dvd": "DVD Bounce",
    "pipes": "Pipes",
}

# Pipes - classic "3D Pipes"-screensaver-style aux window. A small
# cubic grid a pipe can occupy one cell of at a time; the 6 axis-
# aligned directions a pipe can move/turn between.
PIPE_DIRECTIONS = [
    (1, 0, 0), (-1, 0, 0),
    (0, 1, 0), (0, -1, 0),
    (0, 0, 1), (0, 0, -1),
]

PIPES_GRID_SIZE = 8
PIPES_TICK_INTERVAL_MS = 16
PIPES_BASE_STEP_INTERVAL = 0.12

# Once this fraction of the grid's cells are filled, everything clears
# and starts over - same periodic "reset and start fresh" behavior the
# reference screensaver has, since a mostly-full grid leaves pipes
# with nowhere left to grow.
PIPES_RESET_FRACTION = 0.6

# Each pipe is assigned one of these frequency bands (cycled through
# at spawn - see reset_pipes/spawn_pipe) and moves at a speed driven
# by that band's own live level rather than one shared broadband
# level - so different pipes visibly react to different parts of the
# mix. Colored to match (PIPES_BAND_COLOR_HEXES, same index), so the
# effect is actually visible rather than just an invisible behavioral
# difference.
PIPES_BANDS = [(20, 250), (250, 1000), (1000, 4000), (4000, 16000)]
PIPES_BAND_COLOR_HEXES = ["#e6394b", "#ffcc33", "#33cccc", "#cc66ff"]
PIPES_BAND_FRAME_INTERVAL = 0.08

# Real music's spectral energy rolls off heavily with frequency - a
# 1000-4000Hz or 4000-16000Hz bin's raw magnitude sits far below a
# 20-250Hz one's for most tracks, even though magnitude_in_band's own
# scale (a fixed reference calibrated for a full-scale sine peak, same
# idea as bars_from_magnitudes) is identical for every band. Without
# compensation, three of four pipes barely ever see a high band_level
# and read as "not reacting" - this is the actual thing "pipes don't
# react to frequencies" was about, not a formula bug in how band_level
# then drives speed/width/turning. Same index as PIPES_BANDS.
PIPES_BAND_GAINS = [1.0, 1.6, 2.8, 4.5]
PIPES_BAND_LABELS = ["Bass Color", "Low Mid Color", "High Mid Color", "Treble Color"]

# Oscilloscope: how many past samples are kept in its rolling buffer
# (push_audio appends into it) versus how many of those are actually
# shown at once (the "time base" - user-adjustable, see below). The
# buffer needs to hold more than the display window so a trigger point
# can be searched for with enough trailing samples left to fill a full
# display window past it (see find_trigger_index).
SCOPE_BUFFER_SAMPLES = 4096
SCOPE_DEFAULT_TIME_BASE = 1024

# Sentinel icon_name (not a real GTK icon-theme name) meaning "draw
# the DVD logo" (see draw_dvd_text_logo) instead of looking anything
# up in the icon theme.
DVD_TEXT_ICON = "__dvd_text__"

# The actual DVD logo - Wikimedia Commons' File:DVD_logo.svg, tagged
# there as public domain (PD-textlogo: simple geometric shapes/text,
# below the threshold of originality for copyright), bundled into
# melange.gresource via melange.gresource.xml. Commons separately
# notes the logo may still be trademark-protected in some
# jurisdictions regardless of its copyright status - noted here, not
# a reason to avoid a nostalgic screensaver parody use like this one.
# Its own viewBox is 1058.4 x 465.84 - draw_dvd_text_logo renders it
# at this aspect ratio rather than stretching it into the square
# bounding box dvd_tick's wall-collision math uses.
DVD_LOGO_RESOURCE = "/com/cameronlp/Melange/dvd-logo.svg"
DVD_LOGO_ASPECT = 1058.4 / 465.84

# Same idea as DVD_TEXT_ICON - the real Tux, Larry Ewing's original
# artwork (freely licensed: usage is permitted provided Larry Ewing
# and The GIMP are credited - see README's Credits section), fetched
# from the URL the user gave and bundled the same way as the DVD logo
# (src/tux.svg, melange.gresource.xml). Its own viewBox is 216 x 256
# (taller than wide, unlike the DVD logo's wide wordmark) and it's
# genuinely full-color (confirmed via its own fill attributes: black,
# off-white, several yellow/orange shades for the beak and feet) - so
# draw_tux_icon shows it as-is rather than recoloring it, the same
# choice already made for the full-color app-logo option. Falls back
# to an original, simple Cairo-drawn penguin doodle (not a
# reproduction of Ewing's specific linework) if the bundled resource
# ever fails to load.
TUX_ICON = "__tux__"
TUX_RESOURCE = "/com/cameronlp/Melange/tux.svg"
TUX_ASPECT = 216 / 256

# icon name -> menu label, offered in the DVD Bounce window's settings
# popover. The wordmark is first/default - it's the whole reason this
# window is called "DVD Bounce"; the rest (Tux, the app's own icon,
# then a few GTK stock icons guaranteed present in any icon theme) are
# just alternatives.
DVD_ICON_CHOICES = [
    (DVD_TEXT_ICON, "DVD Logo"),
    (TUX_ICON, "Tux"),
    ("com.cameronlp.Melange", "Melange Logo"),
    ("starred-symbolic", "Star"),
    ("emblem-favorite-symbolic", "Heart"),
    ("weather-clear-symbolic", "Sun"),
    ("face-smile-symbolic", "Smiley"),
    ("network-wireless-symbolic", "Wireless"),
]

DVD_BASE_SIZE = 72

# Cycled through on every wall bounce - the actual iconic part of a
# "DVD screensaver", not just a bouncing shape. Size/speed react
# continuously to audio level instead (see dvd_tick) since a
# continuous color shift would fight with the discrete per-bounce one.
DVD_BOUNCE_COLOR_HEXES = [
    "#e6394b", "#33cc55", "#3399ff", "#ffcc33",
    "#cc66ff", "#ff9933", "#33cccc", "#ff66aa",
]

# ~60fps - the DVD icon needs to keep moving even between audio
# chunks (silence, or a slow chunk rate), unlike every other aux
# window kind here, which only ever redraws in response to push_audio
# arriving. A dedicated per-window GLib timer decouples its animation
# from the audio delivery rate entirely.
DVD_TICK_INTERVAL_MS = 16

# vu_style key -> settings-popover dropdown label. "bars" first/
# default - the original look.
VU_STYLE_CHOICES = [
    ("bars", "Bars"),
    ("led", "LED Segments"),
    ("needle", "Needle"),
]

SPECTRUM_STYLE_CHOICES = [("bars", "Bars"), ("smooth", "Smooth")]

# How many past frames the spectrogram keeps on screen at once, and
# how far apart (in wall-clock time) those frames are taken - a new
# column every FFT_SIZE samples (~11.6ms at 44100Hz) would scroll by
# far too fast to read, so this decouples the waterfall's scroll speed
# from the FFT's own analysis rate.
SPECTROGRAM_COLUMNS = 200
SPECTROGRAM_FRAME_INTERVAL = 0.05

# 3D Terrain Spectrogram - same idea as the flat Spectrogram's own
# column history, but far fewer rows (a rotatable terrain reads fine
# with a couple dozen ridge lines; it doesn't need anywhere near 200
# to look like a terrain) and a slightly slower cadence, both
# deliberately conservative: the flat Spectrogram (see the **PERF**
# TODO entry for it) redraws its *entire* history on every audio-
# chunk-driven frame regardless of whether a new column actually
# arrived, which is the likely cause of reported lag there - this
# kind avoids repeating that mistake from the start (see
# terrain_dirty/terrain_surface in draw_terrain) rather than fixing it
# after the fact.
TERRAIN_ROWS = 36
TERRAIN_FRAME_INTERVAL = 0.08

# 3D Waterfall - same shape of underlying data (rows of per-bin FFT
# magnitude over time) and the same rotatable oblique camera as
# Terrain, but a flat, per-cell colored surface (like the 2D
# Spectrogram's own heatmap texture) instead of Terrain's height-
# extruded ridges - magnitude reads purely as color here, not
# elevation.
WATERFALL_ROWS = 36
WATERFALL_FRAME_INTERVAL = 0.08


def gradient_color(level, lo, hi):

    # Plain linear interpolation between two user-chosen endpoint
    # colors, reading magnitude as color the same way a classic
    # spectrogram/thermal colormap does - just a straight 2-stop
    # gradient (Low/High, settings-selectable) rather than a fixed
    # multi-stop one, so it isn't locked to any single palette.
    level = max(0.0, min(1.0, level))

    return (
        lo.red + (hi.red - lo.red) * level,
        lo.green + (hi.green - lo.green) * level,
        lo.blue + (hi.blue - lo.blue) * level,
    )


def rainbow_color(level):

    # A full hue sweep instead of a 2-stop lerp between two fixed
    # endpoints - the classic "rainbow" spectrum-analyzer look (red at
    # full scale, sweeping down through orange/yellow/green/cyan to
    # blue-violet at silence), same red=hot/blue=cool convention as
    # the old heatmap_color this whole gradient system replaced.
    # Brightness also rises with level (val) so quiet cells read as
    # dim rather than a fully saturated color at any level.
    level = max(0.0, min(1.0, level))

    hue = (1.0 - level) * 0.72
    r, g, b = colorsys.hsv_to_rgb(hue, 0.9, 0.35 + 0.65 * level)

    return (r, g, b)


def scale_level_for_color(level, ceiling):

    # Remaps level so that `ceiling` (settings-adjustable per kind:
    # spectrogram_color_ceiling/terrain_color_ceiling/
    # waterfall_color_ceiling, "High Level" in each one's settings
    # popover) reads as the top of the color scale (fully "hot"/
    # highest gradient stop) instead of a true 1.0 - lets the color
    # scale's own visible range be dialed in against whatever a given
    # track's real levels typically reach, rather than requiring an
    # actual full-scale reading to ever show the hottest color at all.
    # 1.0 (the default) is the identity mapping - today's original
    # behavior, unchanged unless this is actually turned down.
    return min(1.0, level / max(0.05, ceiling))


# Palette choices shared by every gradient-capable kind (Spectrogram,
# Terrain, Waterfall) - key stored in e.g. self.terrain_palette,
# label shown in that kind's settings popover dropdown.
PALETTE_CHOICES = [("gradient", "Gradient"), ("rainbow", "Rainbow")]

# Same idea as PALETTE_CHOICES, for the kinds that only ever had a
# single solid Color (X-Y Scope, Spectrum, VU Meter's needle style,
# Oscilloscope, Vector Scope) rather than a Low/High gradient pair -
# "Gradient" wouldn't mean anything for those, so this offers "Solid"
# (today's only behavior) against "Rainbow" instead. Backed by one
# shared self.color_mode field, the same way self.color itself is
# already shared generic state across these kinds.
COLOR_MODE_CHOICES = [("solid", "Solid"), ("rainbow", "Rainbow")]


def ensure_min_brightness(color, minimum=0.35):

    # Scales a color up toward white (preserving its hue, not just
    # clamping each channel independently - clamping black straight to
    # gray would erase the hue entirely) if it's darker than `minimum`.
    # Used for the Terrain ridge-line outline specifically: at a low
    # gradient level and/or a dark Low Color, the plain gradient result
    # can get close enough to the near-black background that the
    # outline all but disappears at some camera angles - reported from
    # real use ("low outline is hard to see, blends into background").
    # The filled body underneath is left alone; only the outline needs
    # to always read as a distinct line regardless of the chosen
    # palette or how quiet that row was.
    r, g, b = color
    brightness = max(r, g, b)

    if brightness >= minimum:
        return color

    if brightness <= 0.0:
        return (minimum, minimum, minimum)

    factor = minimum / brightness

    return (min(1.0, r * factor), min(1.0, g * factor), min(1.0, b * factor))

# The pipeline in window.py (start_system_audio) is hardcoded to this
# rate, so the spectrum window's bin-to-frequency mapping can be too.
SAMPLE_RATE = 44100

# Empirical VU-meter calibration - see draw_vu_meter.
VU_GAIN = 1.8

# Must be a power of two (this FFT is plain radix-2 Cooley-Tukey).
# Frequency resolution is SAMPLE_RATE/FFT_SIZE per bin (~21.5Hz at
# 2048) - this is the classic STFT time/frequency trade-off: a bigger
# window resolves lower frequencies more distinctly (see
# bars_from_magnitudes - at 512 samples/~86Hz-per-bin, many of the
# low, log-spaced bars/rows below a few hundred Hz span *less than one
# bin's width* and end up reading the exact same bin, showing as
# several bars/rows moving in lockstep) at the cost of needing more
# buffered audio (2048 samples is ~46ms of latency/staleness - still
# imperceptible for a passive visualizer, unlike the main audio path).
FFT_SIZE = 2048

# Precomputed once - a plain Hann window, tapering the analysis
# buffer's edges to reduce spectral leakage (energy smearing into
# neighboring bins that a hard-edged buffer would otherwise cause).
_HANN_WINDOW = [
    0.5 - 0.5 * math.cos(2 * math.pi * i / (FFT_SIZE - 1))
    for i in range(FFT_SIZE)
]


def random_bounce_color():

    color = Gdk.RGBA()
    color.parse(random.choice(DVD_BOUNCE_COLOR_HEXES))

    return color


def rms(samples):

    if not samples:
        return 0.0

    return math.sqrt(sum(s * s for s in samples) / len(samples))


def fft(samples):

    n = len(samples)

    if n <= 1:
        return samples

    even = fft(samples[0::2])
    odd = fft(samples[1::2])

    half = n // 2
    combined = [0j] * n

    for k in range(half):

        twiddled = cmath.exp(-2j * math.pi * k / n) * odd[k]

        combined[k] = even[k] + twiddled
        combined[k + half] = even[k] - twiddled

    return combined


class AuxVisualizerWindow(Adw.ApplicationWindow):

    # Native GTK+Cairo rendering, not a second WebKit view - the same
    # memory-cost reasoning MirrorWindow already documents (see
    # mirror_window.py) applies even more directly here, since every
    # additional aux window would otherwise be its own full
    # WebKitWebProcess. Unlike a mirror, though, there's no existing
    # paintable to reuse - each window shows its own reading of the
    # live audio (a level meter, an oscilloscope trace, a spectrum...),
    # not a copy of what the main preset draws, so it keeps its own
    # small buffer of the latest PCM and draws it directly with Cairo.
    def __init__(self, primary, kind, **kwargs):

        super().__init__(
            application=primary.get_application(),
            **kwargs
        )

        self.primary = primary
        self.kind = kind
        self.hide_timer = None
        self.mouse_over_toolbar = False

        # DVD Bounce wants more room to actually bounce around in, and
        # the rotatable 3D kinds read as a cramped sliver at the
        # default size, than the other, mostly-fixed-layout aux
        # windows.
        if kind == "dvd":
            self.set_default_size(480, 320)
        elif kind in ("terrain", "waterfall", "pipes"):
            self.set_default_size(520, 360)
        elif kind in ("xy", "vectorscope"):
            self.set_default_size(400, 400)
        else:
            self.set_default_size(360, 220)

        self.set_title(AUX_WINDOW_TITLES[kind])

        # Same auto-hiding header as the main window/mirror windows
        # (see mouse_move/hide_toolbar below) - these are meant to sit
        # in a corner of the screen alongside the visualizer without a
        # permanently-visible title bar competing for attention.
        self.toolbar_view = Adw.ToolbarView()
        self.toolbar_view.set_extend_content_to_top_edge(True)

        self.header = Adw.HeaderBar()
        self.header.add_css_class("melange-header")
        self.header.set_show_title(True)

        self.color = Gdk.RGBA()
        self.color.parse("#33cc55")

        # Rainbow mode draws the trace as many short per-point-colored
        # segments instead of one continuous stroke - cheap for X-Y
        # Scope/Vector Scope (bounded by the current audio chunk size)
        # but Oscilloscope's Time Base can run up to 2048 points per
        # channel, which would mean thousands of tiny stroke() calls
        # every redraw at the same unthrottled audio-chunk-driven rate
        # already flagged as the likely cause of the flat Spectrogram's
        # own reported lag - defaulting Oscilloscope to Solid instead
        # avoids reintroducing that exact class of problem; Rainbow is
        # still there to opt into. Vector Scope and X-Y Scope also
        # default to Solid, on request - Vector Scope's persistence-
        # trail cloud reads more clearly as one coherent shape in a
        # single color than with each dot's color cycling
        # independently, and X-Y Scope's classic look is the plain
        # green Lissajous trace (self.color's own default, "#33cc55"),
        # not a rainbow one.
        self.color_mode = (
            "solid" if kind in ("oscilloscope", "vectorscope", "xy", "spectrum", "vu")
            else "rainbow"
        )
        self.xy_line_width = 1.0

        self.num_bars = 24
        self.decay = 0.85
        # Scope-type kinds (axis ticks/reference lines) read as more
        # useful with labels on by default than the bar/meter kinds,
        # where labels are more of an optional add-on - requested.
        self.show_labels = kind in ("xy", "oscilloscope", "vectorscope")
        self.mirror_reflection = False
        self.spectrogram_vertical = False

        # A multiplier on how often a new column/row actually gets
        # appended (update_spectrogram_columns) - >1 scrolls faster
        # (shorter effective interval), <1 slower. Doesn't touch how
        # much history SPECTROGRAM_COLUMNS keeps, just how quickly it
        # fills/scrolls through that history.
        self.spectrogram_speed = 1.0

        # Spectrogram heatmap gradient endpoints (see gradient_color) -
        # defaults approximate the look of the fixed 4-stop thermal
        # colormap this replaced (black at silence, warm red-orange at
        # full scale) without being locked to it.
        self.spectrogram_color_lo = Gdk.RGBA()
        self.spectrogram_color_lo.parse("#000000")
        self.spectrogram_color_hi = Gdk.RGBA()
        self.spectrogram_color_hi.parse("#ff3300")
        self.spectrogram_palette = "rainbow"
        self.spectrogram_color_ceiling = 1.0

        # VU Meter style. "bars" is the original look (draw_vu_bar);
        # "led" is a discrete-segment hardware-style meter
        # (draw_vu_led); "needle" is an analog dial gauge
        # (draw_vu_needle) - all three read the same underlying
        # vu_left/vu_right ballistics (draw_vu_meter), just rendered
        # differently.
        self.vu_style = "bars"
        self.vu_segments = 15

        # DVD Bounce state. Position/velocity are in the drawing
        # area's own pixel space and only meaningful once it has a
        # real allocation - dvd_tick bails out until then. Starting
        # velocity is arbitrary (not axis-aligned, so it doesn't
        # immediately bounce straight back the way it came); direction
        # only matters until the first wall hit reorients it.
        self.icon_name = DVD_ICON_CHOICES[0][0]
        self.dvd_speed_scale = 1.0
        self.dvd_base_size = float(DVD_BASE_SIZE)
        self.dvd_reactivity = 1.0
        self.dvd_x = 40.0
        self.dvd_y = 30.0
        self.dvd_vx = 140.0
        self.dvd_vy = 95.0
        self.dvd_size = float(DVD_BASE_SIZE)
        self.dvd_level = 0.0
        self.dvd_color = random_bounce_color()
        self.dvd_bg_color = Gdk.RGBA()
        self.dvd_bg_color.parse("#0d0d0d")
        self.dvd_timer = None
        self._icon_cache_key = None
        self._icon_pixbuf = None

        # Beat pulse state (see dvd_tick) - separate from dvd_level's
        # continuous VU-style ballistics.
        self.dvd_beats_enabled = True
        self.dvd_beat_sensitivity = 1.4
        self.dvd_beat_pulse = 0.0
        self.dvd_beat_cooldown = 0.0
        self.dvd_energy_history = deque(maxlen=60)

        self.toolbar_view.add_top_bar(self.header)

        self.drawing_area = Gtk.DrawingArea()
        self.drawing_area.set_hexpand(True)
        self.drawing_area.set_vexpand(True)
        self.drawing_area.set_draw_func(self.on_draw)

        # Overlaid on the drawing area rather than living in the
        # header - same corner/style, and the same "nav-arrow-button"/
        # "nav-arrow-visible" fade-with-the-header behavior, as the
        # main window's on-canvas favorite/queue buttons (window.py).
        content_overlay = Gtk.Overlay()
        content_overlay.set_child(self.drawing_area)

        # Gtk.MenuButton, not a plain Gtk.Button with a hand-rolled
        # popup()/set_parent() - that manual version (tried first) did
        # not reliably open the popover on click. MenuButton is GTK's
        # own purpose-built "click this, show that popover" widget
        # (already used and working elsewhere in this app, e.g. the
        # hamburger menu), so handing it the popover directly via
        # set_popover() is the standard, robust way to wire this up
        # rather than driving Gtk.Popover.popup()/popdown() by hand.
        # Its CSS node name is "menubutton", not "button", though -
        # style.css's ".nav-arrow-button" rule (previously
        # "button.nav-arrow-button") was widened to match either, or
        # this would render with GTK's bare default menubutton look
        # instead of matching the other overlay controls.
        #
        # build_settings_popover() itself isn't called here, though -
        # it reads kind-specific state (self.peak_hold_seconds,
        # self.pipes_max_pipes, self.scope_time_base,
        # self.vector_persistence...) that, for several kinds, isn't
        # actually assigned until further down in this same method.
        # Calling it this early crashed __init__ with an AttributeError
        # for exactly those kinds (Peak Meter, Oscilloscope, Vector
        # Scope, Pipes) the moment their window was opened - silently,
        # from the caller's side (window.py's aux_window_toggled),
        # since nothing here catches it: the toggle action's state
        # still flips to "on" but the window itself never gets
        # created. See the real popover build + set_popover() call at
        # the end of this method instead, once every kind's state is
        # guaranteed to exist.
        self.settings_button = Gtk.MenuButton(
            icon_name="emblem-system-symbolic",
            tooltip_text="Settings"
        )
        self.settings_button.add_css_class("nav-arrow-button")
        self.settings_button.add_css_class("flat")
        self.settings_button.set_size_request(40, 40)
        self.settings_button.set_halign(Gtk.Align.END)
        self.settings_button.set_valign(Gtk.Align.END)
        self.settings_button.set_margin_end(10)
        self.settings_button.set_margin_bottom(10)

        content_overlay.add_overlay(self.settings_button)

        # In the header, same placement as MirrorWindow's own
        # fullscreen button (mirror_window.py) - not an overlay button
        # like Settings, so it fades with the header itself rather
        # than needing its own visibility state.
        self.fullscreen_button = Gtk.Button(
            icon_name="view-fullscreen-symbolic",
            tooltip_text="Toggle Fullscreen",
            action_name="win.toggle-fullscreen"
        )

        self.header.pack_end(self.fullscreen_button)

        self.toolbar_view.set_content(content_overlay)

        self.set_content(self.toolbar_view)

        # Drag-to-move from anywhere in the content - scoped to just
        # the drawing area (not content_overlay, its parent) so this
        # gesture's own recognizer never sits in the same
        # ancestor/descendant propagation path as a click landing on
        # the settings button, which is a sibling overlay widget, not
        # a descendant of drawing_area. Originally attached to
        # content_overlay itself, which turned out to make the
        # settings button unclickable in practice, even though a plain
        # GestureDrag "shouldn't" claim a no-movement click in
        # principle - moving it here removes the ambiguity outright
        # rather than relying on gesture-arbitration internals.
        # 3D Terrain Spectrogram, 3D Waterfall, and Pipes spend their
        # drag gesture on rotating the camera instead (see
        # on_terrain_drag_update/on_waterfall_drag_update/
        # on_pipes_drag_update) - the kinds here where dragging the
        # canvas has a more useful meaning than moving the window.
        # Each can still be moved via its header bar, same as every
        # window's native CSD behavior; it just doesn't get the drag-
        # from-anywhere convenience every other aux window kind has.
        if kind == "terrain":

            rotate_gesture = Gtk.GestureDrag()
            rotate_gesture.set_button(Gdk.BUTTON_PRIMARY)
            rotate_gesture.connect("drag-begin", self.on_terrain_drag_begin)
            rotate_gesture.connect("drag-update", self.on_terrain_drag_update)
            self.drawing_area.add_controller(rotate_gesture)

            zoom_scroll = Gtk.EventControllerScroll.new(
                Gtk.EventControllerScrollFlags.VERTICAL
            )
            zoom_scroll.connect("scroll", self.on_terrain_scroll)
            self.drawing_area.add_controller(zoom_scroll)

        elif kind == "waterfall":

            rotate_gesture = Gtk.GestureDrag()
            rotate_gesture.set_button(Gdk.BUTTON_PRIMARY)
            rotate_gesture.connect("drag-begin", self.on_waterfall_drag_begin)
            rotate_gesture.connect("drag-update", self.on_waterfall_drag_update)
            self.drawing_area.add_controller(rotate_gesture)

            zoom_scroll = Gtk.EventControllerScroll.new(
                Gtk.EventControllerScrollFlags.VERTICAL
            )
            zoom_scroll.connect("scroll", self.on_waterfall_scroll)
            self.drawing_area.add_controller(zoom_scroll)

        elif kind == "pipes":

            rotate_gesture = Gtk.GestureDrag()
            rotate_gesture.set_button(Gdk.BUTTON_PRIMARY)
            rotate_gesture.connect("drag-begin", self.on_pipes_drag_begin)
            rotate_gesture.connect("drag-update", self.on_pipes_drag_update)
            rotate_gesture.connect("drag-end", self.on_pipes_drag_end)
            self.drawing_area.add_controller(rotate_gesture)

            zoom_scroll = Gtk.EventControllerScroll.new(
                Gtk.EventControllerScrollFlags.VERTICAL
            )
            zoom_scroll.connect("scroll", self.on_pipes_scroll)
            self.drawing_area.add_controller(zoom_scroll)

        else:

            drag_gesture = Gtk.GestureDrag()
            drag_gesture.set_button(Gdk.BUTTON_PRIMARY)
            drag_gesture.connect("drag-begin", self.on_drag_begin)
            self.drawing_area.add_controller(drag_gesture)

        # Belt-and-suspenders click-outside-to-close for the settings
        # popover, on top of Gtk.Popover's own default autohide - CAPTURE
        # phase on the window itself so it sees a press anywhere before
        # any other controller does, but doesn't claim the sequence
        # (no set_state call), so normal handling elsewhere - the
        # drag/nav gestures, the header's own buttons, the popover's
        # own content - still runs afterward exactly as before. A
        # click that lands on settings_button or inside the popover's
        # own surface never reaches this handler in the first place -
        # each owns its own hit region - so this only ever fires for
        # genuine "elsewhere" clicks, matching what popdown() on an
        # already-closed popover safely no-ops on anyway.
        dismiss_popover_click = Gtk.GestureClick()
        dismiss_popover_click.set_button(0)
        dismiss_popover_click.set_propagation_phase(Gtk.PropagationPhase.CAPTURE)
        dismiss_popover_click.connect("pressed", self.on_window_pressed)
        self.add_controller(dismiss_popover_click)

        # Same fullscreen action/icon-sync/Escape-to-exit pattern as
        # MirrorWindow (mirror_window.py).
        fullscreen_action = Gio.SimpleAction.new("toggle-fullscreen", None)
        fullscreen_action.connect("activate", self.toggle_fullscreen)
        self.add_action(fullscreen_action)

        self.connect("notify::fullscreened", self.update_fullscreen_button_icon)

        escape_controller = Gtk.EventControllerKey()
        escape_controller.connect("key-pressed", self.on_key_pressed)
        self.add_controller(escape_controller)

        # Latest de-interleaved channel samples, as -1..1 floats -
        # replaced wholesale on every push_audio() rather than
        # accumulated, since a draw only ever needs "what's playing
        # right now" (same as the main visualizer's own PCM handling).
        self.left = []
        self.right = []

        # The VU meter's displayed level (and the spectrum's bars)
        # decay a little on every draw rather than snapping straight
        # to each chunk's own peak - plain per-chunk peaks arrive tens
        # of times/sec and would flicker illegibly. Fast attack (jumps
        # up instantly), slow release (falls gradually) - standard
        # VU-meter ballistics.
        self.vu_left = 0.0
        self.vu_right = 0.0
        self.bar_levels = []

        # VU Meter lagging peak-hold caps (Bars/LED styles - Needle
        # has no natural "hold line" the way a real analog dial
        # doesn't either) - same update_peak_hold/peak_hold_seconds
        # generic logic Peak Meter and Spectrum's own hold markers
        # already reuse.
        self.vu_peak_hold = True
        self.vu_hold_left = 0.0
        self.vu_hold_right = 0.0
        self.vu_hold_left_time = 0.0
        self.vu_hold_right_time = 0.0

        # Spectrum-only lagging peak caps - a small marker above each
        # bar that jumps to a new peak instantly then falls back down
        # on its own, the classic hardware EQ "peak indicator" look.
        # Reuses update_peak_hold/peak_hold_seconds, the same generic
        # peak-hold logic and settings field the Peak Meter window
        # already has (each window instance keeps its own copy of
        # peak_hold_seconds, so adjusting it here doesn't touch a Peak
        # Meter window's own value).
        self.spectrum_peak_hold = True
        self.spectrum_bar_holds = []
        self.spectrum_bar_hold_times = []

        # "Smooth" is a continuous curve through the same per-bar
        # levels (render_spectrum_smooth) instead of discrete
        # rectangles (render_spectrum_bars) - same underlying data
        # either way, just a different render function selected at
        # draw time (on_draw).
        self.spectrum_style = "bars"
        self.spectrum_height_scale = 1.0

        # Peak Meter state - deliberately separate from vu_left/right
        # above rather than reusing them: a peak meter tracks the true
        # instantaneous sample peak (raw abs(), no VU_GAIN) rather than
        # RMS, and adds its own peak-hold indicator (a marker that
        # jumps to a new peak instantly, then lingers for
        # peak_hold_seconds before falling back down at
        # peak_hold_fall_rate) - a real second reading, not just a
        # different skin on the VU Meter (see draw_peak_meter).
        self.peak_left = 0.0
        self.peak_right = 0.0
        self.peak_hold_left = 0.0
        self.peak_hold_right = 0.0
        self.peak_hold_left_time = 0.0
        self.peak_hold_right_time = 0.0
        self.peak_hold_seconds = 1.5
        self.peak_hold_fall_rate = 0.6
        # Off by default - canvas_foreground_rgba(1.0) (theme-reactive
        # near-black/near-white) is the existing default look for the
        # hold marker on VU Meter/Peak Meter/Spectrum, unchanged unless
        # the user opts into a fixed custom color instead.
        self.peak_hold_color_custom = False
        self.peak_hold_color = Gdk.RGBA()
        self.peak_hold_color.parse("#ffffff")

        # Rolling mono buffer feeding the spectrum FFT - audio chunks
        # arrive at whatever size GStreamer hands over (see
        # window.py's on_audio_sample), not necessarily FFT_SIZE, so
        # this keeps the most recent FFT_SIZE samples across calls
        # rather than requiring one chunk to be exactly that long.
        self.spectrum_buffer = []

        # Spectrogram-only: one entry per column already drawn (each a
        # list of per-bar levels), oldest first - newest column is
        # appended at the right edge and drawn there, scrolling left
        # as older ones fall off (see draw_spectrogram).
        self.spectrogram_columns = deque(maxlen=SPECTROGRAM_COLUMNS)
        self.last_spectrogram_frame_time = 0.0

        # 3D Terrain Spectrogram - terrain_rows holds the same shape of
        # data as spectrogram_columns (one list of per-bin levels per
        # row, oldest first). terrain_surface/terrain_dirty are a
        # render cache (see draw_terrain) - the expensive part (project
        # every point through the current camera rotation, sort rows
        # by depth, fill/stroke each one) only actually re-runs when a
        # new row arrives or the camera rotates, not on every redraw
        # request, unlike the flat Spectrogram (see the TERRAIN_ROWS
        # comment above). Rotation angles default to a pleasant 3/4
        # oblique view rather than looking straight down.
        self.terrain_rows = deque(maxlen=TERRAIN_ROWS)
        self.last_terrain_frame_time = 0.0
        self.terrain_azimuth = math.radians(35)
        self.terrain_elevation = math.radians(28)
        self.terrain_rotate_start = (self.terrain_azimuth, self.terrain_elevation)
        self.terrain_surface = None
        self.terrain_dirty = True
        self.terrain_zoom = 1.0
        self.terrain_show_axes = False

        # Ridge color gradient, by that row's own loudness (see
        # render_terrain_surface) - replaces the plain single Color
        # setting the other kinds use, since a height/loudness-colored
        # terrain (classic elevation-map style) reads far better than
        # one flat hue across every ridge regardless of how loud it
        # was.
        self.terrain_color_lo = Gdk.RGBA()
        self.terrain_color_lo.parse("#0a1a4d")
        self.terrain_color_hi = Gdk.RGBA()
        self.terrain_color_hi.parse("#ff9933")
        self.terrain_palette = "rainbow"
        self.terrain_color_ceiling = 1.0

        # 3D Waterfall - same rows-of-FFT-magnitude/camera-rotation/
        # render-cache shape as Terrain above (see waterfall_surface/
        # waterfall_dirty in draw_waterfall), just flat rather than
        # height-extruded. Own Low/High gradient (not shared with
        # Terrain's or the flat Spectrogram's) since it's a genuinely
        # separate window/instance whenever more than one of these is
        # open at once.
        self.waterfall_rows = deque(maxlen=WATERFALL_ROWS)
        self.last_waterfall_frame_time = 0.0
        self.waterfall_azimuth = math.radians(35)
        self.waterfall_elevation = math.radians(55)
        self.waterfall_rotate_start = (
            self.waterfall_azimuth, self.waterfall_elevation
        )
        self.waterfall_surface = None
        self.waterfall_dirty = True
        self.waterfall_color_lo = Gdk.RGBA()
        self.waterfall_color_lo.parse("#000000")
        self.waterfall_color_hi = Gdk.RGBA()
        self.waterfall_color_hi.parse("#ff3300")
        self.waterfall_palette = "rainbow"
        self.waterfall_color_ceiling = 1.0
        self.waterfall_textured = True

        # How much magnitude also lifts each grid point in Z, on top
        # of the per-cell coloring - 0 keeps the original perfectly
        # flat surface; >0 turns it into a genuine height-mapped mesh
        # (Terrain's own height idea, combined with Waterfall's per-
        # cell color/texture rather than Terrain's per-row silhouette
        # fill), requested directly.
        self.waterfall_height_scale = 0.5
        self.waterfall_zoom = 1.0
        self.waterfall_show_axes = False

        # Pipes - pipes_occupied tracks every grid cell any pipe has
        # ever passed through since the last reset (collision check
        # for new moves); pipes_segments is every laid segment, drawn
        # every frame until reset_pipes clears it - segments from dead
        # pipes are deliberately left in place rather than removed,
        # same as the reference screensaver's grid staying filled
        # until a full reset. pipes_active holds the pipes still
        # growing right now. Populated by reset_pipes() once
        # everything else below is set up (needs pipes_max_pipes to
        # know how many to spawn).
        self.pipes_azimuth = math.radians(35)
        self.pipes_elevation = math.radians(28)
        self.pipes_rotate_start = (self.pipes_azimuth, self.pipes_elevation)
        self.pipes_zoom = 1.0
        self.pipes_show_grid = False
        self.pipes_max_pipes = 4
        self.pipes_speed_scale = 1.0
        self.pipes_reactivity = 1.0
        self.pipes_occupied = set()
        self.pipes_segments = []
        self.pipes_active = []
        self.pipes_timer = None
        self.pipes_next_band = 0

        # Settings-adjustable per-band colors - spawn_pipe reads these
        # (not PIPES_BAND_COLOR_HEXES directly), so changing one only
        # affects pipes spawned from then on, same as every other
        # per-pipe property (speed, etc.) already only applies going
        # forward rather than retroactively.
        self.pipes_band_colors = []
        for hex_color in PIPES_BAND_COLOR_HEXES:
            color = Gdk.RGBA()
            color.parse(hex_color)
            self.pipes_band_colors.append(color)

        # Each active pipe's step_timer (see spawn_pipe) is its own
        # accumulator now, not a single shared one - that's what lets
        # different pipes actually move at different speeds. Per-band
        # levels (see update_pipes_band_levels) are decayed the same
        # VU-ballistics way as everything else audio-reactive in this
        # file, one per PIPES_BANDS entry.
        self.pipes_band_levels = [0.0] * len(PIPES_BANDS)
        self.last_pipes_band_frame_time = 0.0

        # Old batch fades out over pipes_fade_seconds instead of
        # vanishing instantly on reset (automatic or the settings
        # Reset button) - see reset_pipes/draw_pipes.
        self.pipes_fade_enabled = True
        self.pipes_fade_seconds = 1.5
        self.pipes_fading_segments = []
        self.pipes_fade_start_time = 0.0

        # Slow ambient auto-rotation, paused while a manual rotate
        # drag is in progress (pipes_dragging) so the two don't fight
        # - resumes seamlessly from wherever the drag left the camera,
        # since it just keeps incrementing pipes_azimuth from its
        # current value rather than resetting anything.
        self.pipes_auto_rotate = True
        self.pipes_rotate_speed = 6.0
        self.pipes_dragging = False

        # Beat-triggered pipe burst - a rolling-average energy-jump
        # detector, the same idea as DVD Bounce's own beat_pulse
        # (dvd_tick), kept separate/self-contained here rather than
        # shared, same reasoning as that one: no existing path
        # forwards a single detection to more than one consumer.
        self.pipes_beats_enabled = True
        self.pipes_beat_sensitivity = 1.4
        self.pipes_beat_cooldown = 0.0
        self.pipes_energy_history = deque(maxlen=60)

        # Decaying extra rotation velocity applied on top of
        # pipes_rotate_speed/pipes_elevation on a detected beat (see
        # pipes_tick) - "x and y rotation" reacting to beats too, on
        # request, not just the pipes themselves. Independently
        # toggleable from the pipe-spawn burst (pipes_beats_enabled)
        # via its own switch.
        self.pipes_beat_rotation_enabled = True
        self.pipes_beat_spin_boost = 0.0
        self.pipes_beat_tilt_velocity = 0.0

        # Same idea, a brief zoom punch-in on a detected beat, added
        # on top of the user's own Zoom setting (draw_pipes) rather
        # than overwriting it - also independently toggleable.
        self.pipes_beat_zoom_enabled = True
        self.pipes_beat_zoom_boost = 0.0

        # Each pipe's tube width pulses with its own band's live level
        # (draw_pipes) when this is on - a second, more continuous
        # reinforcement of the per-band reactivity on top of speed.
        self.pipes_pulse_width = True
        self.pipes_pulse_color = True

        # A cheap pseudo-3D "glossy tube" trick, not real lighting -
        # Cairo has no shading/lighting model to actually compute this
        # from. A thin, semi-transparent light stroke offset to one
        # side of each segment's main colored line (draw_pipes),
        # mimicking a specular highlight running along the top of a
        # cylinder, the same technique commonly used for "tube-style"
        # chart lines generally.
        self.pipes_tube_shading = True
        self.pipes_base_width = 6.0

        # Oscilloscope-only rolling buffers (see push_audio/
        # draw_oscilloscope) - unlike self.left/self.right (replaced
        # wholesale every push_audio, "what's playing right now"),
        # these accumulate across calls so a trigger point can be
        # searched for with a full time-base window of samples after
        # it, regardless of how small a given audio chunk is.
        self.scope_left = deque(maxlen=SCOPE_BUFFER_SAMPLES)
        self.scope_right = deque(maxlen=SCOPE_BUFFER_SAMPLES)
        self.scope_time_base = SCOPE_DEFAULT_TIME_BASE
        self.scope_trigger = True

        # Vector Scope (goniometer) - vector_surface is an offscreen
        # Cairo ImageSurface accumulating a fading trail across frames
        # (see draw_vector_scope), not something GTK's own draw_func
        # gives for free (each on_draw call gets a fresh render target
        # with no memory of the previous frame's pixels) - recreated
        # lazily whenever the drawing area's own size changes.
        # vector_persistence is how much of the previous frame's trail
        # survives each new one (same "higher = slower decay" meaning
        # as the Decay setting elsewhere in this file).
        self.vector_surface = None
        self.vector_persistence = 0.90
        self.vector_dot_size = 1.2

        motion = Gtk.EventControllerMotion()
        motion.connect("motion", self.mouse_move)
        self.add_controller(motion)

        header_motion = Gtk.EventControllerMotion()
        header_motion.connect("enter", self.toolbar_enter)
        header_motion.connect("leave", self.toolbar_leave)
        self.header.add_controller(header_motion)

        self.connect("close-request", self.on_close_request)

        if self.kind == "dvd":
            self.dvd_timer = GLib.timeout_add(
                DVD_TICK_INTERVAL_MS, self.dvd_tick
            )

        if self.kind == "pipes":
            self.reset_pipes()
            self.pipes_timer = GLib.timeout_add(
                PIPES_TICK_INTERVAL_MS, self.pipes_tick
            )

        # Built here, not up near settings_button's own construction -
        # see the comment there for why (every kind's state needs to
        # exist first, and this is the first point in __init__ where
        # that's guaranteed).
        self.settings_popover = self.build_settings_popover()
        self.settings_button.set_popover(self.settings_popover)

    def build_settings_popover(self):

        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        box.set_margin_start(10)
        box.set_margin_end(10)
        box.set_margin_top(10)
        box.set_margin_bottom(10)

        if self.kind in (
            "xy", "spectrum", "vu", "oscilloscope", "vectorscope"
        ):

            color_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)

            color_label = Gtk.Label(label="Color", xalign=0, hexpand=True)
            color_row.append(color_label)

            color_button = Gtk.ColorDialogButton(dialog=Gtk.ColorDialog())
            color_button.set_rgba(self.color)

            color_button.connect(
                "notify::rgba",
                self.on_color_changed
            )

            color_row.append(color_button)
            box.append(color_row)

            color_mode_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)

            color_mode_label = Gtk.Label(label="Color Mode", xalign=0, hexpand=True)
            color_mode_row.append(color_mode_label)

            color_mode_dropdown = Gtk.DropDown.new_from_strings(
                [label for _, label in COLOR_MODE_CHOICES]
            )

            current_color_mode_index = next(
                (
                    i for i, (key, _) in enumerate(COLOR_MODE_CHOICES)
                    if key == self.color_mode
                ),
                0
            )
            color_mode_dropdown.set_selected(current_color_mode_index)

            color_mode_dropdown.connect(
                "notify::selected",
                self.on_color_mode_changed
            )

            color_mode_row.append(color_mode_dropdown)
            box.append(color_mode_row)

        if self.kind == "vu":

            style_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)

            style_label = Gtk.Label(label="Style", xalign=0, hexpand=True)
            style_row.append(style_label)

            style_dropdown = Gtk.DropDown.new_from_strings(
                [label for _, label in VU_STYLE_CHOICES]
            )

            current_style_index = next(
                (
                    i for i, (key, _) in enumerate(VU_STYLE_CHOICES)
                    if key == self.vu_style
                ),
                0
            )
            style_dropdown.set_selected(current_style_index)

            style_dropdown.connect(
                "notify::selected",
                self.on_vu_style_changed
            )

            style_row.append(style_dropdown)
            box.append(style_row)

            vu_hold_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)

            vu_hold_label = Gtk.Label(label="Peak Hold", xalign=0, hexpand=True)
            vu_hold_row.append(vu_hold_label)

            vu_hold_switch = Gtk.Switch()
            vu_hold_switch.set_active(self.vu_peak_hold)
            vu_hold_switch.set_valign(Gtk.Align.CENTER)

            vu_hold_switch.connect(
                "notify::active",
                self.on_vu_peak_hold_changed
            )

            vu_hold_row.append(vu_hold_switch)
            box.append(vu_hold_row)

            segments_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)

            segments_label = Gtk.Label(
                label="LED Segments", xalign=0, hexpand=True
            )
            segments_row.append(segments_label)

            segments_spin = Gtk.SpinButton.new_with_range(4, 30, 1)
            segments_spin.set_value(self.vu_segments)

            segments_spin.connect(
                "value-changed",
                self.on_vu_segments_changed
            )

            segments_row.append(segments_spin)
            box.append(segments_row)

        if self.kind in ("spectrum", "spectrogram", "terrain", "waterfall"):

            bars_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)

            bars_label = Gtk.Label(
                label={
                    "spectrum": "Bars",
                    "spectrogram": "Frequency Bins",
                    "terrain": "Ridge Points",
                    "waterfall": "Frequency Bins",
                }[self.kind],
                xalign=0,
                hexpand=True
            )
            bars_row.append(bars_label)

            bars_spin = Gtk.SpinButton.new_with_range(4, 64, 1)
            bars_spin.set_value(self.num_bars)

            bars_spin.connect(
                "value-changed",
                self.on_bars_changed
            )

            bars_row.append(bars_spin)
            box.append(bars_row)

        if self.kind == "terrain":

            terrain_zoom_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)

            terrain_zoom_label = Gtk.Label(label="Zoom", xalign=0, hexpand=True)
            terrain_zoom_row.append(terrain_zoom_label)

            terrain_zoom_scale = Gtk.Scale.new_with_range(
                Gtk.Orientation.HORIZONTAL, 0.4, 3.0, 0.05
            )
            terrain_zoom_scale.set_value(self.terrain_zoom)
            terrain_zoom_scale.set_size_request(120, -1)
            terrain_zoom_scale.set_draw_value(False)

            terrain_zoom_scale.connect(
                "value-changed",
                self.on_terrain_zoom_changed
            )

            terrain_zoom_row.append(terrain_zoom_scale)
            box.append(terrain_zoom_row)

            terrain_axes_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)

            terrain_axes_label = Gtk.Label(label="Show Axes", xalign=0, hexpand=True)
            terrain_axes_row.append(terrain_axes_label)

            terrain_axes_switch = Gtk.Switch()
            terrain_axes_switch.set_active(self.terrain_show_axes)
            terrain_axes_switch.set_valign(Gtk.Align.CENTER)

            terrain_axes_switch.connect(
                "notify::active",
                self.on_terrain_show_axes_changed
            )

            terrain_axes_row.append(terrain_axes_switch)
            box.append(terrain_axes_row)

            reset_view_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)

            reset_view_button = Gtk.Button(label="Reset View")
            reset_view_button.set_hexpand(True)

            reset_view_button.connect(
                "clicked",
                self.on_terrain_reset_view_clicked
            )

            reset_view_row.append(reset_view_button)
            box.append(reset_view_row)

        if self.kind == "waterfall":

            waterfall_palette_row = Gtk.Box(
                orientation=Gtk.Orientation.HORIZONTAL, spacing=8
            )

            waterfall_palette_label = Gtk.Label(
                label="Palette", xalign=0, hexpand=True
            )
            waterfall_palette_row.append(waterfall_palette_label)

            waterfall_palette_dropdown = Gtk.DropDown.new_from_strings(
                [label for _, label in PALETTE_CHOICES]
            )

            current_waterfall_palette_index = next(
                (
                    i for i, (key, _) in enumerate(PALETTE_CHOICES)
                    if key == self.waterfall_palette
                ),
                0
            )
            waterfall_palette_dropdown.set_selected(current_waterfall_palette_index)

            waterfall_palette_dropdown.connect(
                "notify::selected",
                self.on_waterfall_palette_changed
            )

            waterfall_palette_row.append(waterfall_palette_dropdown)
            box.append(waterfall_palette_row)

            waterfall_textured_row = Gtk.Box(
                orientation=Gtk.Orientation.HORIZONTAL, spacing=8
            )

            waterfall_textured_label = Gtk.Label(
                label="Textured", xalign=0, hexpand=True
            )
            waterfall_textured_row.append(waterfall_textured_label)

            waterfall_textured_switch = Gtk.Switch()
            waterfall_textured_switch.set_active(self.waterfall_textured)
            waterfall_textured_switch.set_valign(Gtk.Align.CENTER)

            waterfall_textured_switch.connect(
                "notify::active",
                self.on_waterfall_textured_changed
            )

            waterfall_textured_row.append(waterfall_textured_switch)
            box.append(waterfall_textured_row)

            waterfall_lo_color_row = Gtk.Box(
                orientation=Gtk.Orientation.HORIZONTAL, spacing=8
            )

            waterfall_lo_color_label = Gtk.Label(
                label="Low Color", xalign=0, hexpand=True
            )
            waterfall_lo_color_row.append(waterfall_lo_color_label)

            waterfall_lo_color_button = Gtk.ColorDialogButton(
                dialog=Gtk.ColorDialog()
            )
            waterfall_lo_color_button.set_rgba(self.waterfall_color_lo)

            waterfall_lo_color_button.connect(
                "notify::rgba",
                self.on_waterfall_color_lo_changed
            )

            waterfall_lo_color_row.append(waterfall_lo_color_button)
            box.append(waterfall_lo_color_row)

            waterfall_hi_color_row = Gtk.Box(
                orientation=Gtk.Orientation.HORIZONTAL, spacing=8
            )

            waterfall_hi_color_label = Gtk.Label(
                label="High Color", xalign=0, hexpand=True
            )
            waterfall_hi_color_row.append(waterfall_hi_color_label)

            waterfall_hi_color_button = Gtk.ColorDialogButton(
                dialog=Gtk.ColorDialog()
            )
            waterfall_hi_color_button.set_rgba(self.waterfall_color_hi)

            waterfall_hi_color_button.connect(
                "notify::rgba",
                self.on_waterfall_color_hi_changed
            )

            waterfall_hi_color_row.append(waterfall_hi_color_button)
            box.append(waterfall_hi_color_row)

            waterfall_ceiling_row = Gtk.Box(
                orientation=Gtk.Orientation.HORIZONTAL, spacing=8
            )

            waterfall_ceiling_label = Gtk.Label(
                label="High Level", xalign=0, hexpand=True
            )
            waterfall_ceiling_row.append(waterfall_ceiling_label)

            waterfall_ceiling_scale = Gtk.Scale.new_with_range(
                Gtk.Orientation.HORIZONTAL, 0.1, 1.0, 0.05
            )
            waterfall_ceiling_scale.set_value(self.waterfall_color_ceiling)
            waterfall_ceiling_scale.set_size_request(120, -1)
            waterfall_ceiling_scale.set_draw_value(False)

            waterfall_ceiling_scale.connect(
                "value-changed",
                self.on_waterfall_color_ceiling_changed
            )

            waterfall_ceiling_row.append(waterfall_ceiling_scale)
            box.append(waterfall_ceiling_row)

            waterfall_reset_view_row = Gtk.Box(
                orientation=Gtk.Orientation.HORIZONTAL, spacing=8
            )

            waterfall_reset_view_button = Gtk.Button(label="Reset View")
            waterfall_reset_view_button.set_hexpand(True)

            waterfall_reset_view_button.connect(
                "clicked",
                self.on_waterfall_reset_view_clicked
            )

            waterfall_reset_view_row.append(waterfall_reset_view_button)
            box.append(waterfall_reset_view_row)

            waterfall_height_row = Gtk.Box(
                orientation=Gtk.Orientation.HORIZONTAL, spacing=8
            )

            waterfall_height_label = Gtk.Label(
                label="Height", xalign=0, hexpand=True
            )
            waterfall_height_row.append(waterfall_height_label)

            waterfall_height_scale_widget = Gtk.Scale.new_with_range(
                Gtk.Orientation.HORIZONTAL, 0.0, 1.5, 0.05
            )
            waterfall_height_scale_widget.set_value(self.waterfall_height_scale)
            waterfall_height_scale_widget.set_size_request(120, -1)
            waterfall_height_scale_widget.set_draw_value(False)

            waterfall_height_scale_widget.connect(
                "value-changed",
                self.on_waterfall_height_changed
            )

            waterfall_height_row.append(waterfall_height_scale_widget)
            box.append(waterfall_height_row)

            waterfall_zoom_row = Gtk.Box(
                orientation=Gtk.Orientation.HORIZONTAL, spacing=8
            )

            waterfall_zoom_label = Gtk.Label(label="Zoom", xalign=0, hexpand=True)
            waterfall_zoom_row.append(waterfall_zoom_label)

            waterfall_zoom_scale = Gtk.Scale.new_with_range(
                Gtk.Orientation.HORIZONTAL, 0.4, 3.0, 0.05
            )
            waterfall_zoom_scale.set_value(self.waterfall_zoom)
            waterfall_zoom_scale.set_size_request(120, -1)
            waterfall_zoom_scale.set_draw_value(False)

            waterfall_zoom_scale.connect(
                "value-changed",
                self.on_waterfall_zoom_changed
            )

            waterfall_zoom_row.append(waterfall_zoom_scale)
            box.append(waterfall_zoom_row)

            waterfall_axes_row = Gtk.Box(
                orientation=Gtk.Orientation.HORIZONTAL, spacing=8
            )

            waterfall_axes_label = Gtk.Label(
                label="Show Axes", xalign=0, hexpand=True
            )
            waterfall_axes_row.append(waterfall_axes_label)

            waterfall_axes_switch = Gtk.Switch()
            waterfall_axes_switch.set_active(self.waterfall_show_axes)
            waterfall_axes_switch.set_valign(Gtk.Align.CENTER)

            waterfall_axes_switch.connect(
                "notify::active",
                self.on_waterfall_show_axes_changed
            )

            waterfall_axes_row.append(waterfall_axes_switch)
            box.append(waterfall_axes_row)

        if self.kind in ("vu", "spectrum", "peak"):

            decay_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)

            decay_label = Gtk.Label(label="Decay", xalign=0, hexpand=True)
            decay_row.append(decay_label)

            decay_scale = Gtk.Scale.new_with_range(
                Gtk.Orientation.HORIZONTAL, 0.5, 0.98, 0.01
            )
            decay_scale.set_value(self.decay)
            decay_scale.set_size_request(120, -1)
            decay_scale.set_draw_value(False)

            decay_scale.connect(
                "value-changed",
                self.on_decay_changed
            )

            decay_row.append(decay_scale)
            box.append(decay_row)

        if self.kind == "spectrum":

            spectrum_style_row = Gtk.Box(
                orientation=Gtk.Orientation.HORIZONTAL, spacing=8
            )

            spectrum_style_label = Gtk.Label(
                label="Style", xalign=0, hexpand=True
            )
            spectrum_style_row.append(spectrum_style_label)

            spectrum_style_dropdown = Gtk.DropDown.new_from_strings(
                [label for _, label in SPECTRUM_STYLE_CHOICES]
            )

            current_spectrum_style_index = next(
                (
                    i for i, (key, _) in enumerate(SPECTRUM_STYLE_CHOICES)
                    if key == self.spectrum_style
                ),
                0
            )
            spectrum_style_dropdown.set_selected(current_spectrum_style_index)

            spectrum_style_dropdown.connect(
                "notify::selected",
                self.on_spectrum_style_changed
            )

            spectrum_style_row.append(spectrum_style_dropdown)
            box.append(spectrum_style_row)

            spectrum_hold_row = Gtk.Box(
                orientation=Gtk.Orientation.HORIZONTAL, spacing=8
            )

            spectrum_hold_label = Gtk.Label(
                label="Peak Hold", xalign=0, hexpand=True
            )
            spectrum_hold_row.append(spectrum_hold_label)

            spectrum_hold_switch = Gtk.Switch()
            spectrum_hold_switch.set_active(self.spectrum_peak_hold)
            spectrum_hold_switch.set_valign(Gtk.Align.CENTER)

            spectrum_hold_switch.connect(
                "notify::active",
                self.on_spectrum_peak_hold_changed
            )

            spectrum_hold_row.append(spectrum_hold_switch)
            box.append(spectrum_hold_row)

            height_scale_row = Gtk.Box(
                orientation=Gtk.Orientation.HORIZONTAL, spacing=8
            )

            height_scale_label = Gtk.Label(
                label="Height Scale", xalign=0, hexpand=True
            )
            height_scale_row.append(height_scale_label)

            # Goes past 1.0 on purpose (a gain-style control, not a
            # 0-100% fit-to-canvas one) - bars/the smooth curve simply
            # clip at the canvas edge once a level's scaled height
            # exceeds it, same as an analog meter pinned past its dial.
            height_scale_scale = Gtk.Scale.new_with_range(
                Gtk.Orientation.HORIZONTAL, 0.25, 2.5, 0.05
            )
            height_scale_scale.set_value(self.spectrum_height_scale)
            height_scale_scale.set_size_request(120, -1)
            height_scale_scale.set_draw_value(False)

            height_scale_scale.connect(
                "value-changed",
                self.on_spectrum_height_scale_changed
            )

            height_scale_row.append(height_scale_scale)
            box.append(height_scale_row)

        if self.kind in ("peak", "spectrum", "vu"):

            hold_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)

            hold_label = Gtk.Label(label="Peak Hold Time", xalign=0, hexpand=True)
            hold_row.append(hold_label)

            hold_scale = Gtk.Scale.new_with_range(
                Gtk.Orientation.HORIZONTAL, 0.2, 4.0, 0.1
            )
            hold_scale.set_value(self.peak_hold_seconds)
            hold_scale.set_size_request(120, -1)
            hold_scale.set_draw_value(False)

            hold_scale.connect(
                "value-changed",
                self.on_peak_hold_seconds_changed
            )

            hold_row.append(hold_scale)
            box.append(hold_row)

            hold_color_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)

            hold_color_label = Gtk.Label(
                label="Peak Hold Color", xalign=0, hexpand=True
            )
            hold_color_row.append(hold_color_label)

            hold_color_switch = Gtk.Switch()
            hold_color_switch.set_active(self.peak_hold_color_custom)
            hold_color_switch.set_valign(Gtk.Align.CENTER)
            hold_color_switch.set_tooltip_text(
                "Off follows the theme (near-black/near-white), same "
                "as this window's other structural marks."
            )

            hold_color_switch.connect(
                "notify::active",
                self.on_peak_hold_color_custom_changed
            )

            hold_color_row.append(hold_color_switch)

            hold_color_button = Gtk.ColorDialogButton(dialog=Gtk.ColorDialog())
            hold_color_button.set_valign(Gtk.Align.CENTER)
            hold_color_button.set_rgba(self.peak_hold_color)

            hold_color_button.connect(
                "notify::rgba",
                self.on_peak_hold_color_changed
            )

            hold_color_row.append(hold_color_button)
            box.append(hold_color_row)

        if self.kind == "oscilloscope":

            time_base_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)

            time_base_label = Gtk.Label(label="Time Base", xalign=0, hexpand=True)
            time_base_row.append(time_base_label)

            time_base_scale = Gtk.Scale.new_with_range(
                Gtk.Orientation.HORIZONTAL, 256, SCOPE_BUFFER_SAMPLES / 2, 32
            )
            time_base_scale.set_value(self.scope_time_base)
            time_base_scale.set_size_request(120, -1)
            time_base_scale.set_draw_value(False)

            time_base_scale.connect(
                "value-changed",
                self.on_scope_time_base_changed
            )

            time_base_row.append(time_base_scale)
            box.append(time_base_row)

            trigger_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)

            trigger_label = Gtk.Label(label="Trigger", xalign=0, hexpand=True)
            trigger_row.append(trigger_label)

            trigger_switch = Gtk.Switch()
            trigger_switch.set_active(self.scope_trigger)
            trigger_switch.set_valign(Gtk.Align.CENTER)

            trigger_switch.connect(
                "notify::active",
                self.on_scope_trigger_changed
            )

            trigger_row.append(trigger_switch)
            box.append(trigger_row)

        if self.kind == "vectorscope":

            persistence_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)

            persistence_label = Gtk.Label(
                label="Persistence", xalign=0, hexpand=True
            )
            persistence_row.append(persistence_label)

            persistence_scale = Gtk.Scale.new_with_range(
                Gtk.Orientation.HORIZONTAL, 0.5, 0.98, 0.01
            )
            persistence_scale.set_value(self.vector_persistence)
            persistence_scale.set_size_request(120, -1)
            persistence_scale.set_draw_value(False)

            persistence_scale.connect(
                "value-changed",
                self.on_vector_persistence_changed
            )

            persistence_row.append(persistence_scale)
            box.append(persistence_row)

            dot_size_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)

            dot_size_label = Gtk.Label(label="Dot Size", xalign=0, hexpand=True)
            dot_size_row.append(dot_size_label)

            dot_size_scale = Gtk.Scale.new_with_range(
                Gtk.Orientation.HORIZONTAL, 0.5, 4.0, 0.1
            )
            dot_size_scale.set_value(self.vector_dot_size)
            dot_size_scale.set_size_request(120, -1)
            dot_size_scale.set_draw_value(False)

            dot_size_scale.connect(
                "value-changed",
                self.on_vector_dot_size_changed
            )

            dot_size_row.append(dot_size_scale)
            box.append(dot_size_row)

        if self.kind in (
            "spectrum", "spectrogram", "vu", "peak", "oscilloscope", "vectorscope",
            "xy"
        ):

            labels_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)

            labels_label = Gtk.Label(
                label=(
                    "Frequency Labels"
                    if self.kind in ("spectrum", "spectrogram")
                    else "Labels"
                ),
                xalign=0,
                hexpand=True
            )
            labels_row.append(labels_label)

            labels_switch = Gtk.Switch()
            labels_switch.set_active(self.show_labels)
            labels_switch.set_valign(Gtk.Align.CENTER)

            labels_switch.connect(
                "notify::active",
                self.on_labels_changed
            )

            labels_row.append(labels_switch)
            box.append(labels_row)

        if self.kind in ("spectrum", "spectrogram"):

            mirror_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)

            mirror_label = Gtk.Label(
                label="Mirror Reflection", xalign=0, hexpand=True
            )
            mirror_row.append(mirror_label)

            mirror_switch = Gtk.Switch()
            mirror_switch.set_active(self.mirror_reflection)
            mirror_switch.set_valign(Gtk.Align.CENTER)

            mirror_switch.connect(
                "notify::active",
                self.on_mirror_changed
            )

            mirror_row.append(mirror_switch)
            box.append(mirror_row)

        if self.kind == "spectrogram":

            vertical_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)

            vertical_label = Gtk.Label(
                label="Vertical Orientation", xalign=0, hexpand=True
            )
            vertical_row.append(vertical_label)

            vertical_switch = Gtk.Switch()
            vertical_switch.set_active(self.spectrogram_vertical)
            vertical_switch.set_valign(Gtk.Align.CENTER)

            vertical_switch.connect(
                "notify::active",
                self.on_spectrogram_vertical_changed
            )

            vertical_row.append(vertical_switch)
            box.append(vertical_row)

            spectrogram_speed_row = Gtk.Box(
                orientation=Gtk.Orientation.HORIZONTAL, spacing=8
            )

            spectrogram_speed_label = Gtk.Label(
                label="Speed", xalign=0, hexpand=True
            )
            spectrogram_speed_row.append(spectrogram_speed_label)

            spectrogram_speed_scale = Gtk.Scale.new_with_range(
                Gtk.Orientation.HORIZONTAL, 0.25, 4.0, 0.05
            )
            spectrogram_speed_scale.set_value(self.spectrogram_speed)
            spectrogram_speed_scale.set_size_request(120, -1)
            spectrogram_speed_scale.set_draw_value(False)

            spectrogram_speed_scale.connect(
                "value-changed",
                self.on_spectrogram_speed_changed
            )

            spectrogram_speed_row.append(spectrogram_speed_scale)
            box.append(spectrogram_speed_row)

            palette_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)

            palette_label = Gtk.Label(label="Palette", xalign=0, hexpand=True)
            palette_row.append(palette_label)

            palette_dropdown = Gtk.DropDown.new_from_strings(
                [label for _, label in PALETTE_CHOICES]
            )

            current_palette_index = next(
                (
                    i for i, (key, _) in enumerate(PALETTE_CHOICES)
                    if key == self.spectrogram_palette
                ),
                0
            )
            palette_dropdown.set_selected(current_palette_index)

            palette_dropdown.connect(
                "notify::selected",
                self.on_spectrogram_palette_changed
            )

            palette_row.append(palette_dropdown)
            box.append(palette_row)

            lo_color_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)

            lo_color_label = Gtk.Label(label="Low Color", xalign=0, hexpand=True)
            lo_color_row.append(lo_color_label)

            lo_color_button = Gtk.ColorDialogButton(dialog=Gtk.ColorDialog())
            lo_color_button.set_rgba(self.spectrogram_color_lo)

            lo_color_button.connect(
                "notify::rgba",
                self.on_spectrogram_color_lo_changed
            )

            lo_color_row.append(lo_color_button)
            box.append(lo_color_row)

            hi_color_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)

            hi_color_label = Gtk.Label(label="High Color", xalign=0, hexpand=True)
            hi_color_row.append(hi_color_label)

            hi_color_button = Gtk.ColorDialogButton(dialog=Gtk.ColorDialog())
            hi_color_button.set_rgba(self.spectrogram_color_hi)

            hi_color_button.connect(
                "notify::rgba",
                self.on_spectrogram_color_hi_changed
            )

            hi_color_row.append(hi_color_button)
            box.append(hi_color_row)

            ceiling_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)

            ceiling_label = Gtk.Label(label="High Level", xalign=0, hexpand=True)
            ceiling_row.append(ceiling_label)

            ceiling_scale = Gtk.Scale.new_with_range(
                Gtk.Orientation.HORIZONTAL, 0.1, 1.0, 0.05
            )
            ceiling_scale.set_value(self.spectrogram_color_ceiling)
            ceiling_scale.set_size_request(120, -1)
            ceiling_scale.set_draw_value(False)

            ceiling_scale.connect(
                "value-changed",
                self.on_spectrogram_color_ceiling_changed
            )

            ceiling_row.append(ceiling_scale)
            box.append(ceiling_row)

        if self.kind == "terrain":

            terrain_palette_row = Gtk.Box(
                orientation=Gtk.Orientation.HORIZONTAL, spacing=8
            )

            terrain_palette_label = Gtk.Label(
                label="Palette", xalign=0, hexpand=True
            )
            terrain_palette_row.append(terrain_palette_label)

            terrain_palette_dropdown = Gtk.DropDown.new_from_strings(
                [label for _, label in PALETTE_CHOICES]
            )

            current_terrain_palette_index = next(
                (
                    i for i, (key, _) in enumerate(PALETTE_CHOICES)
                    if key == self.terrain_palette
                ),
                0
            )
            terrain_palette_dropdown.set_selected(current_terrain_palette_index)

            terrain_palette_dropdown.connect(
                "notify::selected",
                self.on_terrain_palette_changed
            )

            terrain_palette_row.append(terrain_palette_dropdown)
            box.append(terrain_palette_row)

            terrain_lo_color_row = Gtk.Box(
                orientation=Gtk.Orientation.HORIZONTAL, spacing=8
            )

            terrain_lo_color_label = Gtk.Label(
                label="Low Color", xalign=0, hexpand=True
            )
            terrain_lo_color_row.append(terrain_lo_color_label)

            terrain_lo_color_button = Gtk.ColorDialogButton(dialog=Gtk.ColorDialog())
            terrain_lo_color_button.set_rgba(self.terrain_color_lo)

            terrain_lo_color_button.connect(
                "notify::rgba",
                self.on_terrain_color_lo_changed
            )

            terrain_lo_color_row.append(terrain_lo_color_button)
            box.append(terrain_lo_color_row)

            terrain_hi_color_row = Gtk.Box(
                orientation=Gtk.Orientation.HORIZONTAL, spacing=8
            )

            terrain_hi_color_label = Gtk.Label(
                label="High Color", xalign=0, hexpand=True
            )
            terrain_hi_color_row.append(terrain_hi_color_label)

            terrain_hi_color_button = Gtk.ColorDialogButton(dialog=Gtk.ColorDialog())
            terrain_hi_color_button.set_rgba(self.terrain_color_hi)

            terrain_hi_color_button.connect(
                "notify::rgba",
                self.on_terrain_color_hi_changed
            )

            terrain_hi_color_row.append(terrain_hi_color_button)
            box.append(terrain_hi_color_row)

            terrain_ceiling_row = Gtk.Box(
                orientation=Gtk.Orientation.HORIZONTAL, spacing=8
            )

            terrain_ceiling_label = Gtk.Label(
                label="High Level", xalign=0, hexpand=True
            )
            terrain_ceiling_row.append(terrain_ceiling_label)

            terrain_ceiling_scale = Gtk.Scale.new_with_range(
                Gtk.Orientation.HORIZONTAL, 0.1, 1.0, 0.05
            )
            terrain_ceiling_scale.set_value(self.terrain_color_ceiling)
            terrain_ceiling_scale.set_size_request(120, -1)
            terrain_ceiling_scale.set_draw_value(False)

            terrain_ceiling_scale.connect(
                "value-changed",
                self.on_terrain_color_ceiling_changed
            )

            terrain_ceiling_row.append(terrain_ceiling_scale)
            box.append(terrain_ceiling_row)

        if self.kind == "dvd":

            icon_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)

            icon_label = Gtk.Label(label="Icon", xalign=0, hexpand=True)
            icon_row.append(icon_label)

            icon_dropdown = Gtk.DropDown.new_from_strings(
                [label for _, label in DVD_ICON_CHOICES]
            )

            current_index = next(
                (
                    i for i, (name, _) in enumerate(DVD_ICON_CHOICES)
                    if name == self.icon_name
                ),
                0
            )
            icon_dropdown.set_selected(current_index)

            icon_dropdown.connect(
                "notify::selected",
                self.on_icon_changed
            )

            icon_row.append(icon_dropdown)
            box.append(icon_row)

            bg_color_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)

            bg_color_label = Gtk.Label(
                label="Background Color", xalign=0, hexpand=True
            )
            bg_color_row.append(bg_color_label)

            bg_color_button = Gtk.ColorDialogButton(dialog=Gtk.ColorDialog())
            bg_color_button.set_rgba(self.dvd_bg_color)

            bg_color_button.connect(
                "notify::rgba",
                self.on_dvd_bg_color_changed
            )

            bg_color_row.append(bg_color_button)
            box.append(bg_color_row)

            speed_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)

            speed_label = Gtk.Label(label="Speed", xalign=0, hexpand=True)
            speed_row.append(speed_label)

            speed_scale = Gtk.Scale.new_with_range(
                Gtk.Orientation.HORIZONTAL, 0.25, 3.0, 0.05
            )
            speed_scale.set_value(self.dvd_speed_scale)
            speed_scale.set_size_request(120, -1)
            speed_scale.set_draw_value(False)

            speed_scale.connect(
                "value-changed",
                self.on_dvd_speed_changed
            )

            speed_row.append(speed_scale)
            box.append(speed_row)

            size_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)

            size_label = Gtk.Label(label="Size", xalign=0, hexpand=True)
            size_row.append(size_label)

            size_scale = Gtk.Scale.new_with_range(
                Gtk.Orientation.HORIZONTAL, 24, 160, 2
            )
            size_scale.set_value(self.dvd_base_size)
            size_scale.set_size_request(120, -1)
            size_scale.set_draw_value(False)

            size_scale.connect(
                "value-changed",
                self.on_dvd_base_size_changed
            )

            size_row.append(size_scale)
            box.append(size_row)

            reactivity_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)

            reactivity_label = Gtk.Label(
                label="Audio Reactivity", xalign=0, hexpand=True
            )
            reactivity_row.append(reactivity_label)

            reactivity_scale = Gtk.Scale.new_with_range(
                Gtk.Orientation.HORIZONTAL, 0.0, 2.5, 0.05
            )
            reactivity_scale.set_value(self.dvd_reactivity)
            reactivity_scale.set_size_request(120, -1)
            reactivity_scale.set_draw_value(False)

            reactivity_scale.connect(
                "value-changed",
                self.on_dvd_reactivity_changed
            )

            reactivity_row.append(reactivity_scale)
            box.append(reactivity_row)

            beats_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)

            beats_label = Gtk.Label(
                label="React to Beats", xalign=0, hexpand=True
            )
            beats_row.append(beats_label)

            beats_switch = Gtk.Switch()
            beats_switch.set_active(self.dvd_beats_enabled)
            beats_switch.set_valign(Gtk.Align.CENTER)

            beats_switch.connect(
                "notify::active",
                self.on_dvd_beats_enabled_changed
            )

            beats_row.append(beats_switch)
            box.append(beats_row)

            beat_sensitivity_row = Gtk.Box(
                orientation=Gtk.Orientation.HORIZONTAL, spacing=8
            )

            beat_sensitivity_label = Gtk.Label(
                label="Beat Sensitivity", xalign=0, hexpand=True
            )
            beat_sensitivity_row.append(beat_sensitivity_label)

            beat_sensitivity_scale = Gtk.Scale.new_with_range(
                Gtk.Orientation.HORIZONTAL, 1.05, 2.5, 0.05
            )
            beat_sensitivity_scale.set_value(self.dvd_beat_sensitivity)
            beat_sensitivity_scale.set_size_request(120, -1)
            beat_sensitivity_scale.set_draw_value(False)

            beat_sensitivity_scale.connect(
                "value-changed",
                self.on_dvd_beat_sensitivity_changed
            )

            beat_sensitivity_row.append(beat_sensitivity_scale)
            box.append(beat_sensitivity_row)

        if self.kind == "pipes":

            max_pipes_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)

            max_pipes_label = Gtk.Label(label="Max Pipes", xalign=0, hexpand=True)
            max_pipes_row.append(max_pipes_label)

            max_pipes_spin = Gtk.SpinButton.new_with_range(1, 8, 1)
            max_pipes_spin.set_value(self.pipes_max_pipes)

            max_pipes_spin.connect(
                "value-changed",
                self.on_pipes_max_pipes_changed
            )

            max_pipes_row.append(max_pipes_spin)
            box.append(max_pipes_row)

            pipes_width_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)

            pipes_width_label = Gtk.Label(label="Tube Width", xalign=0, hexpand=True)
            pipes_width_row.append(pipes_width_label)

            pipes_width_scale = Gtk.Scale.new_with_range(
                Gtk.Orientation.HORIZONTAL, 1.0, 16.0, 0.5
            )
            pipes_width_scale.set_value(self.pipes_base_width)
            pipes_width_scale.set_size_request(120, -1)
            pipes_width_scale.set_draw_value(False)

            pipes_width_scale.connect(
                "value-changed",
                self.on_pipes_base_width_changed
            )

            pipes_width_row.append(pipes_width_scale)
            box.append(pipes_width_row)

            for band_index, band_label in enumerate(PIPES_BAND_LABELS):

                band_color_row = Gtk.Box(
                    orientation=Gtk.Orientation.HORIZONTAL, spacing=8
                )

                band_color_label = Gtk.Label(
                    label=band_label, xalign=0, hexpand=True
                )
                band_color_row.append(band_color_label)

                band_color_button = Gtk.ColorDialogButton(dialog=Gtk.ColorDialog())
                band_color_button.set_rgba(self.pipes_band_colors[band_index])

                band_color_button.connect(
                    "notify::rgba",
                    lambda button, param, i=band_index: (
                        self.on_pipes_band_color_changed(button, i)
                    )
                )

                band_color_row.append(band_color_button)
                box.append(band_color_row)

            pipes_speed_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)

            pipes_speed_label = Gtk.Label(label="Speed", xalign=0, hexpand=True)
            pipes_speed_row.append(pipes_speed_label)

            pipes_speed_scale = Gtk.Scale.new_with_range(
                Gtk.Orientation.HORIZONTAL, 0.25, 3.0, 0.05
            )
            pipes_speed_scale.set_value(self.pipes_speed_scale)
            pipes_speed_scale.set_size_request(120, -1)
            pipes_speed_scale.set_draw_value(False)

            pipes_speed_scale.connect(
                "value-changed",
                self.on_pipes_speed_changed
            )

            pipes_speed_row.append(pipes_speed_scale)
            box.append(pipes_speed_row)

            pipes_reactivity_row = Gtk.Box(
                orientation=Gtk.Orientation.HORIZONTAL, spacing=8
            )

            pipes_reactivity_label = Gtk.Label(
                label="Audio Reactivity", xalign=0, hexpand=True
            )
            pipes_reactivity_row.append(pipes_reactivity_label)

            pipes_reactivity_scale = Gtk.Scale.new_with_range(
                Gtk.Orientation.HORIZONTAL, 0.0, 2.5, 0.05
            )
            pipes_reactivity_scale.set_value(self.pipes_reactivity)
            pipes_reactivity_scale.set_size_request(120, -1)
            pipes_reactivity_scale.set_draw_value(False)

            pipes_reactivity_scale.connect(
                "value-changed",
                self.on_pipes_reactivity_changed
            )

            pipes_reactivity_row.append(pipes_reactivity_scale)
            box.append(pipes_reactivity_row)

            pipes_reset_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)

            pipes_reset_button = Gtk.Button(label="Reset")
            pipes_reset_button.set_hexpand(True)

            pipes_reset_button.connect(
                "clicked",
                self.on_pipes_reset_clicked
            )

            pipes_reset_row.append(pipes_reset_button)
            box.append(pipes_reset_row)

            pipes_reset_view_row = Gtk.Box(
                orientation=Gtk.Orientation.HORIZONTAL, spacing=8
            )

            pipes_reset_view_button = Gtk.Button(label="Reset View")
            pipes_reset_view_button.set_hexpand(True)

            pipes_reset_view_button.connect(
                "clicked",
                self.on_pipes_reset_view_clicked
            )

            pipes_reset_view_row.append(pipes_reset_view_button)
            box.append(pipes_reset_view_row)

            pipes_fade_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)

            pipes_fade_label = Gtk.Label(
                label="Fade Old Segments", xalign=0, hexpand=True
            )
            pipes_fade_row.append(pipes_fade_label)

            pipes_fade_switch = Gtk.Switch()
            pipes_fade_switch.set_active(self.pipes_fade_enabled)
            pipes_fade_switch.set_valign(Gtk.Align.CENTER)

            pipes_fade_switch.connect(
                "notify::active",
                self.on_pipes_fade_enabled_changed
            )

            pipes_fade_row.append(pipes_fade_switch)
            box.append(pipes_fade_row)

            pipes_fade_time_row = Gtk.Box(
                orientation=Gtk.Orientation.HORIZONTAL, spacing=8
            )

            pipes_fade_time_label = Gtk.Label(
                label="Fade Time", xalign=0, hexpand=True
            )
            pipes_fade_time_row.append(pipes_fade_time_label)

            pipes_fade_time_scale = Gtk.Scale.new_with_range(
                Gtk.Orientation.HORIZONTAL, 0.2, 4.0, 0.1
            )
            pipes_fade_time_scale.set_value(self.pipes_fade_seconds)
            pipes_fade_time_scale.set_size_request(120, -1)
            pipes_fade_time_scale.set_draw_value(False)

            pipes_fade_time_scale.connect(
                "value-changed",
                self.on_pipes_fade_seconds_changed
            )

            pipes_fade_time_row.append(pipes_fade_time_scale)
            box.append(pipes_fade_time_row)

            pipes_auto_rotate_row = Gtk.Box(
                orientation=Gtk.Orientation.HORIZONTAL, spacing=8
            )

            pipes_auto_rotate_label = Gtk.Label(
                label="Auto-Rotate", xalign=0, hexpand=True
            )
            pipes_auto_rotate_row.append(pipes_auto_rotate_label)

            pipes_auto_rotate_switch = Gtk.Switch()
            pipes_auto_rotate_switch.set_active(self.pipes_auto_rotate)
            pipes_auto_rotate_switch.set_valign(Gtk.Align.CENTER)

            pipes_auto_rotate_switch.connect(
                "notify::active",
                self.on_pipes_auto_rotate_changed
            )

            pipes_auto_rotate_row.append(pipes_auto_rotate_switch)
            box.append(pipes_auto_rotate_row)

            pipes_rotate_speed_row = Gtk.Box(
                orientation=Gtk.Orientation.HORIZONTAL, spacing=8
            )

            pipes_rotate_speed_label = Gtk.Label(
                label="Rotation Speed", xalign=0, hexpand=True
            )
            pipes_rotate_speed_row.append(pipes_rotate_speed_label)

            pipes_rotate_speed_scale = Gtk.Scale.new_with_range(
                Gtk.Orientation.HORIZONTAL, 0.0, 30.0, 1.0
            )
            pipes_rotate_speed_scale.set_value(self.pipes_rotate_speed)
            pipes_rotate_speed_scale.set_size_request(120, -1)
            pipes_rotate_speed_scale.set_draw_value(False)

            pipes_rotate_speed_scale.connect(
                "value-changed",
                self.on_pipes_rotate_speed_changed
            )

            pipes_rotate_speed_row.append(pipes_rotate_speed_scale)
            box.append(pipes_rotate_speed_row)

            pipes_beats_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)

            pipes_beats_label = Gtk.Label(
                label="Beat Spawn", xalign=0, hexpand=True
            )
            pipes_beats_row.append(pipes_beats_label)

            pipes_beats_switch = Gtk.Switch()
            pipes_beats_switch.set_active(self.pipes_beats_enabled)
            pipes_beats_switch.set_valign(Gtk.Align.CENTER)

            pipes_beats_switch.connect(
                "notify::active",
                self.on_pipes_beats_enabled_changed
            )

            pipes_beats_row.append(pipes_beats_switch)
            box.append(pipes_beats_row)

            pipes_beat_rotation_row = Gtk.Box(
                orientation=Gtk.Orientation.HORIZONTAL, spacing=8
            )

            pipes_beat_rotation_label = Gtk.Label(
                label="Beat Rotation", xalign=0, hexpand=True
            )
            pipes_beat_rotation_row.append(pipes_beat_rotation_label)

            pipes_beat_rotation_switch = Gtk.Switch()
            pipes_beat_rotation_switch.set_active(self.pipes_beat_rotation_enabled)
            pipes_beat_rotation_switch.set_valign(Gtk.Align.CENTER)

            pipes_beat_rotation_switch.connect(
                "notify::active",
                self.on_pipes_beat_rotation_enabled_changed
            )

            pipes_beat_rotation_row.append(pipes_beat_rotation_switch)
            box.append(pipes_beat_rotation_row)

            pipes_beat_zoom_row = Gtk.Box(
                orientation=Gtk.Orientation.HORIZONTAL, spacing=8
            )

            pipes_beat_zoom_label = Gtk.Label(
                label="Beat Zoom", xalign=0, hexpand=True
            )
            pipes_beat_zoom_row.append(pipes_beat_zoom_label)

            pipes_beat_zoom_switch = Gtk.Switch()
            pipes_beat_zoom_switch.set_active(self.pipes_beat_zoom_enabled)
            pipes_beat_zoom_switch.set_valign(Gtk.Align.CENTER)

            pipes_beat_zoom_switch.connect(
                "notify::active",
                self.on_pipes_beat_zoom_enabled_changed
            )

            pipes_beat_zoom_row.append(pipes_beat_zoom_switch)
            box.append(pipes_beat_zoom_row)

            pipes_pulse_width_row = Gtk.Box(
                orientation=Gtk.Orientation.HORIZONTAL, spacing=8
            )

            pipes_pulse_width_label = Gtk.Label(
                label="Pulse Tube Width", xalign=0, hexpand=True
            )
            pipes_pulse_width_row.append(pipes_pulse_width_label)

            pipes_pulse_width_switch = Gtk.Switch()
            pipes_pulse_width_switch.set_active(self.pipes_pulse_width)
            pipes_pulse_width_switch.set_valign(Gtk.Align.CENTER)

            pipes_pulse_width_switch.connect(
                "notify::active",
                self.on_pipes_pulse_width_changed
            )

            pipes_pulse_width_row.append(pipes_pulse_width_switch)
            box.append(pipes_pulse_width_row)

            pipes_pulse_color_row = Gtk.Box(
                orientation=Gtk.Orientation.HORIZONTAL, spacing=8
            )

            pipes_pulse_color_label = Gtk.Label(
                label="Pulse Color", xalign=0, hexpand=True
            )
            pipes_pulse_color_row.append(pipes_pulse_color_label)

            pipes_pulse_color_switch = Gtk.Switch()
            pipes_pulse_color_switch.set_active(self.pipes_pulse_color)
            pipes_pulse_color_switch.set_valign(Gtk.Align.CENTER)

            pipes_pulse_color_switch.connect(
                "notify::active",
                self.on_pipes_pulse_color_changed
            )

            pipes_pulse_color_row.append(pipes_pulse_color_switch)
            box.append(pipes_pulse_color_row)

            pipes_shading_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)

            pipes_shading_label = Gtk.Label(
                label="Tube Shading", xalign=0, hexpand=True
            )
            pipes_shading_row.append(pipes_shading_label)

            pipes_shading_switch = Gtk.Switch()
            pipes_shading_switch.set_active(self.pipes_tube_shading)
            pipes_shading_switch.set_valign(Gtk.Align.CENTER)

            pipes_shading_switch.connect(
                "notify::active",
                self.on_pipes_tube_shading_changed
            )

            pipes_shading_row.append(pipes_shading_switch)
            box.append(pipes_shading_row)

            pipes_zoom_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)

            pipes_zoom_label = Gtk.Label(label="Zoom", xalign=0, hexpand=True)
            pipes_zoom_row.append(pipes_zoom_label)

            pipes_zoom_scale = Gtk.Scale.new_with_range(
                Gtk.Orientation.HORIZONTAL, 0.4, 3.0, 0.05
            )
            pipes_zoom_scale.set_value(self.pipes_zoom)
            pipes_zoom_scale.set_size_request(120, -1)
            pipes_zoom_scale.set_draw_value(False)

            pipes_zoom_scale.connect(
                "value-changed",
                self.on_pipes_zoom_changed
            )

            pipes_zoom_row.append(pipes_zoom_scale)
            box.append(pipes_zoom_row)

            pipes_grid_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)

            pipes_grid_label = Gtk.Label(label="Show Grid", xalign=0, hexpand=True)
            pipes_grid_row.append(pipes_grid_label)

            pipes_grid_switch = Gtk.Switch()
            pipes_grid_switch.set_active(self.pipes_show_grid)
            pipes_grid_switch.set_valign(Gtk.Align.CENTER)

            pipes_grid_switch.connect(
                "notify::active",
                self.on_pipes_show_grid_changed
            )

            pipes_grid_row.append(pipes_grid_switch)
            box.append(pipes_grid_row)

        if self.kind == "xy":

            xy_line_width_row = Gtk.Box(
                orientation=Gtk.Orientation.HORIZONTAL, spacing=8
            )

            xy_line_width_label = Gtk.Label(
                label="Line Width", xalign=0, hexpand=True
            )
            xy_line_width_row.append(xy_line_width_label)

            xy_line_width_scale = Gtk.Scale.new_with_range(
                Gtk.Orientation.HORIZONTAL, 0.5, 5.0, 0.25
            )
            xy_line_width_scale.set_value(self.xy_line_width)
            xy_line_width_scale.set_size_request(120, -1)
            xy_line_width_scale.set_draw_value(False)

            xy_line_width_scale.connect(
                "value-changed",
                self.on_xy_line_width_changed
            )

            xy_line_width_row.append(xy_line_width_scale)
            box.append(xy_line_width_row)

        popover = Gtk.Popover()
        popover.set_child(box)

        return popover

    def on_color_changed(self, button, param):

        self.color = button.get_rgba()
        self.terrain_dirty = True
        self.drawing_area.queue_draw()

    def on_color_mode_changed(self, dropdown, param):

        index = dropdown.get_selected()

        if 0 <= index < len(COLOR_MODE_CHOICES):
            self.color_mode = COLOR_MODE_CHOICES[index][0]
            self.drawing_area.queue_draw()

    def on_bars_changed(self, spin):

        self.num_bars = int(spin.get_value())
        self.terrain_dirty = True
        self.waterfall_dirty = True
        self.drawing_area.queue_draw()

    def on_decay_changed(self, scale):

        self.decay = scale.get_value()

    def on_peak_hold_seconds_changed(self, scale):

        self.peak_hold_seconds = scale.get_value()

    def on_peak_hold_color_custom_changed(self, switch, param):

        self.peak_hold_color_custom = switch.get_active()
        self.drawing_area.queue_draw()

    def on_peak_hold_color_changed(self, button, param):

        self.peak_hold_color = button.get_rgba()
        self.drawing_area.queue_draw()

    def on_spectrum_peak_hold_changed(self, switch, param):

        self.spectrum_peak_hold = switch.get_active()

    def on_spectrum_height_scale_changed(self, scale):

        self.spectrum_height_scale = scale.get_value()

    def on_spectrum_style_changed(self, dropdown, param):

        index = dropdown.get_selected()

        if 0 <= index < len(SPECTRUM_STYLE_CHOICES):
            self.spectrum_style = SPECTRUM_STYLE_CHOICES[index][0]
            self.drawing_area.queue_draw()

    def on_scope_time_base_changed(self, scale):

        self.scope_time_base = int(scale.get_value())

    def on_scope_trigger_changed(self, switch, param):

        self.scope_trigger = switch.get_active()

    def on_vector_persistence_changed(self, scale):

        self.vector_persistence = scale.get_value()

    def on_vector_dot_size_changed(self, scale):

        self.vector_dot_size = scale.get_value()

    def on_icon_changed(self, dropdown, param):

        index = dropdown.get_selected()

        if index < 0 or index >= len(DVD_ICON_CHOICES):
            return

        self.icon_name = DVD_ICON_CHOICES[index][0]
        self._icon_cache_key = None
        self.drawing_area.queue_draw()

    def on_dvd_bg_color_changed(self, button, param):

        self.dvd_bg_color = button.get_rgba()
        self.drawing_area.queue_draw()

    def on_dvd_speed_changed(self, scale):

        self.dvd_speed_scale = scale.get_value()

    def on_dvd_base_size_changed(self, scale):

        self.dvd_base_size = scale.get_value()

    def on_dvd_reactivity_changed(self, scale):

        self.dvd_reactivity = scale.get_value()

    def on_dvd_beats_enabled_changed(self, switch, param):

        self.dvd_beats_enabled = switch.get_active()

        if not self.dvd_beats_enabled:
            self.dvd_beat_pulse = 0.0
            self.dvd_energy_history.clear()

    def on_dvd_beat_sensitivity_changed(self, scale):

        self.dvd_beat_sensitivity = scale.get_value()

    def on_pipes_max_pipes_changed(self, spin):

        self.pipes_max_pipes = int(spin.get_value())

    def on_pipes_base_width_changed(self, scale):

        self.pipes_base_width = scale.get_value()

    def on_pipes_band_color_changed(self, button, band_index):

        self.pipes_band_colors[band_index] = button.get_rgba()

        # Only pipes spawned after this point pick up the new color -
        # already-laid segments/currently-active pipes keep whatever
        # color they were given at spawn, same as every other per-pipe
        # property.

    def on_pipes_speed_changed(self, scale):

        self.pipes_speed_scale = scale.get_value()

    def on_pipes_reactivity_changed(self, scale):

        self.pipes_reactivity = scale.get_value()

    def on_pipes_reset_clicked(self, button):

        self.reset_pipes()
        self.drawing_area.queue_draw()

    def on_pipes_reset_view_clicked(self, button):

        self.pipes_azimuth = math.radians(35)
        self.pipes_elevation = math.radians(28)
        self.pipes_zoom = 1.0
        self.drawing_area.queue_draw()

    def on_pipes_scroll(self, controller, dx, dy):

        self.pipes_zoom = max(0.4, min(3.0, self.pipes_zoom - dy * 0.1))
        self.drawing_area.queue_draw()

        return True

    def on_pipes_show_grid_changed(self, switch, param):

        self.pipes_show_grid = switch.get_active()
        self.drawing_area.queue_draw()

    def on_pipes_fade_enabled_changed(self, switch, param):

        self.pipes_fade_enabled = switch.get_active()

    def on_pipes_fade_seconds_changed(self, scale):

        self.pipes_fade_seconds = scale.get_value()

    def on_pipes_auto_rotate_changed(self, switch, param):

        self.pipes_auto_rotate = switch.get_active()

    def on_pipes_rotate_speed_changed(self, scale):

        self.pipes_rotate_speed = scale.get_value()

    def on_pipes_beats_enabled_changed(self, switch, param):

        # Doesn't touch pipes_energy_history - beat detection itself
        # now always runs regardless of this switch (pipes_tick), so
        # Beat Rotation/Beat Zoom's own detection wouldn't be affected
        # anyway, and clearing it here would only needlessly reset
        # their state too.
        self.pipes_beats_enabled = switch.get_active()

    def on_pipes_beat_rotation_enabled_changed(self, switch, param):

        self.pipes_beat_rotation_enabled = switch.get_active()

        if not self.pipes_beat_rotation_enabled:
            self.pipes_beat_spin_boost = 0.0
            self.pipes_beat_tilt_velocity = 0.0

    def on_pipes_beat_zoom_enabled_changed(self, switch, param):

        self.pipes_beat_zoom_enabled = switch.get_active()

        if not self.pipes_beat_zoom_enabled:
            self.pipes_beat_zoom_boost = 0.0

    def on_pipes_pulse_width_changed(self, switch, param):

        self.pipes_pulse_width = switch.get_active()

    def on_pipes_pulse_color_changed(self, switch, param):

        self.pipes_pulse_color = switch.get_active()

    def on_pipes_tube_shading_changed(self, switch, param):

        self.pipes_tube_shading = switch.get_active()

    def on_pipes_zoom_changed(self, scale):

        self.pipes_zoom = scale.get_value()
        self.drawing_area.queue_draw()

    def on_xy_line_width_changed(self, scale):

        self.xy_line_width = scale.get_value()
        self.drawing_area.queue_draw()

    def on_pipes_drag_begin(self, gesture, start_x, start_y):

        self.pipes_rotate_start = (self.pipes_azimuth, self.pipes_elevation)
        self.pipes_dragging = True

    def on_pipes_drag_update(self, gesture, offset_x, offset_y):

        start_azimuth, start_elevation = self.pipes_rotate_start

        # Negated - same reasoning as Terrain's identical handler
        # (on_terrain_drag_update): reported as feeling reversed.
        self.pipes_azimuth = start_azimuth - math.radians(offset_x * 0.3)

        self.pipes_elevation = max(
            math.radians(-10), min(
                math.radians(85),
                start_elevation - math.radians(offset_y * 0.3)
            )
        )

        self.drawing_area.queue_draw()

    def on_pipes_drag_end(self, gesture, offset_x, offset_y):

        # Auto-rotate (pipes_tick) resumes on the very next tick,
        # continuing from wherever this drag left pipes_azimuth - nothing
        # else to reset here.
        self.pipes_dragging = False

    def on_vu_style_changed(self, dropdown, param):

        index = dropdown.get_selected()

        if index < 0 or index >= len(VU_STYLE_CHOICES):
            return

        self.vu_style = VU_STYLE_CHOICES[index][0]
        self.drawing_area.queue_draw()

    def on_vu_peak_hold_changed(self, switch, param):

        self.vu_peak_hold = switch.get_active()

    def on_vu_segments_changed(self, spin):

        self.vu_segments = int(spin.get_value())
        self.drawing_area.queue_draw()

    def on_labels_changed(self, switch, param):

        self.show_labels = switch.get_active()
        self.drawing_area.queue_draw()

    def on_mirror_changed(self, switch, param):

        self.mirror_reflection = switch.get_active()
        self.drawing_area.queue_draw()

    def on_spectrogram_vertical_changed(self, switch, param):

        self.spectrogram_vertical = switch.get_active()
        self.drawing_area.queue_draw()

    def on_spectrogram_speed_changed(self, scale):

        self.spectrogram_speed = scale.get_value()

    def on_spectrogram_color_lo_changed(self, button, param):

        self.spectrogram_color_lo = button.get_rgba()
        self.drawing_area.queue_draw()

    def on_spectrogram_color_hi_changed(self, button, param):

        self.spectrogram_color_hi = button.get_rgba()
        self.drawing_area.queue_draw()

    def on_spectrogram_color_ceiling_changed(self, scale):

        self.spectrogram_color_ceiling = scale.get_value()
        self.drawing_area.queue_draw()

    def on_terrain_color_lo_changed(self, button, param):

        self.terrain_color_lo = button.get_rgba()
        self.terrain_dirty = True
        self.drawing_area.queue_draw()

    def on_terrain_color_hi_changed(self, button, param):

        self.terrain_color_hi = button.get_rgba()
        self.terrain_dirty = True
        self.drawing_area.queue_draw()

    def on_terrain_color_ceiling_changed(self, scale):

        self.terrain_color_ceiling = scale.get_value()
        self.terrain_dirty = True
        self.drawing_area.queue_draw()

    def on_terrain_zoom_changed(self, scale):

        self.terrain_zoom = scale.get_value()
        self.terrain_dirty = True
        self.drawing_area.queue_draw()

    def on_terrain_show_axes_changed(self, switch, param):

        # Axes are drawn fresh each frame, not baked into
        # terrain_surface (see draw_terrain) - no need to mark dirty,
        # just redraw.
        self.terrain_show_axes = switch.get_active()
        self.drawing_area.queue_draw()

    def on_waterfall_color_lo_changed(self, button, param):

        self.waterfall_color_lo = button.get_rgba()
        self.waterfall_dirty = True
        self.drawing_area.queue_draw()

    def on_waterfall_color_hi_changed(self, button, param):

        self.waterfall_color_hi = button.get_rgba()
        self.waterfall_dirty = True
        self.drawing_area.queue_draw()

    def on_waterfall_color_ceiling_changed(self, scale):

        self.waterfall_color_ceiling = scale.get_value()
        self.waterfall_dirty = True
        self.drawing_area.queue_draw()

    def on_spectrogram_palette_changed(self, dropdown, param):

        index = dropdown.get_selected()

        if 0 <= index < len(PALETTE_CHOICES):
            self.spectrogram_palette = PALETTE_CHOICES[index][0]
            self.drawing_area.queue_draw()

    def on_terrain_palette_changed(self, dropdown, param):

        index = dropdown.get_selected()

        if 0 <= index < len(PALETTE_CHOICES):
            self.terrain_palette = PALETTE_CHOICES[index][0]
            self.terrain_dirty = True
            self.drawing_area.queue_draw()

    def on_waterfall_palette_changed(self, dropdown, param):

        index = dropdown.get_selected()

        if 0 <= index < len(PALETTE_CHOICES):
            self.waterfall_palette = PALETTE_CHOICES[index][0]
            self.waterfall_dirty = True
            self.drawing_area.queue_draw()

    def on_waterfall_textured_changed(self, switch, param):

        self.waterfall_textured = switch.get_active()
        self.waterfall_dirty = True
        self.drawing_area.queue_draw()

    def on_waterfall_height_changed(self, scale):

        self.waterfall_height_scale = scale.get_value()
        self.waterfall_dirty = True
        self.drawing_area.queue_draw()

    def on_waterfall_zoom_changed(self, scale):

        self.waterfall_zoom = scale.get_value()
        self.waterfall_dirty = True
        self.drawing_area.queue_draw()

    def on_waterfall_show_axes_changed(self, switch, param):

        self.waterfall_show_axes = switch.get_active()
        self.drawing_area.queue_draw()

    def on_drag_begin(self, gesture, start_x, start_y):

        # Dragging a maximized surface is meaningless (nowhere to move
        # it while filling the monitor) - same guard MirrorWindow uses
        # (mirror_window.py, on_picture_drag_begin) after a real report
        # of a maximized window's content breaking when this was
        # attempted anyway.
        if self.is_maximized():
            return

        widget = gesture.get_widget()

        ok, bounds = widget.compute_bounds(self)

        if not ok:
            return

        self.get_surface().begin_move(
            gesture.get_current_event_device(),
            gesture.get_current_button(),
            bounds.get_x() + start_x,
            bounds.get_y() + start_y,
            gesture.get_current_event_time()
        )

    def on_window_pressed(self, gesture, n_press, x, y):

        if self.settings_popover.get_visible():
            self.settings_popover.popdown()

    def on_terrain_drag_begin(self, gesture, start_x, start_y):

        self.terrain_rotate_start = (self.terrain_azimuth, self.terrain_elevation)

    def on_terrain_drag_update(self, gesture, offset_x, offset_y):

        # offset_x/offset_y are cumulative from drag-begin (GTK's own
        # GestureDrag semantics), not per-event deltas - recomputing
        # from terrain_rotate_start every update (rather than
        # incrementally accumulating a running total here) means this
        # can't drift from rounding error over a long drag.
        start_azimuth, start_elevation = self.terrain_rotate_start

        # Negated - reported as feeling reversed (dragging right
        # visibly rotated the view the "wrong" way, opposite the
        # usual "grab the surface and drag it the way you want it to
        # turn" expectation). Same fix as Pipes' identical handler.
        self.terrain_azimuth = start_azimuth - math.radians(offset_x * 0.3)

        self.terrain_elevation = max(
            math.radians(-10), min(
                math.radians(85),
                start_elevation - math.radians(offset_y * 0.3)
            )
        )

        self.terrain_dirty = True
        self.drawing_area.queue_draw()

    def on_terrain_reset_view_clicked(self, button):

        self.terrain_azimuth = math.radians(35)
        self.terrain_elevation = math.radians(28)
        self.terrain_zoom = 1.0
        self.terrain_dirty = True
        self.drawing_area.queue_draw()

    def on_terrain_scroll(self, controller, dx, dy):

        # dy > 0 is scroll-down (zoom out), dy < 0 is scroll-up (zoom
        # in) - the usual convention. Clamped so it can't zoom
        # through the camera (too small) or off into empty space (too
        # large).
        self.terrain_zoom = max(0.4, min(3.0, self.terrain_zoom - dy * 0.1))
        self.terrain_dirty = True
        self.drawing_area.queue_draw()

        return True

    def on_waterfall_drag_begin(self, gesture, start_x, start_y):

        self.waterfall_rotate_start = (
            self.waterfall_azimuth, self.waterfall_elevation
        )

    def on_waterfall_drag_update(self, gesture, offset_x, offset_y):

        start_azimuth, start_elevation = self.waterfall_rotate_start

        self.waterfall_azimuth = start_azimuth - math.radians(offset_x * 0.3)

        self.waterfall_elevation = max(
            math.radians(10), min(
                math.radians(85),
                start_elevation - math.radians(offset_y * 0.3)
            )
        )

        self.waterfall_dirty = True
        self.drawing_area.queue_draw()

    def on_waterfall_reset_view_clicked(self, button):

        self.waterfall_azimuth = math.radians(35)
        self.waterfall_elevation = math.radians(55)
        self.waterfall_zoom = 1.0
        self.waterfall_dirty = True
        self.drawing_area.queue_draw()

    def on_waterfall_scroll(self, controller, dx, dy):

        self.waterfall_zoom = max(0.4, min(3.0, self.waterfall_zoom - dy * 0.1))
        self.waterfall_dirty = True
        self.drawing_area.queue_draw()

        return True

    def canvas_background_rgb(self):

        # Queried fresh at every draw rather than cached/tracked via a
        # notify signal - Adw.StyleManager.get_default().dark already
        # reflects the app's own Light/Dark/Follow System selection
        # (the same property window.py's own header-tinting reads),
        # so a plain per-frame read is enough to stay correct without
        # any extra plumbing; it's a cheap property read, not a real
        # cost at redraw time. Every aux window kind's canvas
        # previously painted one of a few near-identical hardcoded
        # dark grays regardless of theme - now light-reactive, sharing
        # this one shade rather than each kind keeping its own
        # slightly different dark tone (a difference small enough not
        # to be worth preserving against the cost of hand-picking a
        # light counterpart for each one). DVD Bounce is the one
        # exception - its background is already a user-set color
        # (dvd_bg_color), so automatically overriding it with the
        # theme would fight an explicit choice rather than respect it.
        if Adw.StyleManager.get_default().get_dark():
            return (0.05, 0.05, 0.06)

        return (0.90, 0.90, 0.91)

    def canvas_foreground_rgba(self, alpha):

        # The counterpart to canvas_background_rgb() for structural
        # reference marks drawn *over* the canvas - axis lines
        # (draw_3d_axes), the X-Y/Vector Scope crosshairs, the
        # Oscilloscope's center line, Pipes' grid wireframe, the VU
        # needle's dial scale. These used to be a plain translucent
        # white regardless of theme - fine against the old always-dark
        # canvas, but reported as unreadable once the canvas itself
        # started following Light/Dark/Follow System (near-white lines
        # on a near-white background). Near-black in light mode, near-
        # white in dark mode - same alpha either way, just the
        # opposite end of the brightness scale.
        if Adw.StyleManager.get_default().get_dark():
            return (1, 1, 1, alpha)

        return (0, 0, 0, alpha)

    # The peak-hold marker line's own color - VU Meter/Peak Meter/
    # Spectrum all share this (peak_hold_color_custom/peak_hold_color,
    # see __init__) rather than each having its own, matching how they
    # already share peak_hold_seconds/peak_hold_fall_rate. Falls back
    # to the same theme-reactive canvas_foreground_rgba every other
    # structural mark uses until the user explicitly opts into a fixed
    # color instead.
    def peak_hold_draw_color(self, fallback_alpha=1.0):

        if self.peak_hold_color_custom:

            return (
                self.peak_hold_color.red,
                self.peak_hold_color.green,
                self.peak_hold_color.blue,
                self.peak_hold_color.alpha
            )

        return self.canvas_foreground_rgba(fallback_alpha)

    def set_overlay_controls_visible(self, visible):

        buttons = (self.settings_button,)

        for button in buttons:

            if visible:
                button.add_css_class("nav-arrow-visible")
            else:
                button.remove_css_class("nav-arrow-visible")

    def toolbar_enter(self, controller, x, y):

        self.mouse_over_toolbar = True

        if self.hide_timer:
            GLib.source_remove(self.hide_timer)
            self.hide_timer = None

        self.toolbar_view.set_reveal_top_bars(True)
        self.set_overlay_controls_visible(True)

    def toolbar_leave(self, controller):

        self.mouse_over_toolbar = False

        self.hide_timer = GLib.timeout_add_seconds(3, self.hide_toolbar)

    def mouse_move(self, controller, x, y):

        self.toolbar_view.set_reveal_top_bars(True)
        self.set_overlay_controls_visible(True)

        if self.hide_timer:
            GLib.source_remove(self.hide_timer)

        self.hide_timer = GLib.timeout_add_seconds(3, self.hide_toolbar)

    def hide_toolbar(self):

        self.hide_timer = None

        if self.mouse_over_toolbar:
            return False

        self.toolbar_view.set_reveal_top_bars(False)
        self.set_overlay_controls_visible(False)

        return False

    def push_audio(self, left, right):

        self.left = left
        self.right = right

        if self.kind in ("spectrum", "spectrogram", "terrain", "waterfall", "pipes"):

            mono = [(l + r) / 2.0 for l, r in zip(left, right)]

            self.spectrum_buffer.extend(mono)
            self.spectrum_buffer = self.spectrum_buffer[-FFT_SIZE:]

        if self.kind == "oscilloscope":
            self.scope_left.extend(left)
            self.scope_right.extend(right)

        self.drawing_area.queue_draw()

    def on_draw(self, area, cr, width, height):

        if self.kind == "vu":
            self.draw_vu_meter(cr, width, height)
        elif self.kind == "peak":
            self.draw_peak_meter(cr, width, height)
        elif self.kind == "spectrum":
            self.update_spectrum_levels()
            spectrum_render_func = (
                self.render_spectrum_smooth
                if self.spectrum_style == "smooth"
                else self.render_spectrum_bars
            )
            self.render_with_optional_mirror(cr, width, height, spectrum_render_func)
        elif self.kind == "spectrogram":
            self.update_spectrogram_columns()
            self.render_with_optional_mirror(cr, width, height, self.render_spectrogram)
        elif self.kind == "dvd":
            self.draw_dvd_bounce(cr, width, height)
        elif self.kind == "oscilloscope":
            self.draw_oscilloscope(cr, width, height)
        elif self.kind == "vectorscope":
            self.draw_vector_scope(cr, width, height)
        elif self.kind == "terrain":
            self.draw_terrain(cr, width, height)
        elif self.kind == "waterfall":
            self.draw_waterfall(cr, width, height)
        elif self.kind == "pipes":
            self.draw_pipes(cr, width, height)
        else:
            self.draw_xy_scope(cr, width, height)

    def render_with_optional_mirror(self, cr, width, height, render_func):

        cr.set_source_rgb(*self.canvas_background_rgb())
        cr.paint()

        if not self.mirror_reflection:
            render_func(cr, width, height)

            if self.show_labels:
                self.draw_frequency_labels(cr, width, height)

            return

        # A faded, vertically-flipped copy of the same content drawn
        # again into the bottom portion of the window - the classic
        # media-player-visualizer "reflection" look. push_group/
        # pop_group_to_source renders render_func once into an offscreen
        # pattern so the flip+fade can be applied to it as a whole,
        # rather than needing render_func itself to know about mirroring.
        main_height = height * 0.7
        reflection_height = height - main_height

        cr.save()
        cr.rectangle(0, 0, width, main_height)
        cr.clip()
        render_func(cr, width, main_height)
        cr.restore()

        cr.push_group()
        cr.save()
        # Flips render_func's own [0, main_height] coordinate space so
        # main_height (its bottom edge) lands exactly on the seam
        # between the real content and the reflection, with 0 (its top
        # edge) landing furthest away - same orientation a real
        # reflection in water would have.
        cr.translate(0, main_height * 2)
        cr.scale(1, -1)
        render_func(cr, width, main_height)
        cr.restore()
        pattern = cr.pop_group()

        cr.save()
        cr.rectangle(0, main_height, width, reflection_height)
        cr.clip()
        cr.set_source(pattern)

        gradient = cairo.LinearGradient(0, main_height, 0, height)
        gradient.add_color_stop_rgba(0, 1, 1, 1, 0.4)
        gradient.add_color_stop_rgba(1, 1, 1, 1, 0.0)
        cr.mask(gradient)
        cr.restore()

        if self.show_labels:
            self.draw_frequency_labels(cr, width, main_height)

    def draw_vu_meter(self, cr, width, height):

        cr.set_source_rgb(*self.canvas_background_rgb())
        cr.paint()

        # RMS (average power over the chunk), not the instantaneous
        # sample peak - a real VU meter's ballistics respond to
        # average loudness, not momentary peaks. Loudly-mastered audio
        # routinely has individual samples sitting near full scale, so
        # a peak-driven meter would read yellow/red almost constantly
        # regardless of how the track actually sounds.
        # RMS reads well below 1.0 even for a full-scale, "clipping"
        # signal (a 0dBFS sine's RMS is ~0.71, and real music has a
        # bigger peak-to-average gap still) - VU_GAIN compensates so
        # normal program material still uses the meter's full visible
        # range instead of sitting permanently in the green.
        rms_left = rms(self.left) * VU_GAIN
        rms_right = rms(self.right) * VU_GAIN

        self.vu_left = max(rms_left, self.vu_left * self.decay)
        self.vu_right = max(rms_right, self.vu_right * self.decay)

        if self.vu_peak_hold:

            now = time.monotonic()

            self.vu_hold_left, self.vu_hold_left_time = self.update_peak_hold(
                self.vu_left, self.vu_hold_left, self.vu_hold_left_time, now
            )
            self.vu_hold_right, self.vu_hold_right_time = self.update_peak_hold(
                self.vu_right, self.vu_hold_right, self.vu_hold_right_time, now
            )

        margin = 12
        label_space = 16 if self.show_labels else 0
        cell_width = (width - margin * 3) / 2
        cell_height = height - margin * 2 - label_space

        left_x = margin
        right_x = margin * 2 + cell_width

        hold_left = self.vu_hold_left if self.vu_peak_hold else None
        hold_right = self.vu_hold_right if self.vu_peak_hold else None

        if self.vu_style == "led":
            self.draw_vu_led(
                cr, left_x, margin, cell_width, cell_height, self.vu_left, hold_left
            )
            self.draw_vu_led(
                cr, right_x, margin, cell_width, cell_height, self.vu_right, hold_right
            )
        elif self.vu_style == "needle":
            self.draw_vu_needle(cr, left_x, margin, cell_width, cell_height, self.vu_left)
            self.draw_vu_needle(cr, right_x, margin, cell_width, cell_height, self.vu_right)
        else:
            self.draw_vu_bar(
                cr, left_x, margin, cell_width, cell_height, self.vu_left, hold_left
            )
            self.draw_vu_bar(
                cr, right_x, margin, cell_width, cell_height, self.vu_right, hold_right
            )

        if self.show_labels:
            label_y = margin + cell_height + label_space - 3
            self.draw_text_label(cr, left_x + cell_width / 2 - 3, label_y, "L")
            self.draw_text_label(cr, right_x + cell_width / 2 - 3, label_y, "R")

    def draw_vu_bar(self, cr, x, y, bar_width, bar_height, level, hold_level=None):

        cr.set_source_rgba(1, 1, 1, 0.15)
        cr.rectangle(x, y, bar_width, bar_height)
        cr.fill()

        filled_height = bar_height * min(level, 1.0)

        # Green/yellow/red zones, same convention as a hardware VU
        # meter's colored bands - kept fixed (not the user color
        # setting) since the zones themselves are the information.
        if level < 0.7:
            cr.set_source_rgb(0.2, 0.8, 0.3)
        elif level < 0.9:
            cr.set_source_rgb(0.9, 0.8, 0.2)
        else:
            cr.set_source_rgb(0.9, 0.2, 0.2)

        cr.rectangle(x, y + (bar_height - filled_height), bar_width, filled_height)
        cr.fill()

        if hold_level is not None:

            hold_y = y + bar_height * (1.0 - min(hold_level, 1.0))

            cr.set_source_rgba(*self.peak_hold_draw_color())
            cr.rectangle(x, hold_y - 2, bar_width, 2)
            cr.fill()

    def draw_vu_led(self, cr, x, y, w, h, level, hold_level=None):

        # A discrete, hardware-style meter - a fixed stack of
        # individually lit/unlit segments instead of one continuous
        # bar, same green/yellow/red zone convention as draw_vu_bar
        # (by segment position, not by the live level - a segment in
        # the red zone is drawn red whether lit or not, just dim when
        # unlit).
        segments = max(2, self.vu_segments)
        gap = min(3, h / segments / 4)
        seg_height = (h - gap * (segments - 1)) / segments
        level = min(level, 1.0)

        # The lagging hold segment - whichever segment the hold value
        # currently falls in gets an outline instead of the normal
        # lit/unlit fill, distinguishing "this is where the peak was"
        # from "this is currently lit."
        hold_segment_index = None

        if hold_level is not None:
            hold_segment_index = min(
                segments - 1, int(min(hold_level, 1.0) * segments)
            )

        for i in range(segments):

            threshold = (i + 1) / segments
            seg_y = y + h - (i + 1) * seg_height - i * gap

            if threshold > 0.9:
                color = (0.9, 0.2, 0.2)
            elif threshold > 0.7:
                color = (0.9, 0.8, 0.2)
            else:
                color = (0.2, 0.8, 0.3)

            lit = level >= threshold - 1.0 / segments

            if lit:
                cr.set_source_rgb(*color)
            else:
                cr.set_source_rgba(color[0], color[1], color[2], 0.12)

            cr.rectangle(x, seg_y, w, seg_height)
            cr.fill()

            if hold_segment_index is not None and i == hold_segment_index:
                cr.set_source_rgba(*self.peak_hold_draw_color(0.9))
                cr.set_line_width(1.5)
                cr.rectangle(x + 0.75, seg_y + 0.75, w - 1.5, seg_height - 1.5)
                cr.stroke()

    def draw_vu_needle(self, cr, x, y, w, h, level):

        # A classic analog dial gauge - needle pivots near the bottom
        # of the cell, sweeping through the top across a fixed angular
        # range as level goes 0..1, same "up is louder" convention
        # bars/LEDs use, just expressed as rotation instead of height.
        # Angles are built from "degrees tilted off straight-up" (0 =
        # pointing at 12 o'clock) rather than raw Cairo angles, purely
        # because that's a far more legible way to reason about a
        # sweep-left-to-sweep-right gauge than Cairo's own
        # positive-x-axis convention.
        cx = x + w / 2
        radius = max(4.0, min(w / 2, h) * 0.92)

        # Vertically centers the whole gauge shape (the arc above the
        # pivot, down to the pivot dot) within the cell, rather than
        # anchoring the pivot a fixed 4px above the bottom edge -
        # anchoring it left a large empty gap above the dial whenever
        # the cell's *width* (not height) was the limiting dimension
        # for radius, which is the common case at this window's
        # default aspect ratio. Reported as "the entire VU needle dial
        # is not centered" - confirmed by rendering a real cell and
        # measuring a 116px gap above the dial vs 1px below it.
        # pivot_margin mirrors the small bottom-edge margin the old
        # fixed "-4" offset used to provide.
        pivot_margin = 4.0
        cy = y + h / 2 + (radius - pivot_margin) / 2
        sweep = math.radians(55)

        level = min(level, 1.0)

        def point_at(angle_from_vertical, r):
            cairo_angle = -math.pi / 2 + angle_from_vertical
            return cx + r * math.cos(cairo_angle), cy + r * math.sin(cairo_angle)

        cr.set_line_width(2)
        cr.set_source_rgba(*self.canvas_foreground_rgba(0.25))
        cr.arc(cx, cy, radius, -math.pi / 2 - sweep, -math.pi / 2 + sweep)
        cr.stroke()

        # Red zone - the last ~15% of the sweep, same idea as the
        # bars'/LEDs' top red band.
        cr.set_source_rgba(0.9, 0.2, 0.2, 0.7)
        cr.arc(cx, cy, radius, -math.pi / 2 + sweep * 0.7, -math.pi / 2 + sweep)
        cr.stroke()

        if self.show_labels:

            for frac in (0.0, 0.5, 1.0):

                angle = -sweep + frac * 2 * sweep
                x1, y1 = point_at(angle, radius * 0.8)
                x2, y2 = point_at(angle, radius)

                cr.set_source_rgba(*self.canvas_foreground_rgba(0.5))
                cr.set_line_width(1.5)
                cr.move_to(x1, y1)
                cr.line_to(x2, y2)
                cr.stroke()

        needle_angle = -sweep + level * 2 * sweep
        tip_x, tip_y = point_at(needle_angle, radius * 0.92)

        if self.color_mode == "rainbow":
            cr.set_source_rgb(*rainbow_color(level))
        else:
            cr.set_source_rgb(self.color.red, self.color.green, self.color.blue)

        cr.set_line_width(2.5)
        cr.move_to(cx, cy)
        cr.line_to(tip_x, tip_y)
        cr.stroke()

        cr.arc(cx, cy, 3.5, 0, 2 * math.pi)
        cr.fill()

    def db_frac(self, level):

        # Linear 0..1 amplitude -> a 0..1 position on a -60..0 dBFS
        # scale. Peak meters are traditionally read in dB, not linear
        # amplitude - most of a track's real dynamic range lives in
        # the top ~20dB, which a linear scale would crush into a
        # sliver at the very top of the bar.
        level = max(level, 1e-5)
        db = max(-60.0, min(0.0, 20 * math.log10(level)))

        return (db + 60.0) / 60.0

    def update_peak_hold(self, current_level, hold_value, hold_time, now):

        # Jumps to a new peak the instant one arrives; otherwise holds
        # its current reading for peak_hold_seconds before falling
        # back down at peak_hold_fall_rate (never below the live
        # level) - the classic hardware "peak hold" marker, letting a
        # brief transient stay readable instead of vanishing on the
        # very next frame the way the bar's own fast-decay fill does.
        if current_level >= hold_value:
            return current_level, now

        elapsed = now - hold_time

        if elapsed < self.peak_hold_seconds:
            return hold_value, hold_time

        fallen = (elapsed - self.peak_hold_seconds) * self.peak_hold_fall_rate

        return max(current_level, hold_value - fallen), hold_time

    def draw_peak_bar(self, cr, x, y, w, h, level_frac, hold_frac):

        cr.set_source_rgba(1, 1, 1, 0.15)
        cr.rectangle(x, y, w, h)
        cr.fill()

        filled_height = h * min(level_frac, 1.0)

        # Same green/yellow/red convention as the VU Meter/LED
        # segments, but at dB-scaled thresholds appropriate to a peak
        # reading (red only very close to 0dBFS/clipping) rather than
        # the VU Meter's linear ones - a peak meter's red zone means
        # something different (headroom, not perceived loudness).
        if level_frac < 0.8:
            cr.set_source_rgb(0.2, 0.8, 0.3)
        elif level_frac < 0.95:
            cr.set_source_rgb(0.9, 0.8, 0.2)
        else:
            cr.set_source_rgb(0.9, 0.2, 0.2)

        cr.rectangle(x, y + (h - filled_height), w, filled_height)
        cr.fill()

        hold_y = y + h * (1.0 - min(hold_frac, 1.0))

        cr.set_source_rgba(*self.peak_hold_draw_color())
        cr.rectangle(x, hold_y - 1, w, 2)
        cr.fill()

    def draw_peak_scale(self, cr, x, y, h):

        for db, label in (
            (0, "0"), (-6, "-6"), (-12, "-12"),
            (-24, "-24"), (-40, "-40"), (-60, "-60")
        ):
            frac = (db + 60) / 60
            tick_y = y + h * (1.0 - frac)
            self.draw_text_label(cr, x, tick_y + 3, label)

    def draw_peak_meter(self, cr, width, height):

        cr.set_source_rgb(*self.canvas_background_rgb())
        cr.paint()

        # True instantaneous sample peak - no VU_GAIN, unlike the VU
        # Meter. A peak meter exists specifically to show real
        # headroom against 0dBFS, so artificially inflating it the way
        # VU_GAIN inflates RMS for a more usable *linear* VU range
        # would defeat the point.
        peak_l = max((abs(s) for s in self.left), default=0.0)
        peak_r = max((abs(s) for s in self.right), default=0.0)

        self.peak_left = max(peak_l, self.peak_left * self.decay)
        self.peak_right = max(peak_r, self.peak_right * self.decay)

        now = time.monotonic()

        self.peak_hold_left, self.peak_hold_left_time = self.update_peak_hold(
            self.peak_left, self.peak_hold_left, self.peak_hold_left_time, now
        )
        self.peak_hold_right, self.peak_hold_right_time = self.update_peak_hold(
            self.peak_right, self.peak_hold_right, self.peak_hold_right_time, now
        )

        margin = 12
        scale_width = 26 if self.show_labels else 0
        label_space = 16 if self.show_labels else 0

        bars_x = margin + scale_width
        cell_width = (width - bars_x - margin - margin) / 2
        cell_height = height - margin * 2 - label_space

        left_x = bars_x
        right_x = bars_x + cell_width + margin

        self.draw_peak_bar(
            cr, left_x, margin, cell_width, cell_height,
            self.db_frac(self.peak_left), self.db_frac(self.peak_hold_left)
        )
        self.draw_peak_bar(
            cr, right_x, margin, cell_width, cell_height,
            self.db_frac(self.peak_right), self.db_frac(self.peak_hold_right)
        )

        if self.show_labels:

            self.draw_peak_scale(cr, margin, margin, cell_height)

            label_y = margin + cell_height + label_space - 3
            self.draw_text_label(cr, left_x + cell_width / 2 - 3, label_y, "L")
            self.draw_text_label(cr, right_x + cell_width / 2 - 3, label_y, "R")

    def draw_xy_scope(self, cr, width, height):

        cr.set_source_rgb(*self.canvas_background_rgb())
        cr.paint()

        cx = width / 2
        cy = height / 2
        scale = min(width, height) / 2 - 8

        if self.show_labels:

            cr.set_source_rgba(*self.canvas_foreground_rgba(0.15))
            cr.set_line_width(1.0)
            cr.move_to(cx, 0)
            cr.line_to(cx, height)
            cr.stroke()
            cr.move_to(0, cy)
            cr.line_to(width, cy)
            cr.stroke()

            # L is the horizontal axis, R the vertical - same
            # convention self.left[i]/self.right[i] are actually
            # plotted with below (x from left, y from right).
            self.draw_text_label(cr, width - 16, cy + 4, "L")
            self.draw_text_label(cr, cx - 4, 14, "R")

        count = min(len(self.left), len(self.right))

        if count == 0:
            return

        cr.set_line_width(self.xy_line_width)

        if self.color_mode == "rainbow":

            # A continuous single-color path can't have a color that
            # itself changes along its length - drawn as one short
            # segment per sample pair instead, each colored by its own
            # position in the trace, for a "rainbow comet trail"
            # look. Costs one stroke call per sample rather than one
            # for the whole path; count is bounded by the audio chunk
            # size (not a large rolling buffer here, unlike the
            # Oscilloscope), so this stays cheap.
            for i in range(1, count):

                cr.set_source_rgba(*rainbow_color(i / max(1, count - 1)), 0.85)
                cr.move_to(
                    cx + self.left[i - 1] * scale, cy - self.right[i - 1] * scale
                )
                cr.line_to(cx + self.left[i] * scale, cy - self.right[i] * scale)
                cr.stroke()

        else:

            cr.set_source_rgba(
                self.color.red, self.color.green, self.color.blue, 0.85
            )

            cr.move_to(cx + self.left[0] * scale, cy - self.right[0] * scale)

            for i in range(1, count):
                cr.line_to(cx + self.left[i] * scale, cy - self.right[i] * scale)

            cr.stroke()

    def find_scope_trigger_index(self, samples, count):

        # A rising zero-crossing (negative sample immediately followed
        # by a positive one) close to the start of the buffer - the
        # classic oscilloscope trigger. Without this, plotting a fixed
        # window of the rolling buffer every frame would show the
        # waveform sliding/jittering left-right randomly frame to
        # frame, since successive audio chunks land at an arbitrary
        # phase relative to the display window - triggering on the
        # same point in the waveform's own cycle each time is what
        # makes a real scope's display look "locked" instead. Only
        # searches within a range that still leaves a full `count`
        # samples after the match; falls back to 0 (no trigger found -
        # silence, noise, or content with no clean zero-crossing) so
        # the trace still draws *something* rather than nothing.
        n = len(samples)
        limit = n - count

        if limit <= 0:
            return 0

        search_limit = min(limit, n // 2)

        for i in range(1, search_limit):
            if samples[i - 1] <= 0.0 and samples[i] > 0.0:
                return i

        return 0

    def draw_scope_trace(self, cr, x, y, w, h, samples, opacity, label):

        cr.set_source_rgba(1, 1, 1, 0.06)
        cr.rectangle(x, y, w, h)
        cr.fill()

        cr.set_source_rgba(*self.canvas_foreground_rgba(0.15))
        cr.move_to(x, y + h / 2)
        cr.line_to(x + w, y + h / 2)
        cr.stroke()

        n = len(samples)

        if n < 2:
            return

        count = min(self.scope_time_base, n)

        start = (
            self.find_scope_trigger_index(samples, count)
            if self.scope_trigger else max(0, n - count)
        )

        count = min(count, n - start)

        if count < 2:
            return

        cr.set_line_width(1.2)

        if self.color_mode == "rainbow":

            prev_px, prev_py = None, None

            for i in range(count):

                px = x + (i / (count - 1)) * w
                py = y + h / 2 - samples[start + i] * (h / 2 * 0.9)

                if prev_px is not None:
                    cr.set_source_rgba(*rainbow_color(i / (count - 1)), opacity)
                    cr.move_to(prev_px, prev_py)
                    cr.line_to(px, py)
                    cr.stroke()

                prev_px, prev_py = px, py

        else:

            cr.set_source_rgba(
                self.color.red, self.color.green, self.color.blue, opacity
            )

            for i in range(count):

                px = x + (i / (count - 1)) * w
                py = y + h / 2 - samples[start + i] * (h / 2 * 0.9)

                if i == 0:
                    cr.move_to(px, py)
                else:
                    cr.line_to(px, py)

            cr.stroke()

        if self.show_labels:
            self.draw_text_label(cr, x + 4, y + h - 4, label)

    def draw_oscilloscope(self, cr, width, height):

        cr.set_source_rgb(*self.canvas_background_rgb())
        cr.paint()

        margin = 10
        cell_height = (height - margin * 3) / 2

        left_samples = list(self.scope_left)
        right_samples = list(self.scope_right)

        self.draw_scope_trace(
            cr, margin, margin, width - margin * 2, cell_height,
            left_samples, 1.0, "L"
        )
        self.draw_scope_trace(
            cr, margin, margin * 2 + cell_height, width - margin * 2, cell_height,
            right_samples, 1.0, "R"
        )

    def stereo_correlation(self):

        # Pearson correlation of L and R over the current chunk: +1.0
        # is mono (identical channels), 0.0 is wide/uncorrelated
        # stereo, -1.0 is fully out of phase (a real mono-compatibility
        # problem if sustained - summing to mono would cancel toward
        # silence) - the standard reading a hardware/software
        # correlation meter shows next to a goniometer.
        count = min(len(self.left), len(self.right))

        if count == 0:
            return 0.0

        sum_lr = sum(self.left[i] * self.right[i] for i in range(count))
        sum_ll = sum(v * v for v in self.left[:count])
        sum_rr = sum(v * v for v in self.right[:count])

        denominator = math.sqrt(sum_ll * sum_rr)

        if denominator < 1e-9:
            return 0.0

        return max(-1.0, min(1.0, sum_lr / denominator))

    def draw_vector_scope(self, cr, width, height):

        bg = self.canvas_background_rgb()

        if (
            self.vector_surface is None
            or self.vector_surface.get_width() != width
            or self.vector_surface.get_height() != height
        ):
            self.vector_surface = cairo.ImageSurface(
                cairo.FORMAT_ARGB32, max(1, width), max(1, height)
            )
            fresh = cairo.Context(self.vector_surface)
            fresh.set_source_rgb(*bg)
            fresh.paint()

        trail_cr = cairo.Context(self.vector_surface)

        # Phosphor-persistence trick: instead of clearing to the
        # background every frame (which would make this just a
        # differently-rotated X-Y Scope), partially overpaint the
        # existing trail with the background color at a low alpha -
        # old points fade out exponentially over several frames rather
        # than vanishing instantly, which is what makes a goniometer's
        # display read as a "cloud" with density/shape instead of a
        # single instantaneous dot.
        trail_cr.set_operator(cairo.OPERATOR_OVER)
        trail_cr.set_source_rgba(*bg, 1.0 - self.vector_persistence)
        trail_cr.rectangle(0, 0, width, height)
        trail_cr.fill()

        cx, cy = width / 2, height / 2
        scale = min(width, height) / 2 - 12

        # Rotated 45° from a plain L/R plot - Mid ((L+R)/sqrt(2)) on
        # the vertical axis, Side ((L-R)/sqrt(2)) on the horizontal -
        # the standard goniometer convention: mono material (L == R)
        # draws a vertical line since Side is always 0, and fully out-
        # of-phase material (L == -R) draws a horizontal line since Mid
        # is always 0. A plain, unrotated L-vs-R plot (the existing X-Y
        # Scope) doesn't carry this same "read stereo width/phase at a
        # glance" meaning.
        count = min(len(self.left), len(self.right))
        half_dot = self.vector_dot_size / 2
        rainbow = self.color_mode == "rainbow"

        if not rainbow:
            trail_cr.set_source_rgba(
                self.color.red, self.color.green, self.color.blue, 0.85
            )

        for i in range(count):

            mid = (self.left[i] + self.right[i]) * 0.70710678
            side = (self.left[i] - self.right[i]) * 0.70710678

            px = cx + side * scale
            py = cy - mid * scale

            if rainbow:
                # Colored by sample index within the current chunk,
                # not position in the M/S plane - a moving rainbow
                # cycling through the cloud over time, rather than a
                # fixed color-by-location scheme that would fight the
                # persistence trail's own sense of "recent."
                trail_cr.set_source_rgba(
                    *rainbow_color(i / max(1, count - 1)), 0.85
                )
                trail_cr.rectangle(
                    px - half_dot, py - half_dot,
                    self.vector_dot_size, self.vector_dot_size
                )
                trail_cr.fill()
            else:
                trail_cr.rectangle(
                    px - half_dot, py - half_dot,
                    self.vector_dot_size, self.vector_dot_size
                )

        if not rainbow:
            trail_cr.fill()

        cr.set_source_surface(self.vector_surface, 0, 0)
        cr.paint()

        # Axis lines and labels are drawn fresh onto the visible
        # context every frame, not into vector_surface - they're fixed
        # reference marks, not part of the fading signal trail.
        cr.set_source_rgba(*self.canvas_foreground_rgba(0.18))
        cr.set_line_width(1.0)
        cr.move_to(cx, 0)
        cr.line_to(cx, height)
        cr.stroke()
        cr.move_to(0, cy)
        cr.line_to(width, cy)
        cr.stroke()

        if self.show_labels:

            self.draw_text_label(cr, cx - 4, 14, "M")
            self.draw_text_label(cr, width - 16, cy + 4, "S")

            correlation = self.stereo_correlation()

            self.draw_text_label(
                cr, cx - 24, height - 6, f"Corr {correlation:+.2f}"
            )

    def dvd_tick(self):

        width = self.drawing_area.get_width()
        height = self.drawing_area.get_height()

        # Not yet allocated a real size (window still opening) -
        # nothing to bounce inside of yet. Keep the timer alive
        # (GLib.timeout_add semantics: True = call again) rather than
        # giving up, since this is purely a startup race, not a
        # reason to stop animating for the rest of the window's life.
        if width <= 0 or height <= 0:
            return True

        dt = DVD_TICK_INTERVAL_MS / 1000.0

        raw_level = max(rms(self.left), rms(self.right)) * VU_GAIN

        self.dvd_level = max(raw_level, self.dvd_level * 0.9)
        level = min(self.dvd_level, 1.0)

        # Beat pulse: a separate, snappier reaction layered on top of
        # the continuous level-driven scaling below - "how loud right
        # now" (level, VU-meter-style attack/decay) doesn't capture
        # "a beat just hit", which is a sudden jump *above the recent
        # average*, not just a loud moment (a sustained loud passage
        # has high level throughout but no discrete beats in that
        # sense). Same rolling-average-comparison idea as the app's
        # own Energy Threshold beat detector (docs/beat-detection.md),
        # kept self-contained here rather than reusing that one - it
        # runs JS-side, driven off a different tap of the audio graph,
        # with no existing path forwarding its detections to aux
        # windows.
        if self.dvd_beats_enabled:

            self.dvd_energy_history.append(raw_level)

            average = (
                sum(self.dvd_energy_history) / len(self.dvd_energy_history)
                if self.dvd_energy_history else 0.0
            )

            self.dvd_beat_cooldown = max(0.0, self.dvd_beat_cooldown - dt)

            if (
                self.dvd_beat_cooldown <= 0.0
                and raw_level > 0.08
                and raw_level > average * self.dvd_beat_sensitivity
            ):
                self.dvd_beat_pulse = 1.0
                # A floor under how often a beat can retrigger - purely
                # a debounce (one real hit's rising edge shouldn't
                # count as several beats in a row), not a tempo
                # estimate.
                self.dvd_beat_cooldown = 0.12
            else:
                self.dvd_beat_pulse *= 0.8

        else:
            self.dvd_beat_pulse = 0.0

        speed = self.dvd_speed_scale * (1.0 + level * 1.5 * self.dvd_reactivity)

        new_size = self.dvd_base_size * (
            1.0
            + level * 0.35 * self.dvd_reactivity
            + self.dvd_beat_pulse * 0.6 * self.dvd_reactivity
        )

        # dvd_x/dvd_y are the bounding box's top-left corner (that's
        # what the wall-collision checks below, and every draw
        # function, treat them as) - growing/shrinking dvd_size alone
        # would visibly expand the icon from that corner instead of
        # its center. Shifting the corner by half of whatever the size
        # just changed by keeps the box's center fixed across the
        # resize, which is what actually reads as "the icon pulses
        # from its center" - a purely cosmetic correction, doesn't
        # change anything about how the box moves or bounces.
        size_delta = new_size - self.dvd_size
        self.dvd_x -= size_delta / 2
        self.dvd_y -= size_delta / 2
        self.dvd_size = new_size

        self.dvd_x += self.dvd_vx * dt * speed
        self.dvd_y += self.dvd_vy * dt * speed

        bounced = False

        if self.dvd_x <= 0:
            self.dvd_x = 0.0
            self.dvd_vx = abs(self.dvd_vx)
            bounced = True
        elif self.dvd_x + self.dvd_size >= width:
            self.dvd_x = width - self.dvd_size
            self.dvd_vx = -abs(self.dvd_vx)
            bounced = True

        if self.dvd_y <= 0:
            self.dvd_y = 0.0
            self.dvd_vy = abs(self.dvd_vy)
            bounced = True
        elif self.dvd_y + self.dvd_size >= height:
            self.dvd_y = height - self.dvd_size
            self.dvd_vy = -abs(self.dvd_vy)
            bounced = True

        if bounced:
            self.dvd_color = random_bounce_color()

        self.drawing_area.queue_draw()

        return True

    def load_dvd_icon(self, size):

        # Cached across frames (called every tick, ~60/sec) - only
        # actually reloaded from the icon theme/disk when the chosen
        # icon or the (audio-reactive, so constantly-changing) pixel
        # size genuinely changes.
        cache_key = (self.icon_name, size)

        if cache_key == self._icon_cache_key:
            return self._icon_pixbuf

        pixbuf = None

        try:
            icon_theme = Gtk.IconTheme.get_for_display(Gdk.Display.get_default())

            paintable = icon_theme.lookup_icon(
                self.icon_name, None, size, 1,
                Gtk.TextDirection.NONE, 0
            )

            icon_file = paintable.get_file()

            if icon_file is not None and icon_file.get_path():
                pixbuf = GdkPixbuf.Pixbuf.new_from_file_at_size(
                    icon_file.get_path(), size, size
                )

        # Icon lookup/decoding failing (a theme missing every
        # fallback name, an unreadable file) shouldn't take the whole
        # window down - draw_dvd_bounce falls back to a plain circle
        # when this returns None.
        except GLib.Error:
            pixbuf = None

        self._icon_cache_key = cache_key
        self._icon_pixbuf = pixbuf

        return pixbuf

    def rounded_rect_path(self, cr, x, y, w, h, r):

        r = min(r, w / 2, h / 2)

        cr.new_sub_path()
        cr.arc(x + w - r, y + r, r, -math.pi / 2, 0)
        cr.arc(x + w - r, y + h - r, r, 0, math.pi / 2)
        cr.arc(x + r, y + h - r, r, math.pi / 2, math.pi)
        cr.arc(x + r, y + r, r, math.pi, 3 * math.pi / 2)
        cr.close_path()

    def load_dvd_logo_pixbuf(self, size):

        # The real DVD logo (public-domain vector, below the copyright
        # threshold of originality - see DVD_LOGO_RESOURCE) bundled
        # into the app's own gresource rather than the icon theme,
        # since it's not something any system icon theme would ever
        # ship. Cached the same way load_dvd_icon caches theme lookups
        # - this is called every tick (~60/sec) and size is
        # continuously audio-reactive.
        cache_key = ("__dvd_logo_resource__", size)

        if cache_key == self._icon_cache_key:
            return self._icon_pixbuf

        pixbuf = None

        try:
            # The real logo's own aspect ratio (~2.27:1, wide) -
            # rendered to fit the bounce bbox's width rather than
            # stretched into a square.
            target_h = max(1, int(size / DVD_LOGO_ASPECT))

            pixbuf = GdkPixbuf.Pixbuf.new_from_resource_at_scale(
                DVD_LOGO_RESOURCE, size, target_h, True
            )

        # Missing gdk-pixbuf SVG loader (needs librsvg/gdk-pixbuf's
        # loaders-svg module - not guaranteed present on every system)
        # is the realistic failure case here, not a bad resource path
        # (that's a packaging bug, not a runtime condition to hide) -
        # draw_dvd_text_logo falls back to the hand-drawn wordmark
        # plate either way.
        except GLib.Error:
            pixbuf = None

        self._icon_cache_key = cache_key
        self._icon_pixbuf = pixbuf

        return pixbuf

    def draw_dvd_text_logo(self, cr, size):

        pixbuf = self.load_dvd_logo_pixbuf(size)

        if pixbuf is not None:

            cr.save()
            cr.translate(
                self.dvd_x, self.dvd_y + (size - pixbuf.get_height()) / 2
            )

            # Recolored the same way draw_dvd_bounce recolors a
            # symbolic icon - the real logo's path is a plain black
            # fill on transparent, so its own alpha channel works
            # directly as a mask for a flat fill in the current bounce
            # color.
            cr.push_group()
            Gdk.cairo_set_source_pixbuf(cr, pixbuf, 0, 0)
            cr.paint()
            mask = cr.pop_group()

            cr.set_source_rgba(
                self.dvd_color.red, self.dvd_color.green, self.dvd_color.blue, 1.0
            )
            cr.mask(mask)

            cr.restore()
            return

        # Fallback: the real logo failed to load (see
        # load_dvd_logo_pixbuf) - an original hand-drawn "DVD"
        # wordmark plate stands in, so the window still shows
        # something recognizable either way.
        cr.save()
        cr.translate(self.dvd_x, self.dvd_y)

        # A wide plate rather than the square bounding box dvd_tick
        # collides against - "DVD" reads as a wordmark, not a square
        # icon - centered vertically within that square so the
        # collision math elsewhere doesn't need to know about it.
        box_w = size
        box_h = size * 0.5
        box_y = (size - box_h) / 2
        radius = box_h * 0.2

        self.rounded_rect_path(cr, 0, box_y, box_w, box_h, radius)
        cr.set_source_rgba(
            self.dvd_color.red, self.dvd_color.green, self.dvd_color.blue, 1.0
        )
        cr.fill()

        cr.select_font_face(
            "sans-serif", cairo.FONT_SLANT_NORMAL, cairo.FONT_WEIGHT_BOLD
        )
        cr.set_font_size(box_h * 0.55)

        extents = cr.text_extents("DVD")

        text_x = box_w / 2 - extents.width / 2 - extents.x_bearing
        text_y = box_y + box_h / 2 - extents.height / 2 - extents.y_bearing

        cr.set_source_rgb(1, 1, 1)
        cr.move_to(text_x, text_y)
        cr.show_text("DVD")

        cr.restore()

    def ellipse_path(self, cr, cx, cy, rx, ry):

        cr.new_sub_path()
        cr.save()
        cr.translate(cx, cy)
        cr.scale(rx, ry)
        cr.arc(0, 0, 1, 0, 2 * math.pi)
        cr.restore()
        cr.close_path()

    def load_tux_pixbuf(self, size):

        # The real Tux (see TUX_ICON above), bundled into the app's
        # own gresource. Cached the same way load_dvd_logo_pixbuf
        # caches its lookup - called every tick (~60/sec) with a
        # continuously audio-reactive size.
        cache_key = ("__tux_resource__", size)

        if cache_key == self._icon_cache_key:
            return self._icon_pixbuf

        pixbuf = None

        try:
            # Tux's own aspect ratio (216x256, taller than wide) -
            # fit to the bounce bbox's height rather than stretched
            # into a square.
            target_w = max(1, int(size * TUX_ASPECT))

            pixbuf = GdkPixbuf.Pixbuf.new_from_resource_at_scale(
                TUX_RESOURCE, target_w, size, True
            )

        # Same realistic failure case as load_dvd_logo_pixbuf - a
        # missing gdk-pixbuf SVG loader, not a bad resource path.
        except GLib.Error:
            pixbuf = None

        self._icon_cache_key = cache_key
        self._icon_pixbuf = pixbuf

        return pixbuf

    def draw_tux_icon(self, cr, size):

        pixbuf = self.load_tux_pixbuf(size)

        if pixbuf is not None:

            cr.save()
            cr.translate(
                self.dvd_x + (size - pixbuf.get_width()) / 2,
                self.dvd_y + (size - pixbuf.get_height()) / 2
            )

            # Shown as-is, not recolored - Tux is genuinely full-color
            # (confirmed via its own SVG fill attributes), the same
            # treatment draw_dvd_bounce already gives the full-color
            # app-logo option. A real Tux doesn't change color when it
            # bounces off a wall, only the DVD wordmark does.
            Gdk.cairo_set_source_pixbuf(cr, pixbuf, 0, 0)
            cr.paint()

            cr.restore()
            return

        # Fallback: the real artwork failed to load (see
        # load_tux_pixbuf) - an original, simplified penguin doodle
        # stands in, not a reproduction of Tux's actual official
        # artwork - laid out in a 0..1 unit square, then scaled up to
        # the current bounce size. Fixed colors throughout (like the
        # app-logo branch of draw_dvd_bounce) rather than tinted by
        # dvd_color, for the same reason as the real artwork above.
        cr.save()
        cr.translate(self.dvd_x, self.dvd_y)
        cr.scale(size, size)

        # Body.
        self.ellipse_path(cr, 0.5, 0.58, 0.36, 0.4)
        cr.set_source_rgb(0.06, 0.06, 0.08)
        cr.fill()

        # Head.
        self.ellipse_path(cr, 0.5, 0.2, 0.19, 0.19)
        cr.set_source_rgb(0.06, 0.06, 0.08)
        cr.fill()

        # Belly.
        self.ellipse_path(cr, 0.5, 0.64, 0.2, 0.28)
        cr.set_source_rgb(0.97, 0.97, 0.95)
        cr.fill()

        # Feet.
        self.ellipse_path(cr, 0.37, 0.98, 0.09, 0.035)
        cr.set_source_rgb(0.95, 0.65, 0.1)
        cr.fill()

        self.ellipse_path(cr, 0.63, 0.98, 0.09, 0.035)
        cr.set_source_rgb(0.95, 0.65, 0.1)
        cr.fill()

        # Beak.
        cr.move_to(0.5, 0.22)
        cr.line_to(0.58, 0.27)
        cr.line_to(0.5, 0.32)
        cr.close_path()
        cr.set_source_rgb(0.95, 0.65, 0.1)
        cr.fill()

        # Eyes.
        self.ellipse_path(cr, 0.43, 0.15, 0.045, 0.05)
        cr.set_source_rgb(1, 1, 1)
        cr.fill()

        self.ellipse_path(cr, 0.43, 0.16, 0.02, 0.022)
        cr.set_source_rgb(0.05, 0.05, 0.05)
        cr.fill()

        cr.restore()

    def draw_dvd_bounce(self, cr, width, height):

        cr.set_source_rgb(
            self.dvd_bg_color.red, self.dvd_bg_color.green, self.dvd_bg_color.blue
        )
        cr.paint()

        size = max(1, int(self.dvd_size))

        if self.icon_name == DVD_TEXT_ICON:
            self.draw_dvd_text_logo(cr, size)
            return

        if self.icon_name == TUX_ICON:
            self.draw_tux_icon(cr, size)
            return

        pixbuf = self.load_dvd_icon(size)

        if pixbuf is None:
            cr.set_source_rgba(
                self.dvd_color.red, self.dvd_color.green, self.dvd_color.blue, 0.9
            )
            cr.arc(
                self.dvd_x + size / 2, self.dvd_y + size / 2, size / 2,
                0, 2 * math.pi
            )
            cr.fill()
            return

        cr.save()
        cr.translate(self.dvd_x, self.dvd_y)

        if self.icon_name.endswith("-symbolic"):

            # Symbolic icons are monochrome-on-transparent by design,
            # meant to be recolored by whatever's displaying them
            # (normally done via GTK's own CSS "symbolic color"
            # machinery, which a plain GdkPixbuf load bypasses
            # entirely). Recreating that here: paint the pixbuf into
            # an offscreen group, then use that group's own alpha
            # channel as a mask for a flat fill in the bounce color -
            # same push_group/mask technique the mirror-reflection
            # fade already uses elsewhere in this file.
            cr.push_group()
            Gdk.cairo_set_source_pixbuf(cr, pixbuf, 0, 0)
            cr.paint()
            mask = cr.pop_group()

            cr.set_source_rgba(
                self.dvd_color.red, self.dvd_color.green, self.dvd_color.blue, 1.0
            )
            cr.mask(mask)

        else:
            # A full-color icon (the app logo, by default) - shown as-
            # is rather than recolored, the same way a real DVD logo
            # keeps its own printed colors and only the screensaver's
            # ambient tint/background changes.
            Gdk.cairo_set_source_pixbuf(cr, pixbuf, 0, 0)
            cr.paint()

        cr.restore()

    def update_spectrum_levels(self):

        bar_count = self.num_bars

        if len(self.bar_levels) != bar_count:
            self.bar_levels = [0.0] * bar_count

        if len(self.spectrum_bar_holds) != bar_count:
            self.spectrum_bar_holds = [0.0] * bar_count
            self.spectrum_bar_hold_times = [0.0] * bar_count

        if len(self.spectrum_buffer) == FFT_SIZE:

            windowed = [
                s * w for s, w in zip(self.spectrum_buffer, _HANN_WINDOW)
            ]

            spectrum = fft(windowed)
            magnitudes = [abs(v) for v in spectrum[:FFT_SIZE // 2]]

            new_levels = self.bars_from_magnitudes(magnitudes, bar_count)

            for i in range(bar_count):
                self.bar_levels[i] = max(
                    new_levels[i], self.bar_levels[i] * self.decay
                )

            if self.spectrum_peak_hold:

                # A small lagging cap above each bar - jumps to a new
                # peak instantly, lingers, then falls back down on its
                # own, same update_peak_hold logic the Peak Meter
                # already uses (its own peak-hold marker), just run
                # once per bar here instead of once per channel.
                now = time.monotonic()

                for i in range(bar_count):
                    self.spectrum_bar_holds[i], self.spectrum_bar_hold_times[i] = (
                        self.update_peak_hold(
                            self.bar_levels[i],
                            self.spectrum_bar_holds[i],
                            self.spectrum_bar_hold_times[i],
                            now
                        )
                    )

    def render_spectrum_bars(self, cr, width, height):

        bar_count = self.num_bars
        margin = 8
        gap = 3
        bar_area_width = width - margin * 2
        bar_width = max(1.0, (bar_area_width - gap * (bar_count - 1)) / bar_count)
        bar_height = height - margin * 2

        rainbow = self.color_mode == "rainbow"

        if not rainbow:
            cr.set_source_rgb(self.color.red, self.color.green, self.color.blue)

        for i, level in enumerate(self.bar_levels):

            x = margin + i * (bar_width + gap)
            filled = bar_height * min(level, 1.0) * self.spectrum_height_scale

            if rainbow:
                # By position across the frequency axis (a fixed hue
                # per bar) rather than by that bar's own level - the
                # classic "rainbow spectrum analyzer" look, a stable
                # gradient across the bars rather than colors
                # flickering with loudness.
                cr.set_source_rgb(*rainbow_color(i / max(1, bar_count - 1)))

            cr.rectangle(x, margin + (bar_height - filled), bar_width, filled)
            cr.fill()

            if self.spectrum_peak_hold and i < len(self.spectrum_bar_holds):

                hold_height = (
                    bar_height * min(self.spectrum_bar_holds[i], 1.0)
                    * self.spectrum_height_scale
                )
                hold_y = margin + (bar_height - hold_height)

                cr.set_source_rgba(*self.peak_hold_draw_color())
                cr.rectangle(x, hold_y - 2, bar_width, 2)
                cr.fill()

                if not rainbow:
                    cr.set_source_rgb(self.color.red, self.color.green, self.color.blue)

    def render_spectrum_smooth(self, cr, width, height):

        # A continuous curve through the same per-bar levels
        # render_spectrum_bars uses, instead of discrete rectangles -
        # a Catmull-Rom spline (converted to Cairo's own cubic Bezier
        # curve_to, which is what Cairo actually has) through each
        # bar's own (x, level) point, one bar-center apart, rather
        # than straight lines between them - what makes this read as
        # genuinely smooth rather than just "the same bars with their
        # corners connected."
        bar_count = self.num_bars
        margin = 8
        inner_width = width - margin * 2
        bar_height = height - margin * 2
        baseline_y = margin + bar_height

        if bar_count < 2 or len(self.bar_levels) < 2:
            return

        points = []

        for i, level in enumerate(self.bar_levels):

            x = margin + inner_width * (i / (bar_count - 1))
            y = baseline_y - bar_height * min(level, 1.0) * self.spectrum_height_scale
            points.append((x, y))

        cr.move_to(points[0][0], points[0][1])

        for i in range(len(points) - 1):

            p0 = points[i - 1] if i > 0 else points[i]
            p1 = points[i]
            p2 = points[i + 1]
            p3 = points[i + 2] if i + 2 < len(points) else points[i + 1]

            # Standard Catmull-Rom -> cubic Bezier control point
            # conversion (each segment's tangent estimated from its
            # neighbors on either side).
            c1x = p1[0] + (p2[0] - p0[0]) / 6.0
            c1y = p1[1] + (p2[1] - p0[1]) / 6.0
            c2x = p2[0] - (p3[0] - p1[0]) / 6.0
            c2y = p2[1] - (p3[1] - p1[1]) / 6.0

            cr.curve_to(c1x, c1y, c2x, c2y, p2[0], p2[1])

        cr.line_to(points[-1][0], baseline_y)
        cr.line_to(points[0][0], baseline_y)
        cr.close_path()

        if self.color_mode == "rainbow":

            # A real left-to-right rainbow gradient fill (Cairo's own
            # LinearGradient), rather than per-bar solid fills the way
            # render_spectrum_bars does it - there's no per-bar
            # boundary left to color independently once this is one
            # continuous filled shape.
            gradient = cairo.LinearGradient(margin, 0, margin + inner_width, 0)

            stops = 8

            for s in range(stops + 1):
                t = s / stops
                gradient.add_color_stop_rgb(t, *rainbow_color(t))

            cr.set_source(gradient)

        else:
            cr.set_source_rgba(
                self.color.red, self.color.green, self.color.blue, 0.85
            )

        cr.fill_preserve()

        cr.set_source_rgba(1, 1, 1, 0.3)
        cr.set_line_width(1.2)
        cr.stroke()

        if self.spectrum_peak_hold:

            for i, hold in enumerate(self.spectrum_bar_holds):

                if i >= bar_count:
                    break

                x = margin + inner_width * (i / (bar_count - 1))
                y = baseline_y - bar_height * min(hold, 1.0) * self.spectrum_height_scale

                cr.set_source_rgba(*self.peak_hold_draw_color(0.8))
                cr.rectangle(x - 3, y - 1, 6, 2)
                cr.fill()

    def update_spectrogram_columns(self):

        bin_count = self.num_bars

        if len(self.spectrum_buffer) != FFT_SIZE:
            return

        now = time.monotonic()

        if (
            now - self.last_spectrogram_frame_time
            < SPECTROGRAM_FRAME_INTERVAL / self.spectrogram_speed
        ):
            return

        self.last_spectrogram_frame_time = now

        windowed = [
            s * w for s, w in zip(self.spectrum_buffer, _HANN_WINDOW)
        ]

        spectrum = fft(windowed)
        magnitudes = [abs(v) for v in spectrum[:FFT_SIZE // 2]]

        self.spectrogram_columns.append(
            self.bars_from_magnitudes(magnitudes, bin_count)
        )

    def render_spectrogram(self, cr, width, height):

        if not self.spectrogram_columns:
            return

        bin_count = self.num_bars
        column_count = len(self.spectrogram_columns)

        if self.spectrogram_vertical:

            # Time runs top-to-bottom instead of left-to-right -
            # newest row at the top, scrolling downward, the usual
            # convention for a vertically-oriented waterfall (e.g. SDR
            # receiver software) - and frequency runs left-to-right
            # instead of bottom-to-top, low frequency at the left.
            row_height = height / SPECTROGRAM_COLUMNS
            column_width = width / bin_count

        else:

            column_width = width / SPECTROGRAM_COLUMNS
            row_height = height / bin_count

        # Newest column nearest the "front" edge (right in horizontal
        # mode, top in vertical), oldest falls off the far edge - a
        # live scrolling chart, same convention the X-Y scope and the
        # main visualizer's own timeline already use for "newest is
        # closest to now."
        start_x = width - column_width * column_count

        for column_index, levels in enumerate(self.spectrogram_columns):

            if self.spectrogram_vertical:
                x = None
                y = (column_count - 1 - column_index) * row_height
            else:
                x = start_x + column_index * column_width

            for row_index, level in enumerate(levels):

                scaled_level = scale_level_for_color(
                    level, self.spectrogram_color_ceiling
                )

                if self.spectrogram_palette == "rainbow":
                    r, g, b = rainbow_color(scaled_level)
                else:
                    r, g, b = gradient_color(
                        scaled_level, self.spectrogram_color_lo,
                        self.spectrogram_color_hi
                    )
                cr.set_source_rgb(r, g, b)

                if self.spectrogram_vertical:
                    # Low frequencies at the left, same left-to-right
                    # "increasing pitch" reading as the Spectrum bars.
                    x = row_index * column_width
                else:
                    # Low frequencies at the bottom, same up-is-higher
                    # convention as the Spectrum bars.
                    y = height - (row_index + 1) * row_height

                # +0.5 so adjacent cells overlap slightly - without it,
                # Cairo's antialiasing leaves faint seams between
                # same-colored neighboring rectangles.
                cr.rectangle(x, y, column_width + 0.5, row_height + 0.5)
                cr.fill()

    def update_terrain_rows(self):

        bin_count = self.num_bars

        if len(self.spectrum_buffer) != FFT_SIZE:
            return

        now = time.monotonic()

        if now - self.last_terrain_frame_time < TERRAIN_FRAME_INTERVAL:
            return

        self.last_terrain_frame_time = now

        windowed = [
            s * w for s, w in zip(self.spectrum_buffer, _HANN_WINDOW)
        ]

        spectrum = fft(windowed)
        magnitudes = [abs(v) for v in spectrum[:FFT_SIZE // 2]]

        self.terrain_rows.append(self.bars_from_magnitudes(magnitudes, bin_count))
        self.terrain_dirty = True

    def project_3d_point(self, x, y, z, cx, cy, scale, azimuth, elevation):

        # A simple oblique/orthographic (not perspective-correct) 3D
        # projection, shared by every "3D" aux window kind (Terrain,
        # Pipes) - each keeps its own azimuth/elevation state (drag-
        # to-rotate, see on_terrain_drag_update/on_pipes_drag_update)
        # and passes it in explicitly rather than this method reading
        # a single shared self.azimuth/elevation, since more than one
        # such window can be open at once. Azimuth rotates around the
        # vertical (height) axis, elevation then tilts the camera to
        # look down at the result. No perspective divide - appropriate
        # for a stylized look (the same family of technique classic
        # ridgeline/mountain-range waterfall displays and simple
        # wireframe screensavers both use), and far cheaper per point
        # than a real perspective pipeline would be. Returns the
        # projected screen position plus a rotated depth value used
        # purely for back-to-front painter's-algorithm sorting, not
        # for the projection itself.
        cos_a, sin_a = math.cos(azimuth), math.sin(azimuth)
        cos_e, sin_e = math.cos(elevation), math.sin(elevation)

        rx = x * cos_a - y * sin_a
        ry = x * sin_a + y * cos_a

        depth = ry * cos_e - z * sin_e
        rz = ry * sin_e + z * cos_e

        return (cx + rx * scale, cy - rz * scale, depth)

    def render_terrain_surface(self, width, height):

        self.terrain_surface = cairo.ImageSurface(
            cairo.FORMAT_ARGB32, max(1, width), max(1, height)
        )
        cr = cairo.Context(self.terrain_surface)

        cr.set_source_rgb(*self.canvas_background_rgb())
        cr.paint()

        rows = list(self.terrain_rows)

        if not rows:
            self.terrain_dirty = False
            return

        row_count = len(rows)
        bin_count = len(rows[0])

        cx = width / 2
        cy = height * 0.6
        scale = min(width, height) * 0.42 * self.terrain_zoom

        # Every row shares one Y (time/depth) position, so its own
        # rotated depth (used for the back-to-front sort below) is the
        # same for every point in it - only the first point's depth
        # needs computing per row, not one sort key per point.
        projected_rows = []

        for row_index, levels in enumerate(rows):

            y = (row_index / max(1, row_count - 1) - 0.5) * 2

            points = []

            for bin_index, level in enumerate(levels):

                x = (bin_index / max(1, bin_count - 1) - 0.5) * 2
                points.append(self.project_3d_point(
                    x, y, level, cx, cy, scale,
                    self.terrain_azimuth, self.terrain_elevation
                ))

            base_points = []

            for bin_index in range(bin_count):

                x = (bin_index / max(1, bin_count - 1) - 0.5) * 2
                base_points.append(self.project_3d_point(
                    x, y, 0.0, cx, cy, scale,
                    self.terrain_azimuth, self.terrain_elevation
                ))

            # This row's own peak (not average) loudness, mapped
            # through a color scale - a genuine elevation-style color
            # map (quiet moments read one color, loud ones another)
            # rather than one flat hue across every ridge regardless
            # of how loud it was. Peak rather than average
            # specifically: most individual frequency bins in a row
            # are quiet at any given moment even when the track isn't,
            # so averaging across all of them (an earlier version of
            # this) dragged nearly every row toward the low end of the
            # scale almost all the time - reported from real use as
            # "mostly just blue" once Rainbow (a full hue sweep, where
            # low readings are all blue-violet) became the default
            # palette. A row's peak - did *any* frequency in it hit
            # hard - reflects what's actually happening far better.
            peak_level = max(levels) if levels else 0.0
            scaled_peak_level = scale_level_for_color(
                peak_level, self.terrain_color_ceiling
            )

            if self.terrain_palette == "rainbow":
                row_color = rainbow_color(scaled_peak_level)
            else:
                row_color = gradient_color(
                    scaled_peak_level, self.terrain_color_lo, self.terrain_color_hi
                )

            projected_rows.append((points[0][2], points, base_points, row_color))

        # Painter's algorithm: farthest rows (smallest depth) drawn
        # first, nearest (largest depth) drawn last, on top - correct
        # occlusion for whatever the current camera rotation is,
        # without needing true hidden-surface removal.
        projected_rows.sort(key=lambda entry: entry[0])

        for depth, points, base_points, row_color in projected_rows:

            # Filled silhouette under the ridge line, back down to a
            # flat baseline - an opaque body (not just a wireframe
            # line) is what lets a nearer row actually occlude a
            # farther one, the same technique classic ridgeline/
            # "joy division style" plots use, just projected through a
            # rotatable camera here instead of stacked flat in 2D.
            cr.move_to(points[0][0], points[0][1])

            for px, py, _ in points[1:]:
                cr.line_to(px, py)

            for px, py, _ in reversed(base_points):
                cr.line_to(px, py)

            cr.close_path()

            # High, mostly-opaque alpha - painter's-algorithm occlusion
            # only actually works if a nearer row's fill is opaque
            # enough to hide what's behind it. Darker toward the back
            # and brighter toward the front as a cheap depth cue on top
            # of the row's own gradient color, since there's no real
            # lighting model here.
            shade = 0.35 + 0.55 * min(1.0, max(0.0, (depth + 1.0) / 2.0))
            cr.set_source_rgba(
                row_color[0] * shade,
                row_color[1] * shade,
                row_color[2] * shade,
                0.92
            )
            cr.fill_preserve()

            outline_color = ensure_min_brightness(row_color)
            cr.set_source_rgba(*outline_color, 0.9)
            cr.set_line_width(1.5)
            cr.new_path()
            cr.move_to(points[0][0], points[0][1])

            for px, py, _ in points[1:]:
                cr.line_to(px, py)

            cr.stroke()

        self.terrain_dirty = False

    def draw_3d_axes(
        self, cr, cx, cy, scale, azimuth, elevation, z_length, z_label
    ):

        # Fixed reference lines - always drawn fresh onto the visible
        # frame, never baked into a cached render surface, same
        # reasoning Vector Scope's own axis crosshair already uses:
        # these are reference marks, not part of the signal being
        # displayed, so there's nothing to gain from caching them and
        # every reason not to (they'd have to be excluded/redrawn on
        # top of the cache regardless). Origin at the "quietest,
        # oldest, lowest-frequency" corner rather than the center, so
        # the three axes read as a single connected corner frame
        # instead of three lines crossing through the middle of the
        # data.
        origin = self.project_3d_point(-1, -1, 0, cx, cy, scale, azimuth, elevation)
        freq_end = self.project_3d_point(1, -1, 0, cx, cy, scale, azimuth, elevation)
        time_end = self.project_3d_point(-1, 1, 0, cx, cy, scale, azimuth, elevation)

        cr.set_source_rgba(*self.canvas_foreground_rgba(0.4))
        cr.set_line_width(1.2)

        cr.move_to(origin[0], origin[1])
        cr.line_to(freq_end[0], freq_end[1])
        cr.stroke()

        cr.move_to(origin[0], origin[1])
        cr.line_to(time_end[0], time_end[1])
        cr.stroke()

        self.draw_text_label(cr, freq_end[0] - 24, freq_end[1] + 4, "Freq")
        self.draw_text_label(cr, time_end[0] + 4, time_end[1], "Time")

        if z_length > 0:

            height_end = self.project_3d_point(
                -1, -1, z_length, cx, cy, scale, azimuth, elevation
            )

            cr.move_to(origin[0], origin[1])
            cr.line_to(height_end[0], height_end[1])
            cr.stroke()

            self.draw_text_label(cr, height_end[0] + 4, height_end[1], z_label)

    def draw_terrain(self, cr, width, height):

        self.update_terrain_rows()

        if (
            self.terrain_dirty
            or self.terrain_surface is None
            or self.terrain_surface.get_width() != width
            or self.terrain_surface.get_height() != height
        ):
            self.render_terrain_surface(width, height)

        cr.set_source_surface(self.terrain_surface, 0, 0)
        cr.paint()

        if self.terrain_show_axes:

            cx = width / 2
            cy = height * 0.6
            scale = min(width, height) * 0.42 * self.terrain_zoom

            self.draw_3d_axes(
                cr, cx, cy, scale,
                self.terrain_azimuth, self.terrain_elevation,
                z_length=1.0, z_label="Level"
            )

    def update_waterfall_rows(self):

        bin_count = self.num_bars

        if len(self.spectrum_buffer) != FFT_SIZE:
            return

        now = time.monotonic()

        if now - self.last_waterfall_frame_time < WATERFALL_FRAME_INTERVAL:
            return

        self.last_waterfall_frame_time = now

        windowed = [
            s * w for s, w in zip(self.spectrum_buffer, _HANN_WINDOW)
        ]

        spectrum = fft(windowed)
        magnitudes = [abs(v) for v in spectrum[:FFT_SIZE // 2]]

        self.waterfall_rows.append(self.bars_from_magnitudes(magnitudes, bin_count))
        self.waterfall_dirty = True

    def render_waterfall_surface(self, width, height):

        self.waterfall_surface = cairo.ImageSurface(
            cairo.FORMAT_ARGB32, max(1, width), max(1, height)
        )
        cr = cairo.Context(self.waterfall_surface)

        cr.set_source_rgb(*self.canvas_background_rgb())
        cr.paint()

        rows = list(self.waterfall_rows)

        if len(rows) < 2:
            self.waterfall_dirty = False
            return

        row_count = len(rows)
        bin_count = len(rows[0])

        cx = width / 2
        cy = height * 0.55
        scale = min(width, height) * 0.42 * self.waterfall_zoom

        # Each point's own Z comes from that exact (row, bin)'s own
        # level, scaled by waterfall_height_scale - 0 (the default
        # before this was added) keeps the original perfectly flat
        # plane, where magnitude reads purely as color; >0 turns it
        # into a genuine height-mapped mesh, requested directly,
        # without losing the per-cell color/texture that made this a
        # different thing from Terrain's own height-only ridges in the
        # first place. One projected grid of points, shared by every
        # quad that touches it (each interior point belongs to up to 4
        # neighboring cells) rather than projecting the same corner
        # repeatedly.
        if self.waterfall_palette == "rainbow":
            row_colors = [
                [
                    rainbow_color(
                        scale_level_for_color(level, self.waterfall_color_ceiling)
                    )
                    for level in levels
                ]
                for levels in rows
            ]
        else:
            row_colors = [
                [
                    gradient_color(
                        scale_level_for_color(level, self.waterfall_color_ceiling),
                        self.waterfall_color_lo, self.waterfall_color_hi
                    )
                    for level in levels
                ]
                for levels in rows
            ]

        grid = []

        for row_index in range(row_count):

            y = (row_index / max(1, row_count - 1) - 0.5) * 2
            row_points = []
            levels = rows[row_index]

            for bin_index in range(bin_count):

                x = (bin_index / max(1, bin_count - 1) - 0.5) * 2
                z = levels[bin_index] * self.waterfall_height_scale
                row_points.append(self.project_3d_point(
                    x, y, z, cx, cy, scale,
                    self.waterfall_azimuth, self.waterfall_elevation
                ))

            grid.append(row_points)

        # One quad per grid cell, each flat-shaded with its own
        # (row, bin) cell's own color - the same per-cell block-color
        # look the flat 2D Spectrogram already has, just projected
        # through a rotatable oblique camera instead of drawn straight
        # onto the canvas. Depth-sorted per quad (not per row, unlike
        # Terrain, since a flat plane's own rotation can put a far
        # corner of one row closer to the camera than a near corner of
        # another once azimuth departs from 0) - painter's algorithm
        # again, same reasoning as Terrain/Pipes.
        quads = []

        for row_index in range(row_count - 1):

            for bin_index in range(bin_count - 1):

                p1 = grid[row_index][bin_index]
                p2 = grid[row_index][bin_index + 1]
                p3 = grid[row_index + 1][bin_index + 1]
                p4 = grid[row_index + 1][bin_index]

                depth = (p1[2] + p2[2] + p3[2] + p4[2]) / 4
                color = row_colors[row_index][bin_index]

                quads.append((depth, p1, p2, p3, p4, color))

        quads.sort(key=lambda entry: entry[0])

        # No antialiasing for the cell fills specifically - adjacent
        # quads share exact corner coordinates, but AA'd edges between
        # differently-colored neighbors still leave faint seams
        # otherwise; crisp edges read as one continuous tiled surface
        # instead.
        # Fill and outline (see waterfall_textured below) are done
        # together per quad, in the same depth-sorted pass - not as
        # two full separate passes (an earlier version drew every fill
        # first, then every outline). That was wrong wherever the
        # surface has real height variation (waterfall_height_scale >
        # 0): a fill-only pass already establishes correct back-to-
        # front occlusion on its own, but a *second*, fully separate
        # outline pass runs entirely after it regardless of depth - so
        # a farther quad's outline (say, the mostly-hidden side of a
        # tall peak) could still get drawn *after*, and therefore on
        # top of, a nearer quad's already-painted fill. Reported from
        # real use as stray wireframe lines cutting across tall peaks.
        # Interleaving fixes it at the cost of switching antialiasing
        # twice per quad instead of twice total - fine given the
        # bounded quad count here.
        for depth, p1, p2, p3, p4, color in quads:

            cr.set_antialias(cairo.ANTIALIAS_NONE)

            cr.move_to(p1[0], p1[1])
            cr.line_to(p2[0], p2[1])
            cr.line_to(p3[0], p3[1])
            cr.line_to(p4[0], p4[1])
            cr.close_path()

            cr.set_source_rgb(*color)
            cr.fill_preserve()

            if self.waterfall_textured:
                cr.set_antialias(cairo.ANTIALIAS_DEFAULT)
                cr.set_source_rgba(0, 0, 0, 0.35)
                cr.set_line_width(1.0)
                cr.stroke()
            else:
                cr.new_path()

        cr.set_antialias(cairo.ANTIALIAS_DEFAULT)

        self.waterfall_dirty = False

    def draw_waterfall(self, cr, width, height):

        self.update_waterfall_rows()

        if (
            self.waterfall_dirty
            or self.waterfall_surface is None
            or self.waterfall_surface.get_width() != width
            or self.waterfall_surface.get_height() != height
        ):
            self.render_waterfall_surface(width, height)

        cr.set_source_surface(self.waterfall_surface, 0, 0)
        cr.paint()

        if self.waterfall_show_axes:

            cx = width / 2
            cy = height * 0.55
            scale = min(width, height) * 0.42 * self.waterfall_zoom

            self.draw_3d_axes(
                cr, cx, cy, scale,
                self.waterfall_azimuth, self.waterfall_elevation,
                z_length=self.waterfall_height_scale, z_label="Level"
            )

    def spawn_pipe(self):

        # A fresh list of every empty cell, scanned each spawn - only
        # called occasionally (topping up to pipes_max_pipes after a
        # pipe dies, or on a full reset), not every tick, so rescanning
        # up to PIPES_GRID_SIZE**3 cells (512 at the default size) each
        # time is cheap enough not to bother caching.
        size = PIPES_GRID_SIZE

        empty_cells = [
            (x, y, z)
            for x in range(size) for y in range(size) for z in range(size)
            if (x, y, z) not in self.pipes_occupied
        ]

        if not empty_cells:
            return None

        position = random.choice(empty_cells)
        self.pipes_occupied.add(position)

        # Cycled (not random) across the bands, so a handful of pipes
        # spread evenly across the spectrum instead of randomly
        # clustering on the same one or two bands.
        band = self.pipes_next_band
        self.pipes_next_band = (self.pipes_next_band + 1) % len(PIPES_BANDS)

        return {
            "pos": position,
            "dir": random.choice(PIPE_DIRECTIONS),
            "color": self.pipes_band_colors[band % len(self.pipes_band_colors)],
            "band": band,
            "step_timer": 0.0,
        }

    def reset_pipes(self):

        # The batch about to be cleared fades out instead of vanishing
        # instantly, if enabled - see draw_pipes for how
        # pipes_fading_segments actually gets rendered and expired.
        # Skipped when there's nothing to fade (a fresh window that's
        # never had a batch yet) so the very first reset doesn't fade
        # in an empty list for no reason.
        if self.pipes_fade_enabled and self.pipes_segments:
            self.pipes_fading_segments = list(self.pipes_segments)
            self.pipes_fade_start_time = time.monotonic()
        else:
            self.pipes_fading_segments = []

        self.pipes_occupied = set()
        self.pipes_segments = []
        self.pipes_active = []

        for _ in range(self.pipes_max_pipes):

            spawned = self.spawn_pipe()

            if spawned is None:
                break

            self.pipes_active.append(spawned)

    def magnitude_in_band(self, magnitudes, f_lo, f_hi):

        # Same peak-in-range + sqrt-compression approach as
        # bars_from_magnitudes, generalized to an arbitrary Hz range
        # instead of one of its own log-spaced bars specifically.
        bin_hz = SAMPLE_RATE / FFT_SIZE

        bin_lo = max(0, int(f_lo / bin_hz))
        bin_hi = max(bin_lo + 1, int(f_hi / bin_hz))
        bin_hi = min(bin_hi, len(magnitudes))

        if bin_lo >= len(magnitudes):
            return 0.0

        peak = max(magnitudes[bin_lo:bin_hi])

        return math.sqrt(min(peak / (FFT_SIZE / 4), 1.0))

    def update_pipes_band_levels(self):

        if len(self.spectrum_buffer) != FFT_SIZE:
            return

        now = time.monotonic()

        if now - self.last_pipes_band_frame_time < PIPES_BAND_FRAME_INTERVAL:
            return

        self.last_pipes_band_frame_time = now

        windowed = [
            s * w for s, w in zip(self.spectrum_buffer, _HANN_WINDOW)
        ]

        spectrum = fft(windowed)
        magnitudes = [abs(v) for v in spectrum[:FFT_SIZE // 2]]

        for i, (f_lo, f_hi) in enumerate(PIPES_BANDS):

            raw = self.magnitude_in_band(magnitudes, f_lo, f_hi)
            raw = min(1.0, raw * PIPES_BAND_GAINS[i])

            # Same VU-style fast-attack/slow-release ballistics as
            # everything else audio-reactive in this file, so a pipe's
            # speed doesn't flicker with every single FFT frame.
            self.pipes_band_levels[i] = max(raw, self.pipes_band_levels[i] * 0.85)

    def step_pipe(self, pipe, turn_chance=0.25):

        size = PIPES_GRID_SIZE
        x, y, z = pipe["pos"]
        current_dir = pipe["dir"]

        # Mostly keeps going straight (a pipe that turns every single
        # step looks like noise, not a pipe) - occasionally considers
        # turning instead, trying the perpendicular directions (never
        # reversing straight back the way it came - that would look
        # like backtracking, not a pipe growing) before falling back
        # to continuing straight if every turn is blocked. turn_chance
        # itself is driven by that pipe's own band level (pipes_tick) -
        # a louder band makes its pipes turn more erratically, a
        # second and more perceptible reactive cue than speed alone
        # (a direction change is much easier to notice at a glance
        # than "this one's gliding slightly faster").
        perpendicular = [
            d for d in PIPE_DIRECTIONS
            if d != current_dir and d != tuple(-v for v in current_dir)
        ]
        random.shuffle(perpendicular)

        if random.random() < turn_chance:
            candidates = perpendicular + [current_dir]
        else:
            candidates = [current_dir] + perpendicular

        for direction in candidates:

            next_pos = (
                x + direction[0], y + direction[1], z + direction[2]
            )

            in_bounds = all(0 <= v < size for v in next_pos)

            if in_bounds and next_pos not in self.pipes_occupied:

                self.pipes_segments.append(
                    (pipe["pos"], next_pos, pipe["color"], pipe["band"])
                )
                self.pipes_occupied.add(next_pos)
                pipe["pos"] = next_pos
                pipe["dir"] = direction

                return True

        return False

    def pipes_tick(self):

        dt = PIPES_TICK_INTERVAL_MS / 1000.0

        self.update_pipes_band_levels()

        # Beat detection itself always runs (it's cheap - a sum over a
        # bounded deque plus a couple of comparisons) rather than being
        # gated behind any one of the three reactions below, so each
        # of those can be toggled independently of the others: a
        # sudden jump in overall level above its own recent rolling
        # average (same rolling-average-comparison idea as DVD
        # Bounce's own beat detector, kept self-contained here rather
        # than shared).
        raw_level = max(rms(self.left), rms(self.right)) * VU_GAIN
        self.pipes_energy_history.append(raw_level)

        average = (
            sum(self.pipes_energy_history) / len(self.pipes_energy_history)
            if self.pipes_energy_history else 0.0
        )

        self.pipes_beat_cooldown = max(0.0, self.pipes_beat_cooldown - dt)

        beat_fired = (
            self.pipes_beat_cooldown <= 0.0
            and raw_level > 0.08
            and raw_level > average * self.pipes_beat_sensitivity
        )

        if beat_fired:

            self.pipes_beat_cooldown = 0.25

            # Spawns one bonus pipe beyond pipes_max_pipes, rather
            # than waiting for the normal top-up-to-max logic to ever
            # trigger one - a burst of extra activity right on the
            # hit, fading back to the normal count as it eventually
            # dies out on its own.
            if self.pipes_beats_enabled:

                burst = self.spawn_pipe()

                if burst is not None:
                    self.pipes_active.append(burst)

            # A brief azimuth spin-up plus a small random elevation
            # nudge - both just added velocity that decays back to 0
            # on its own (pipes_beat_spin_boost/pipes_beat_tilt_
            # velocity below), rather than snapping to a specific
            # angle, so it reads as "the rotation reacted to that hit"
            # rather than a jarring jump-cut.
            if self.pipes_beat_rotation_enabled:
                self.pipes_beat_spin_boost = 10.0
                self.pipes_beat_tilt_velocity = random.uniform(-8.0, 8.0)

            # A brief punch-in - the same decaying-value idea, added
            # on top of the user's own Zoom setting in draw_pipes
            # rather than overwriting it.
            if self.pipes_beat_zoom_enabled:
                self.pipes_beat_zoom_boost = 0.35

        # All three decay back toward 0 every tick regardless of
        # whether a beat just fired this exact tick, same fast-decay
        # shape as everything else audio-reactive in this file.
        self.pipes_beat_spin_boost *= 0.88
        self.pipes_beat_tilt_velocity *= 0.88
        self.pipes_beat_zoom_boost *= 0.88

        # Slow ambient auto-rotation (plus the beat kick above, when
        # enabled) - paused while a manual rotate drag is in progress
        # (on_pipes_drag_begin/end) so the two don't fight over
        # pipes_azimuth/elevation; azimuth wrapped to stay bounded
        # rather than growing without limit over a long-running window
        # (math.cos/sin handle any magnitude fine either way, this is
        # purely so the value itself doesn't grow forever).
        if self.pipes_auto_rotate and not self.pipes_dragging:

            self.pipes_azimuth = (
                self.pipes_azimuth
                + math.radians(self.pipes_rotate_speed + self.pipes_beat_spin_boost)
                * dt
            ) % (2 * math.pi)

            self.pipes_elevation = max(
                math.radians(-10), min(
                    math.radians(85),
                    self.pipes_elevation
                    + math.radians(self.pipes_beat_tilt_velocity) * dt
                )
            )

        # Each pipe advances on its own accumulator now, driven by its
        # own assigned band's level (see spawn_pipe/
        # update_pipes_band_levels) - not one shared timer/level for
        # every pipe - so different pipes visibly move at different
        # speeds depending on what's happening in their own part of
        # the spectrum right now.
        still_active = []

        for pipe in self.pipes_active:

            band_level = self.pipes_band_levels[pipe["band"] % len(self.pipes_band_levels)]

            speed = self.pipes_speed_scale * (
                1.0 + band_level * 2.5 * self.pipes_reactivity
            )
            pipe["step_timer"] += dt * speed

            turn_chance = 0.15 + band_level * 0.5 * self.pipes_reactivity

            alive = True

            # A while loop (not "if") so a very high Speed/Reactivity
            # combination can advance more than one grid step in a
            # single tick, rather than being capped at one step per
            # 16ms regardless of how large the requested speed is.
            while alive and pipe["step_timer"] >= PIPES_BASE_STEP_INTERVAL:
                pipe["step_timer"] -= PIPES_BASE_STEP_INTERVAL
                alive = self.step_pipe(pipe, turn_chance)

            if alive:
                still_active.append(pipe)

        self.pipes_active = still_active

        while len(self.pipes_active) < self.pipes_max_pipes:

            spawned = self.spawn_pipe()

            if spawned is None:
                break

            self.pipes_active.append(spawned)

        total_cells = PIPES_GRID_SIZE ** 3

        if (
            not self.pipes_active
            or len(self.pipes_occupied) >= total_cells * PIPES_RESET_FRACTION
        ):
            self.reset_pipes()

        self.drawing_area.queue_draw()

        return True

    def draw_pipes_grid_wireframe(self, cr, cx, cy, scale):

        # A wireframe outline of the play field's own boundary - the
        # closest thing to "axes" that make sense for a cube grid a
        # pipe walks around in, rather than literal labeled X/Y/Z
        # lines the way Terrain/Waterfall get (there's no meaningful
        # single "zero" origin here the way there is for a magnitude/
        # frequency/time plot). Drawn fresh every frame, not cached -
        # 12 edges is cheap enough not to bother.
        corners = {}

        for cx_u in (-1.0, 1.0):
            for cy_u in (-1.0, 1.0):
                for cz_u in (-1.0, 1.0):
                    corners[(cx_u, cy_u, cz_u)] = self.project_3d_point(
                        cx_u, cy_u, cz_u, cx, cy, scale,
                        self.pipes_azimuth, self.pipes_elevation
                    )

        edges = [
            ((-1, -1, -1), (1, -1, -1)), ((-1, 1, -1), (1, 1, -1)),
            ((-1, -1, 1), (1, -1, 1)), ((-1, 1, 1), (1, 1, 1)),
            ((-1, -1, -1), (-1, 1, -1)), ((1, -1, -1), (1, 1, -1)),
            ((-1, -1, 1), (-1, 1, 1)), ((1, -1, 1), (1, 1, 1)),
            ((-1, -1, -1), (-1, -1, 1)), ((1, -1, -1), (1, -1, 1)),
            ((-1, 1, -1), (-1, 1, 1)), ((1, 1, -1), (1, 1, 1)),
        ]

        cr.set_source_rgba(*self.canvas_foreground_rgba(0.2))
        cr.set_line_width(1.0)

        for a, b in edges:

            pa = corners[(float(a[0]), float(a[1]), float(a[2]))]
            pb = corners[(float(b[0]), float(b[1]), float(b[2]))]

            cr.move_to(pa[0], pa[1])
            cr.line_to(pb[0], pb[1])
            cr.stroke()

    def draw_pipes(self, cr, width, height):

        cr.set_source_rgb(*self.canvas_background_rgb())
        cr.paint()

        size = PIPES_GRID_SIZE
        cx, cy = width / 2, height / 2
        scale = (
            min(width, height) * 0.38
            * self.pipes_zoom * (1.0 + self.pipes_beat_zoom_boost)
        )

        def to_unit(v):
            return (v / (size - 1) - 0.5) * 2

        if self.pipes_show_grid:
            self.draw_pipes_grid_wireframe(cr, cx, cy, scale)

        # The previous batch, if still fading (see reset_pipes) - its
        # own alpha decays linearly over pipes_fade_seconds, expiring
        # (dropped entirely, not just drawn at 0 alpha) once the fade
        # finishes, so a fully-faded batch isn't still being projected
        # and sorted every frame for nothing.
        fade_alpha = 0.0

        if self.pipes_fading_segments:

            elapsed = time.monotonic() - self.pipes_fade_start_time
            fade_alpha = max(
                0.0, 1.0 - elapsed / max(0.01, self.pipes_fade_seconds)
            )

            if fade_alpha <= 0.0:
                self.pipes_fading_segments = []

        projected = []

        for cell_a, cell_b, color, band in self.pipes_segments:

            sx1, sy1, d1 = self.project_3d_point(
                to_unit(cell_a[0]), to_unit(cell_a[1]), to_unit(cell_a[2]),
                cx, cy, scale, self.pipes_azimuth, self.pipes_elevation
            )
            sx2, sy2, d2 = self.project_3d_point(
                to_unit(cell_b[0]), to_unit(cell_b[1]), to_unit(cell_b[2]),
                cx, cy, scale, self.pipes_azimuth, self.pipes_elevation
            )

            projected.append(((d1 + d2) / 2, sx1, sy1, sx2, sy2, color, 1.0, band))

        if fade_alpha > 0.0:

            for cell_a, cell_b, color, band in self.pipes_fading_segments:

                sx1, sy1, d1 = self.project_3d_point(
                    to_unit(cell_a[0]), to_unit(cell_a[1]), to_unit(cell_a[2]),
                    cx, cy, scale, self.pipes_azimuth, self.pipes_elevation
                )
                sx2, sy2, d2 = self.project_3d_point(
                    to_unit(cell_b[0]), to_unit(cell_b[1]), to_unit(cell_b[2]),
                    cx, cy, scale, self.pipes_azimuth, self.pipes_elevation
                )

                projected.append(
                    ((d1 + d2) / 2, sx1, sy1, sx2, sy2, color, fade_alpha, band)
                )

        # Painter's algorithm again (see render_terrain_surface) - not
        # perfect for pipes genuinely crossing in front of/behind one
        # another mid-segment, but a per-segment back-to-front sort
        # reads correctly in the vast majority of cases and is far
        # cheaper than real per-pixel depth testing would be.
        projected.sort(key=lambda entry: entry[0])

        cr.set_line_cap(cairo.LINE_CAP_ROUND)

        base_width = self.pipes_base_width

        for depth, sx1, sy1, sx2, sy2, color, alpha, band in projected:

            # Tube thickness pulses with that segment's own band's
            # live level, when enabled - a second, more continuous
            # reinforcement of the per-band reactivity on top of speed
            # (pipes_tick), rather than the only visible cue being how
            # fast a pipe moves.
            band_level = (
                self.pipes_band_levels[band % len(self.pipes_band_levels)]
                if self.pipes_band_levels else 0.0
            )

            if self.pipes_pulse_width:
                cr.set_line_width(base_width * (1.0 + band_level * 1.5))
            else:
                cr.set_line_width(base_width)

            if self.pipes_pulse_color:
                # Brighten toward vivid/white on a loud moment for
                # that pipe's own band, darken toward dim on a quiet
                # one - a third reactive cue (on top of speed and
                # width) using the same band_level already computed
                # above.
                factor = 0.45 + 0.85 * band_level
                cr.set_source_rgba(
                    min(1.0, color.red * factor),
                    min(1.0, color.green * factor),
                    min(1.0, color.blue * factor),
                    0.95 * alpha
                )
            else:
                cr.set_source_rgba(color.red, color.green, color.blue, 0.95 * alpha)

            cr.move_to(sx1, sy1)
            cr.line_to(sx2, sy2)
            cr.stroke()

            if self.pipes_tube_shading:

                # A thin, semi-transparent light stroke offset toward
                # one side of the segment - a specular highlight
                # running along the top of a cylinder, the cheapest
                # plausible stand-in for real lighting Cairo has no
                # model for. Offset perpendicular to the segment's own
                # direction, scaled by the segment's current width (a
                # fatter pulsing tube gets a proportionally wider-
                # spaced highlight) rather than a fixed pixel amount.
                current_width = cr.get_line_width()
                dx = sx2 - sx1
                dy = sy2 - sy1
                length = math.hypot(dx, dy)

                if length > 0.001:

                    offset = current_width * 0.22
                    perp_x = -dy / length * offset
                    perp_y = dx / length * offset

                    cr.set_source_rgba(1, 1, 1, 0.35 * alpha)
                    cr.set_line_width(max(0.6, current_width * 0.28))
                    cr.move_to(sx1 + perp_x, sy1 + perp_y)
                    cr.line_to(sx2 + perp_x, sy2 + perp_y)
                    cr.stroke()

        # A bright cap on each still-growing pipe's current head -
        # otherwise the newest segment's own line end looks identical
        # to any other joint, with no visual cue for "this is where
        # it's actively growing from right now."
        for pipe in self.pipes_active:

            x, y, z = pipe["pos"]

            sx, sy, _ = self.project_3d_point(
                to_unit(x), to_unit(y), to_unit(z),
                cx, cy, scale, self.pipes_azimuth, self.pipes_elevation
            )

            cr.set_source_rgba(1, 1, 1, 0.9)
            cr.arc(sx, sy, 4, 0, 2 * math.pi)
            cr.fill()

    def draw_frequency_labels(self, cr, width, height):

        if self.kind == "spectrum":
            self.draw_spectrum_labels(cr, width, height)
        elif self.kind == "spectrogram":
            self.draw_spectrogram_labels(cr, width, height)

    def draw_spectrum_labels(self, cr, width, height):

        bar_count = self.num_bars
        margin = 8
        gap = 3
        bar_area_width = width - margin * 2
        bar_width = max(1.0, (bar_area_width - gap * (bar_count - 1)) / bar_count)

        for freq, label in ((100, "100Hz"), (1000, "1kHz"), (10000, "10kHz")):

            index = self.bar_index_for_frequency(freq, bar_count)
            x = margin + index * (bar_width + gap)

            self.draw_text_label(cr, x, height - 4, label)

    def draw_spectrogram_labels(self, cr, width, height):

        bin_count = self.num_bars

        if self.spectrogram_vertical:

            column_width = width / bin_count

            for freq, label in ((100, "100Hz"), (1000, "1kHz"), (10000, "10kHz")):

                index = self.bar_index_for_frequency(freq, bin_count)
                x = index * column_width

                self.draw_text_label(cr, x + 2, 14, label)

        else:

            row_height = height / bin_count

            for freq, label in ((100, "100Hz"), (1000, "1kHz"), (10000, "10kHz")):

                index = self.bar_index_for_frequency(freq, bin_count)
                y = height - (index + 1) * row_height

                self.draw_text_label(cr, 4, y + row_height - 3, label)

    def draw_text_label(self, cr, x, y, text):

        cr.select_font_face(
            "sans-serif", cairo.FONT_SLANT_NORMAL, cairo.FONT_WEIGHT_NORMAL
        )
        cr.set_font_size(10)

        extents = cr.text_extents(text)
        padding = 2

        # A translucent backing rectangle - without it, light-colored
        # label text disappears against the spectrum/spectrogram's own
        # bright yellow/green content.
        cr.set_source_rgba(0, 0, 0, 0.55)
        cr.rectangle(
            x - padding,
            y - extents.height - padding,
            extents.width + padding * 2,
            extents.height + padding * 2
        )
        cr.fill()

        cr.set_source_rgba(1, 1, 1, 0.9)
        cr.move_to(x, y)
        cr.show_text(text)

    def bar_index_for_frequency(self, freq, bar_count):

        # Inverse of the log-spaced mapping in bars_from_magnitudes -
        # which bar/row a given reference frequency (100Hz, 1kHz, ...)
        # falls into, for labeling.
        min_freq = 20.0
        max_freq = SAMPLE_RATE / 2

        if freq <= min_freq:
            return 0

        ratio = max_freq / min_freq
        index = bar_count * math.log(freq / min_freq) / math.log(ratio)

        return max(0, min(bar_count - 1, int(index)))

    def bars_from_magnitudes(self, magnitudes, bar_count):

        # Log-spaced bins (20Hz-Nyquist) rather than linear - an
        # audio spectrum is perceived logarithmically, so linear
        # binning would crush almost the entire display into the
        # lowest handful of bars and leave the rest empty.
        min_freq = 20.0
        max_freq = SAMPLE_RATE / 2
        bin_hz = SAMPLE_RATE / FFT_SIZE

        bars = []

        for i in range(bar_count):

            f_lo = min_freq * (max_freq / min_freq) ** (i / bar_count)
            f_hi = min_freq * (max_freq / min_freq) ** ((i + 1) / bar_count)

            bin_lo = max(0, int(f_lo / bin_hz))
            bin_hi = max(bin_lo + 1, int(f_hi / bin_hz))
            bin_hi = min(bin_hi, len(magnitudes))

            if bin_lo >= len(magnitudes):
                bars.append(0.0)
                continue

            # Empirical scale (FFT_SIZE/4, from a Hann-windowed
            # full-scale sine's peak-bin magnitude) so a loud tone
            # reads close to a full bar rather than needing per-track
            # calibration; sqrt compresses the range so quieter
            # content still shows some motion instead of sitting near
            # zero.
            peak = max(magnitudes[bin_lo:bin_hi])
            bars.append(math.sqrt(min(peak / (FFT_SIZE / 4), 1.0)))

        return bars

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

    def on_key_pressed(self, controller, keyval, keycode, state):

        if keyval == Gdk.KEY_Escape and self.is_fullscreen():
            self.unfullscreen()
            return True

        return False

    def on_close_request(self, window):

        if self.hide_timer:
            GLib.source_remove(self.hide_timer)
            self.hide_timer = None

        if self.dvd_timer:
            GLib.source_remove(self.dvd_timer)
            self.dvd_timer = None

        if self.pipes_timer:
            GLib.source_remove(self.pipes_timer)
            self.pipes_timer = None

        self.primary.aux_window_closed(self.kind)

        # Same deferred-destroy workaround as MirrorWindow.on_close_request
        # (mirror_window.py) - returning True alone left the window
        # alive there too.
        GLib.idle_add(self.destroy)

        return True
