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
import json
from pathlib import Path
import math
import cmath
import random
import time
from collections import deque

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
gi.require_version("GdkPixbuf", "2.0")

from melange.cursor_hider import FullscreenCursorHider
from gi.repository import Gtk, Adw, Gdk, Gio, GLib, GdkPixbuf, GObject
import cairo

# Remembered between launches, per kind, in
# ~/.config/melange/mini_visualizers.json (see load_saved_settings).
# Everything a setting control changes, plus the 3D views' camera.
SAVED_SETTINGS = [
    "color", "color_style", "line_gradient", "line_color_lo", "line_color_hi", "num_bars", "decay", "show_labels",
    "mirror_reflection", "maintain_aspect_ratio", "scope_zoom",
    "xy_line_width", "xy_points", "xy_curved",
    "vu_style", "vu_peak_hold", "vu_segments",
    "peak_hold_seconds", "peak_hold_color_mode", "peak_hold_color",
    "spectrum_peak_hold", "spectrum_height_scale", "spectrum_style",
    "scope_time_base", "scope_trigger", "vector_persistence", "vector_dot_size",
    "spectrogram_vertical", "spectrogram_speed", "spectrogram_palette",
    "spectrogram_color_lo", "spectrogram_color_hi", "spectrogram_color_ceiling",
    "terrain_palette", "terrain_color_lo", "terrain_color_hi",
    "terrain_color_ceiling", "terrain_zoom", "terrain_show_axes",
    "terrain_azimuth", "terrain_elevation",
    "waterfall_palette", "waterfall_color_lo", "waterfall_color_hi",
    "waterfall_color_ceiling", "waterfall_textured", "waterfall_height_scale",
    "waterfall_zoom", "waterfall_show_axes",
    "waterfall_azimuth", "waterfall_elevation",
    "icon_name", "dvd_speed_scale", "dvd_base_size",
    "dvd_reactivity", "dvd_beats_enabled", "dvd_beat_sensitivity",
    "pipes_max_pipes", "pipes_base_width", "pipes_band_colors",
    "pipes_speed_scale", "pipes_reactivity", "pipes_show_grid",
    "pipes_fade_enabled", "pipes_fade_seconds", "pipes_auto_rotate",
    "pipes_rotate_speed", "pipes_beats_enabled", "pipes_beat_rotation_enabled",
    "pipes_beat_zoom_enabled", "pipes_pulse_width", "pipes_pulse_color",
    "pipes_tube_shading", "pipes_zoom", "pipes_azimuth", "pipes_elevation",
    # Shared (every kind)
    "background_mode", "background_color", "gain", "frame_rate_cap",
    "compact_mode", "freq_min", "freq_max", "xy_goniometer",
    "terrain_auto_rotate", "terrain_rotate_speed",
    "waterfall_auto_rotate", "waterfall_rotate_speed",
    "view_hint_shown", "vector_auto_gain", "color_sync", "reverse_colors",
    "scope_line_width", "peak_scale", "background_opacity", "glass_finish",
]

# Starting camera (azimuth, elevation in degrees) of the 3D kinds -
# Reset View / double-click / Home go back to these.
VIEW_DEFAULTS = {"terrain": (35, 28), "waterfall": (35, 55), "pipes": (35, 28)}
VIEW_KINDS = tuple(VIEW_DEFAULTS)
VIEW_HINT = "Drag to rotate · Scroll to zoom · Double-click to reset"

SAVE_DELAY_MS = 1000


def saved_settings_path():

    return Path(GLib.get_user_config_dir()) / "melange" / "mini_visualizers.json"


def read_saved_settings():

    try:
        data = json.loads(saved_settings_path().read_text())
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def encode_setting(value):

    if isinstance(value, Gdk.RGBA):
        return value.to_string()

    if isinstance(value, list):
        return [encode_setting(item) for item in value]

    return value


def decode_setting(saved, current):
    """Saved JSON value -> the type of the current default, or None if
    it doesn't fit (then the default is kept)."""

    if isinstance(current, Gdk.RGBA):
        color = Gdk.RGBA()
        return color if isinstance(saved, str) and color.parse(saved) else None

    if isinstance(current, list):
        if not isinstance(saved, list) or len(saved) != len(current):
            return None
        items = [decode_setting(a, b) for a, b in zip(saved, current)]
        return None if None in items else items

    if isinstance(current, bool):
        return saved if isinstance(saved, bool) else None

    if isinstance(current, (int, float)):
        if isinstance(saved, bool) or not isinstance(saved, (int, float)):
            return None
        return type(current)(saved)

    if isinstance(current, str):
        return saved if isinstance(saved, str) else None

    return None


# Smallest size a mini visualizer window can be resized to.
AUX_MIN_WIDTH = 100
AUX_MIN_HEIGHT = 80

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
    ("emote-love-symbolic", "Heart"),
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
    #
    # Looked up from a precomputed table rather than calling colorsys
    # each time - this runs per cell/dot (1.4 million calls per 30s in a
    # profile with the aux windows open); 1024 steps is finer than any
    # visible difference.
    level = max(0.0, min(1.0, level))

    return _RAINBOW_LUT[int(level * _RAINBOW_LUT_MAX + 0.5)]


def _rainbow_color_exact(level):

    hue = (1.0 - level) * 0.72
    return colorsys.hsv_to_rgb(hue, 0.9, 0.35 + 0.65 * level)


_RAINBOW_LUT_MAX = 1023
_RAINBOW_LUT = [
    _rainbow_color_exact(i / _RAINBOW_LUT_MAX)
    for i in range(_RAINBOW_LUT_MAX + 1)
]


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
# Named palettes (dark/quiet -> bright/loud), used by every kind's
# Color Mode / Palette setting. Rainbow keeps its own HSV sweep.
PALETTE_STOPS = {
    "fire": ["#000000", "#5c0a00", "#c0300a", "#ff8c1a", "#ffe066", "#ffffff"],
    "ice": ["#000000", "#071a52", "#1f5fbf", "#3ec6ff", "#b8f2ff", "#ffffff"],
    "viridis": ["#440154", "#3b528b", "#21918c", "#5ec962", "#fde725"],
    "magma": ["#000004", "#3b0f70", "#8c2981", "#de4968", "#fe9f6d", "#fcfdbf"],
    "plasma": ["#0d0887", "#6a00a8", "#b12a90", "#e16462", "#fca636", "#f0f921"],
    "ocean": ["#000814", "#003566", "#0077b6", "#00b4d8", "#90e0ef"],
    "sunset": ["#1a0b3d", "#5b1a6e", "#b0306a", "#f5793b", "#fdd85d"],
    "neon": ["#2b0057", "#ff00cc", "#7a00ff", "#00e5ff", "#e0ffff"],
    "forest": ["#03140a", "#0b3d1e", "#2d7a35", "#8cc63f", "#e8f5a3"],
    "matrix": ["#000000", "#003b00", "#008f11", "#00ff41", "#ccffcc"],
    "candy": ["#3d0a3d", "#ff5fa2", "#ffb3d9", "#a0e7ff", "#ffffff"],
    "grayscale": ["#000000", "#ffffff"],
}

PALETTE_LABELS = [
    ("rainbow", "Rainbow"), ("fire", "Fire"), ("ice", "Ice"),
    ("viridis", "Viridis"), ("magma", "Magma"), ("plasma", "Plasma"),
    ("ocean", "Ocean"), ("sunset", "Sunset"), ("neon", "Neon"),
    ("forest", "Forest"), ("matrix", "Matrix"), ("candy", "Candy"),
    ("grayscale", "Grayscale"),
]

_PALETTE_LUTS = {}

# "Main Visualizer" palette: colors sampled from the main window's
# current frame (update_main_visualizer_palette, fed from
# projectm_view.sample_colors twice a second while a mini visualizer is
# open). Neutral until the first sample arrives.
PALETTE_STOPS["main"] = [(0.08, 0.08, 0.12), (0.25, 0.25, 0.35), (0.5, 0.5, 0.65), (0.75, 0.75, 0.88), (1.0, 1.0, 1.0)]
MAIN_PALETTE_VERSION = [0]

# The main frame's most prominent color (vivid and bright, weighted by
# how much of the frame it covers) - what Solid moves toward.
MAIN_DOMINANT = [(0.75, 0.75, 0.88)]


def update_main_visualizer_palette(pixels):
    """RGBA bytes of a small copy of the main visualizer's frame ->
    five colors, dark to bright, eased toward over ~1.5 s."""

    colors = [
        (pixels[i] / 255, pixels[i + 1] / 255, pixels[i + 2] / 255)
        for i in range(0, len(pixels) - 3, 4)
    ]

    def luminance(c):
        return 0.2126 * c[0] + 0.7152 * c[1] + 0.0722 * c[2]

    # Mostly-black presets: judge by what's actually lit.
    lit = [c for c in colors if max(c) > 0.08] or colors
    lit.sort(key=luminance)

    stops = []
    count = len(lit)

    for k in range(5):
        chunk = lit[k * count // 5:(k + 1) * count // 5] or lit[-1:]
        r = sum(c[0] for c in chunk) / len(chunk)
        g = sum(c[1] for c in chunk) / len(chunk)
        b = sum(c[2] for c in chunk) / len(chunk)

        # A little more vivid, and a dark -> bright ladder, so washed-
        # out or very dark presets still give a usable palette.
        h, sat, val = colorsys.rgb_to_hsv(r, g, b)
        sat = min(1.0, sat * 1.3 + 0.05)
        val = max(val, 0.18 + 0.2 * k)
        stops.append(colorsys.hsv_to_rgb(h, sat, min(1.0, val)))

    # Dominant: hue buckets weighted by saturation x brightness.
    buckets = {}
    for c in lit:
        h, sat, val = colorsys.rgb_to_hsv(*c)
        weight = sat * val
        key = int(h * 12) % 12
        total, rs, gs, bs = buckets.get(key, (0.0, 0.0, 0.0, 0.0))
        buckets[key] = (total + weight, rs + c[0] * weight, gs + c[1] * weight, bs + c[2] * weight)

    best = max(buckets.values(), key=lambda bucket: bucket[0], default=None)

    if best is not None and best[0] > 0:
        h, sat, val = colorsys.rgb_to_hsv(best[1] / best[0], best[2] / best[0], best[3] / best[0])
        dominant = colorsys.hsv_to_rgb(h, min(1.0, sat * 1.2 + 0.1), max(val, 0.7))
    else:
        dominant = stops[3]

    old = MAIN_DOMINANT[0]
    MAIN_DOMINANT[0] = tuple(old[c] + (dominant[c] - old[c]) * 0.35 for c in range(3))

    current = PALETTE_STOPS["main"]
    PALETTE_STOPS["main"] = [
        tuple(old[c] + (new[c] - old[c]) * 0.35 for c in range(3))
        for old, new in zip(current, stops)
    ]
    _PALETTE_LUTS.pop("main", None)
    MAIN_PALETTE_VERSION[0] += 1

    update_main_frame(pixels)


# "Frosted (Main Visualizer)" background: the sampled 32x32 frame
# averaged down to 16x16 and box-blurred, eased between samples, kept as
# a small cairo surface that backgrounds scale up with bilinear
# filtering - a soft, blurry copy of the main view. (GNOME gives apps no
# way to blur the desktop behind a window, so this blurs Melange's own
# picture instead.)
FROST_SIZE = 16
MAIN_FRAME = {"pixels": None, "surface": None, "data": None, "average": (0.1, 0.1, 0.12)}


def update_main_frame(pixels):

    size = 32
    if len(pixels) < size * size * 4:
        return

    # glReadPixels rows run bottom-up.
    def texel(x, y):
        o = ((size - 1 - y) * size + x) * 4
        return pixels[o] / 255, pixels[o + 1] / 255, pixels[o + 2] / 255

    small = []
    for y in range(FROST_SIZE):
        for x in range(FROST_SIZE):
            block = [texel(x * 2 + dx, y * 2 + dy) for dx in (0, 1) for dy in (0, 1)]
            small.append(tuple(sum(c[i] for c in block) / 4 for i in range(3)))

    blurred = []
    for y in range(FROST_SIZE):
        for x in range(FROST_SIZE):
            near = [
                small[ny * FROST_SIZE + nx]
                for ny in range(max(0, y - 1), min(FROST_SIZE, y + 2))
                for nx in range(max(0, x - 1), min(FROST_SIZE, x + 2))
            ]
            blurred.append(tuple(sum(c[i] for c in near) / len(near) for i in range(3)))

    old = MAIN_FRAME["pixels"]
    if old is not None:
        blurred = [
            tuple(o[i] + (n[i] - o[i]) * 0.35 for i in range(3))
            for o, n in zip(old, blurred)
        ]

    MAIN_FRAME["pixels"] = blurred
    MAIN_FRAME["average"] = tuple(sum(c[i] for c in blurred) / len(blurred) for i in range(3))

    data = bytearray(FROST_SIZE * FROST_SIZE * 4)
    for index, (r, g, b) in enumerate(blurred):
        data[index * 4:index * 4 + 4] = bytes((int(b * 255), int(g * 255), int(r * 255), 255))

    MAIN_FRAME["data"] = data
    MAIN_FRAME["surface"] = cairo.ImageSurface.create_for_data(
        data, cairo.FORMAT_ARGB32, FROST_SIZE, FROST_SIZE, FROST_SIZE * 4
    )


def _palette_lut(name):

    lut = _PALETTE_LUTS.get(name)

    if lut is None:
        stops = []
        for stop in PALETTE_STOPS[name]:
            if isinstance(stop, tuple):
                stops.append(stop)
                continue
            color = Gdk.RGBA()
            color.parse(stop)
            stops.append((color.red, color.green, color.blue))

        lut = []
        for i in range(256):
            t = i / 255 * (len(stops) - 1)
            k = min(int(t), len(stops) - 2)
            f = t - k
            a, b = stops[k], stops[k + 1]
            lut.append(tuple(a[c] + (b[c] - a[c]) * f for c in range(3)))

        _PALETTE_LUTS[name] = lut

    return lut


def palette_color(name, level, lo=None, hi=None):
    """Color for 0..1 `level` from a named palette; "gradient" blends
    lo -> hi (the user's own two colors)."""

    if name == "gradient" and lo is not None and hi is not None:
        return gradient_color(level, lo, hi)

    if name not in PALETTE_STOPS:
        return rainbow_color(level)

    level = max(0.0, min(1.0, level))

    return _palette_lut(name)[int(level * 255 + 0.5)]


PALETTE_CHOICES = [("gradient", "Custom")] + PALETTE_LABELS

# Same idea as PALETTE_CHOICES, for the kinds that only ever had a
# single solid Color (X-Y Scope, Spectrum, VU Meter's needle style,
# Oscilloscope, Vector Scope) rather than a Low/High gradient pair -
# "Gradient" wouldn't mean anything for those, so this offers "Solid"
# (today's only behavior) against "Rainbow" instead. Backed by one
# shared self.color_mode field, the same way self.color itself is
# already shared generic state across these kinds.
# Colors group "Color Style" (not "Style" - Spectrum and VU already have
# a Style row for their shape): how a kind is colored. Where the colors come
# from (your own vs the main visualizer) is the Match slider.
STYLE_CHOICES = [("solid", "Solid"), ("gradient", "Gradient")]

PEAK_HOLD_COLOR_CHOICES = [("auto", "Automatic"), ("custom", "Custom"), ("main", "Main Visualizer")]
METER_STYLE_CHOICES = [("classic", "Classic")] + STYLE_CHOICES

# Rows that belong in the Colors group, in display order (whichever a
# kind has). Built partly by build_settings_controls, partly by
# build_color_controls; build_settings_page gathers them.
COLOR_ROW_ORDER = [
    "Color Style", "Color", "Gradient", "Low Color", "High Color", "High Level",
    "Reverse", "Bass Color", "Low Mid Color", "High Mid Color", "Treble Color",
    "Pulse Color", "Peak Hold Color", "Match Main Visualizer",
    "Background", "Background Color", "Background Opacity", "Glass Finish",
]

BACKGROUND_CHOICES = [
    ("theme", "Match Theme"), ("black", "Black"), ("custom", "Custom Color"),
    ("frosted", "Frosted (Main Visualizer)"), ("transparent", "Transparent"),
]

# 0 = redraw on every audio chunk (no cap).
FRAME_RATE_CHOICES = [(0, "Unlimited"), (60, "60 fps"), (30, "30 fps"), (20, "20 fps"), (15, "15 fps"), (10, "10 fps")]


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


# Every aux window is handed the very same left/right list objects per
# audio chunk (window.py forward_audio_to_aux_windows), and VU, DVD and
# Pipes each took the RMS of both on every draw - so the last few
# results are remembered by list identity. The list itself is kept in
# the cache entry, so an id can't be reused while it's cached.
_rms_cache = {}


def rms(samples):

    if not samples:
        return 0.0

    cached = _rms_cache.get(id(samples))

    if cached is not None and cached[0] is samples:
        return cached[1]

    value = math.sqrt(sum(s * s for s in samples) / len(samples))

    if len(_rms_cache) >= 8:
        _rms_cache.clear()

    _rms_cache[id(samples)] = (samples, value)

    return value


# Bit-reversal permutation and twiddle factors per FFT length, built
# once - the old recursive FFT recomputed cmath.exp() for every
# butterfly (~14 million calls per 30s with the aux windows open, its
# single biggest cost in a profile).
_FFT_TABLES = {}


def _fft_tables(n):

    tables = _FFT_TABLES.get(n)

    if tables is None:
        bits = n.bit_length() - 1
        bitrev = [int(format(i, f"0{bits}b")[::-1], 2) for i in range(n)]
        twiddles = [cmath.exp(-2j * math.pi * k / n) for k in range(n // 2)]
        tables = _FFT_TABLES[n] = (bitrev, twiddles)

    return tables


def _complex_fft_in_place(a):

    # Iterative radix-2 Cooley-Tukey; `a` must already be in
    # bit-reversed order. Power-of-two lengths only.
    n = len(a)
    _, twiddles = _fft_tables(n)
    size = 2

    while size <= n:

        half = size // 2
        step = n // size

        for k in range(half):

            w = twiddles[k * step]

            for top in range(k, n, size):
                bottom = top + half
                t = w * a[bottom]
                u = a[top]
                a[top] = u + t
                a[bottom] = u - t

        size *= 2


def real_fft_magnitudes(samples):

    # Magnitudes of bins 0..n/2-1 of a real signal's FFT, computed with
    # one n/2-point complex FFT (even samples as the real part, odd as
    # the imaginary part) plus a split step - half the butterflies of
    # a full n-point transform, since the input has no imaginary part
    # anyway. Matches the old full recursive FFT's magnitudes to
    # ~1e-14, ~4x faster.
    n = len(samples)
    m = n // 2
    bitrev, _ = _fft_tables(m)
    _, twiddles_n = _fft_tables(n)

    z = [complex(samples[2 * i], samples[2 * i + 1]) for i in bitrev]
    _complex_fft_in_place(z)

    magnitudes = [0.0] * m

    for k in range(m):
        zk = z[k]
        zc = z[-k % m].conjugate()
        even = (zk + zc) * 0.5
        odd = (zk - zc) * -0.5j
        magnitudes[k] = abs(even + twiddles_n[k] * odd)

    return magnitudes


# Every FFT-based aux window (Spectrum, Spectrogram, Terrain,
# Waterfall, Pipes) keeps its own copy of the same last FFT_SIZE mono
# samples and used to run its own FFT on them - identical work, once
# per open window. This memoizes on the buffer's contents, so however
# many windows are open, each new piece of audio is analysed once.
# The returned list is shared: callers must treat it as read-only.
_magnitude_cache = [None, None]


def spectrum_magnitudes(buffer):

    key = tuple(buffer)

    if key != _magnitude_cache[0]:
        windowed = [s * w for s, w in zip(key, _HANN_WINDOW)]
        _magnitude_cache[0] = key
        _magnitude_cache[1] = real_fft_magnitudes(windowed)

    return _magnitude_cache[1]


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
    # What the drawing code reads: "solid", "classic", or a palette key.
    # Derived from Style + Gradient; the per-frame color swap
    # (apply_effective_colors) overrides it while drawing.
    @property
    def color_mode(self):
        if self._color_mode_override is not None:
            return self._color_mode_override
        if self.color_style in ("solid", "classic"):
            return self.color_style
        return self.line_gradient

    @color_mode.setter
    def color_mode(self, value):
        self._color_mode_override = value

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

        self.save_timer = None
        saved = read_saved_settings().get(kind, {})
        size = saved.get("window_size")

        if (
            isinstance(size, list) and len(size) == 2
            and all(isinstance(n, int) for n in size)
        ):
            self.set_default_size(max(size[0], AUX_MIN_WIDTH), max(size[1], AUX_MIN_HEIGHT))

        self.set_title(AUX_WINDOW_TITLES[kind])

        # libadwaita windows default to a 360x200 minimum; these can be
        # much smaller (e.g. a narrow VU or Peak meter strip).
        self.set_size_request(AUX_MIN_WIDTH, AUX_MIN_HEIGHT)

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
        # Colors: Style (Solid / Gradient / Classic for meters), and the
        # Gradient for line kinds (heatmap kinds have their own
        # *_palette). color_mode is what the drawing code reads - see
        # the property below.
        self._color_mode_override = None
        self.color_style = "classic" if kind in ("vu", "peak") else "solid"
        self.line_gradient = "rainbow"
        self.line_color_lo = Gdk.RGBA()
        self.line_color_lo.parse("#1a3cff")
        self.line_color_hi = Gdk.RGBA()
        self.line_color_hi.parse("#ff3cc8")

        # Shared settings (every kind) - see build_shared_controls.
        self.background_mode = "custom" if kind == "dvd" else "theme"
        self.background_color = Gdk.RGBA()
        self.background_color.parse("#0d0d0d" if kind == "dvd" else "#000000")
        # How solid the background is (Black/Custom at ~40% = tinted
        # glass over the desktop), and an optional glass sheen.
        self.background_opacity = 1.0
        self.glass_finish = False
        self.gain = 1.0
        self.frame_rate_cap = 0
        self.last_draw_queued = 0.0
        self.compact_mode = False

        # Spectrum-type kinds: the frequency span shown (log-spaced).
        self.freq_min = 20.0
        self.freq_max = SAMPLE_RATE / 2

        # X-Y Scope: rotate 45 degrees (mid up, side across) - the
        # standard goniometer view.
        self.xy_goniometer = False

        # Vector Scope: scale the cloud to recent loudness (fast attack,
        # ~2s release) so it neither presses into the edges on loud
        # masters nor shrinks to a dot on quiet ones.
        self.vector_auto_gain = True
        self.vector_envelope = 0.0

        # Oscilloscope trace thickness (px).
        self.scope_line_width = 1.2

        # Peak meter: "db" (-60..0 dBFS, the default) or "linear".
        self.peak_scale = "db"

        # Colors: blend toward the main visualizer's (0 = own colors,
        # 1 = fully matched), and flip palettes/gradients end to end.
        self.color_sync = 0.0
        self.reverse_colors = False
        self.color_signature = None
        self.effective_signatures = {}

        # Terrain/Waterfall can turn slowly on their own, like Pipes.
        self.terrain_auto_rotate = False
        self.terrain_rotate_speed = 6.0
        self.waterfall_auto_rotate = False
        self.waterfall_rotate_speed = 6.0
        self.view_dragging = False
        self.last_auto_rotate_time = None
        self.view_hint_shown = False

        self.settings_rows = {}
        self.row_subtitles = {}
        self.settings_window = None
        self.zoom_scale = None
        self.context_menu = None
        self.xy_line_width = 1.0
        # 0 = All (every sample in the current audio chunk, the
        # original/default behavior) - a positive value subsamples
        # down to that many evenly-spaced points instead, requested as
        # an adjustable "number of lines drawn" control.
        self.xy_points = 0
        # Smooth Catmull-Rom curve through the plotted points instead
        # of a plain straight polyline, requested to look more like a
        # real analog scope's continuously-curving beam trace. On by
        # default (the just-requested look), toggleable back to the
        # original straight-line look on request.
        self.xy_curved = True
        # X-Y Scope/Vector Scope only - GTK4 has no window-level
        # geometry-hint/aspect-ratio API to lock an actual interactive
        # resize (confirmed via introspection: Gdk.Toplevel/Gtk.Window
        # have no such methods, unlike GTK3's old geometry hints), so
        # this is enforced at the *content* level instead - see
        # apply_aspect_letterbox - by confining what gets drawn to a
        # centered square inscribed in the drawing area, regardless of
        # what shape the window itself gets resized to. On by default,
        # matching these two kinds' new square-by-default window size.
        self.maintain_aspect_ratio = True
        # Zooms the plotted trace in/out within the fixed canvas -
        # useful to zoom in on a quiet signal that only uses a small
        # fraction of the square, or zoom out to avoid clipping a hot
        # one. Multiplies the existing auto-computed scale rather than
        # replacing it, so 1.0 (default) looks identical to before
        # this setting existed.
        self.scope_zoom = 1.0

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
        self._dvd_logo_cache = {}
        self._projection_trig = (None, None, 1.0, 0.0, 1.0, 0.0)

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

        # Sidebar toggle at the right end of the title bar, like the
        # main window's (the settings sidebar opens on the right). The
        # sidebar itself is built at the end of __init__: its controls
        # read kind-specific state assigned further down. Bound both
        # ways to show-sidebar there; the sidebar's own header has a
        # matching toggle to close it.
        self.settings_button = Gtk.ToggleButton(
            icon_name="sidebar-show-right-symbolic",
            tooltip_text="Settings"
        )
        self.header.pack_end(self.settings_button)

        # In the header, same placement as MirrorWindow's own
        # fullscreen button (mirror_window.py) - not an overlay button
        # like Settings, so it fades with the header itself rather
        # than needing its own visibility state.
        self.fullscreen_button = Gtk.Button(
            icon_name="view-fullscreen-symbolic",
            tooltip_text="Toggle Fullscreen",
            action_name="win.toggle-fullscreen"
        )

        # Far left, same as the main window: fullscreen is about the
        # content, kept apart from the window buttons.
        self.header.pack_start(self.fullscreen_button)

        self.toolbar_view.set_content(content_overlay)

        # Settings slide in from the right over the visualizer (always
        # the overlay/collapsed form - these windows are small). It
        # replaced a popover that was often taller than the window
        # itself (Pipes: ~860px in a 360px window) and whose
        # click-outside dismissal fought with the dropdowns and color
        # pickers inside it. Clicking the visualizer or Esc closes it.
        self.settings_split = Adw.OverlaySplitView(
            collapsed=True,
            sidebar_position=Gtk.PackType.END,
            min_sidebar_width=100,
            max_sidebar_width=320,
            sidebar_width_fraction=0.6,
            enable_show_gesture=False,
        )
        self.settings_split.set_content(self.toolbar_view)

        self.set_content(self.settings_split)

        # Narrow windows: drop the title text (it's what kept the header
        # 240px wide), then the fullscreen button (F11 still works).
        narrow = Adw.Breakpoint.new(Adw.BreakpointCondition.parse("max-width: 280px"))
        narrow.add_setter(self.header, "show-title", False)
        narrow.add_setter(self.settings_split, "sidebar-width-fraction", 1.0)
        self.add_breakpoint(narrow)

        very_narrow = Adw.Breakpoint.new(Adw.BreakpointCondition.parse("max-width: 150px"))
        very_narrow.add_setter(self.header, "show-title", False)
        very_narrow.add_setter(self.settings_split, "sidebar-width-fraction", 1.0)
        very_narrow.add_setter(self.fullscreen_button, "visible", False)
        self.add_breakpoint(very_narrow)

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
            rotate_gesture.connect("drag-end", self.on_view_drag_end)
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
            rotate_gesture.connect("drag-end", self.on_view_drag_end)
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

        # Same fullscreen action/icon-sync/Escape-to-exit pattern as
        # MirrorWindow (mirror_window.py).
        fullscreen_action = Gio.SimpleAction.new("toggle-fullscreen", None)
        fullscreen_action.connect("activate", self.toggle_fullscreen)
        self.add_action(fullscreen_action)

        self.connect("notify::fullscreened", self.update_fullscreen_button_icon)

        # Pointer disappears after a moment's stillness in fullscreen.
        self.cursor_hider = FullscreenCursorHider(
            self,
            may_hide=lambda: not self.settings_split.get_show_sidebar(),
            on_hide=lambda: self.drawing_area.set_cursor(None),
            on_show=self.update_view_cursor
        )

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
        self.peak_hold_color_mode = "auto"
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
        # Cached image for render_spectrogram - see update_spectrogram_image.
        self.spectrogram_append_count = 0
        self.spectrogram_image = None
        self.spectrogram_image_key = None
        self.spectrogram_image_appends = 0
        self.spectrogram_pixel_table_cache = None
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
                self.tick_interval_ms(), self.dvd_tick
            )

        if self.kind == "pipes":
            self.reset_pipes()
            self.pipes_timer = GLib.timeout_add(
                self.tick_interval_ms(), self.pipes_tick
            )

        # Built here, not up near settings_button's own construction -
        # see the comment there for why (every kind's state needs to
        # exist first, and this is the first point in __init__ where
        # that's guaranteed).
        self.default_settings = {
            name: encode_setting(getattr(self, name))
            for name in SAVED_SETTINGS if hasattr(self, name)
        }
        self.load_saved_settings()

        self.settings_split.set_sidebar(
            self.build_settings_sidebar(self.build_settings_controls())
        )

        self.setup_extra_controls()

        self.settings_split.connect("notify::show-sidebar", self.on_show_sidebar_changed)

        # Size changes are saved too (not while maximized/fullscreen).
        self.connect("notify::default-width", self.schedule_save)

        # X-Y / Vector Scope: Maintain Aspect Ratio also keeps the
        # window square - snapped once a resize settles, since GNOME
        # doesn't let apps constrain the shape while it's being dragged.
        self.square_timer = None
        self.last_square_side = None
        if self.kind in ("xy", "vectorscope"):
            self.connect("notify::default-width", self.schedule_square_snap)
            self.connect("notify::default-height", self.schedule_square_snap)
            self.connect("notify::fullscreened", self.schedule_square_snap)
        self.connect("notify::default-height", self.schedule_save)
        self.settings_button.bind_property(
            "active", self.settings_split, "show-sidebar",
            GObject.BindingFlags.BIDIRECTIONAL | GObject.BindingFlags.SYNC_CREATE
        )

    # Builds every control for this kind as a plain list of
    # [Label, control...] row boxes (or bare buttons);
    # build_settings_sidebar turns those into libadwaita rows.
    def build_settings_controls(self):

        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        box.set_margin_start(10)
        box.set_margin_end(10)
        box.set_margin_top(10)
        box.set_margin_bottom(10)

        if self.kind in (
            "xy", "spectrum", "vu", "peak", "oscilloscope", "vectorscope"
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
                label="Gradient", xalign=0, hexpand=True
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

            # Automatic follows the background (near-white/near-black,
            # like the other structural marks); Main Visualizer uses
            # its brightest color.
            hold_keys = [key for key, _ in PEAK_HOLD_COLOR_CHOICES]
            hold_mode_dropdown = Gtk.DropDown.new_from_strings(
                [label for _, label in PEAK_HOLD_COLOR_CHOICES]
            )
            hold_mode_dropdown.set_valign(Gtk.Align.CENTER)
            hold_mode_dropdown.set_selected(
                hold_keys.index(self.peak_hold_color_mode)
                if self.peak_hold_color_mode in hold_keys else 0
            )

            def hold_mode_changed(dropdown, param):
                index = dropdown.get_selected()
                if 0 <= index < len(hold_keys):
                    self.peak_hold_color_mode = hold_keys[index]

            hold_mode_dropdown.connect("notify::selected", hold_mode_changed)
            hold_color_row.append(hold_mode_dropdown)

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

        if self.kind in ("xy", "vectorscope"):

            aspect_row = Gtk.Box(
                orientation=Gtk.Orientation.HORIZONTAL, spacing=8
            )

            aspect_label = Gtk.Label(
                label="Maintain Aspect Ratio", xalign=0, hexpand=True
            )
            aspect_row.append(aspect_label)

            aspect_switch = Gtk.Switch()
            aspect_switch.set_active(self.maintain_aspect_ratio)
            aspect_switch.set_valign(Gtk.Align.CENTER)

            aspect_switch.connect(
                "notify::active",
                self.on_maintain_aspect_ratio_changed
            )

            aspect_row.append(aspect_switch)
            box.append(aspect_row)

            zoom_row = Gtk.Box(
                orientation=Gtk.Orientation.HORIZONTAL, spacing=8
            )

            zoom_label = Gtk.Label(
                label="Zoom", xalign=0, hexpand=True
            )
            zoom_row.append(zoom_label)

            self.scope_zoom_value_label = Gtk.Label(
                label=self.format_scope_zoom(self.scope_zoom)
            )

            zoom_scale = Gtk.Scale.new_with_range(
                Gtk.Orientation.HORIZONTAL, 0.25, 3.0, 0.05
            )
            zoom_scale.set_value(self.scope_zoom)
            zoom_scale.set_size_request(120, -1)
            zoom_scale.set_draw_value(False)

            zoom_scale.connect(
                "value-changed",
                self.on_scope_zoom_changed
            )

            zoom_row.append(self.scope_zoom_value_label)
            zoom_row.append(zoom_scale)
            box.append(zoom_row)

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

            palette_label = Gtk.Label(label="Gradient", xalign=0, hexpand=True)
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
                label="Gradient", xalign=0, hexpand=True
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

            xy_points_row = Gtk.Box(
                orientation=Gtk.Orientation.HORIZONTAL, spacing=8
            )

            xy_points_label = Gtk.Label(
                label="Points", xalign=0, hexpand=True
            )
            xy_points_row.append(xy_points_label)

            self.xy_points_value_label = Gtk.Label(label=self.format_xy_points(self.xy_points))

            xy_points_scale = Gtk.Scale.new_with_range(
                Gtk.Orientation.HORIZONTAL, 0.0, 200.0, 4.0
            )
            xy_points_scale.set_value(self.xy_points)
            xy_points_scale.set_size_request(120, -1)
            xy_points_scale.set_draw_value(False)

            xy_points_scale.connect(
                "value-changed",
                self.on_xy_points_changed
            )

            xy_points_row.append(self.xy_points_value_label)
            xy_points_row.append(xy_points_scale)
            box.append(xy_points_row)

            xy_curved_row = Gtk.Box(
                orientation=Gtk.Orientation.HORIZONTAL, spacing=8
            )

            xy_curved_label = Gtk.Label(
                label="Curved Lines", xalign=0, hexpand=True
            )
            xy_curved_row.append(xy_curved_label)

            xy_curved_switch = Gtk.Switch()
            xy_curved_switch.set_active(self.xy_curved)
            xy_curved_switch.set_valign(Gtk.Align.CENTER)

            xy_curved_switch.connect(
                "notify::active",
                self.on_xy_curved_changed
            )

            xy_curved_row.append(xy_curved_switch)
            box.append(xy_curved_row)

        self.build_extra_controls(box)
        self.build_shared_controls(box)

        return box

    # --- small row builders (rows of [Label, control]) ---------------------

    def control_row(self, box, title, widget, subtitle=None):

        row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        row.append(Gtk.Label(label=title, xalign=0, hexpand=True))
        widget.set_valign(Gtk.Align.CENTER)
        row.append(widget)
        box.append(row)

        if subtitle:
            self.row_subtitles[title] = subtitle

    def group_break(self, box, title):
        """Starts a new titled group in the settings sidebar."""

        separator = Gtk.Separator()
        separator.set_name("group:" + title)
        box.append(separator)

    def add_switch(self, box, title, attr, on_change=None, subtitle=None):

        switch = Gtk.Switch(active=getattr(self, attr))

        def changed(widget, param):
            setattr(self, attr, widget.get_active())
            if on_change:
                on_change()

        switch.connect("notify::active", changed)
        self.control_row(box, title, switch, subtitle)

    def add_scale(self, box, title, attr, lower, upper, step, fmt, on_change=None):

        scale = Gtk.Scale.new_with_range(Gtk.Orientation.HORIZONTAL, lower, upper, step)
        scale.set_value(getattr(self, attr))
        scale.set_draw_value(True)
        scale.set_value_pos(Gtk.PositionType.RIGHT)
        scale.set_format_value_func(lambda widget, value: fmt(value))
        scale.set_size_request(130, -1)
        cast = type(getattr(self, attr))

        def changed(widget):
            setattr(self, attr, cast(widget.get_value()))
            if on_change:
                on_change()

        scale.connect("value-changed", changed)
        self.control_row(box, title, scale)

        return scale

    def add_choice(self, box, title, attr, choices, on_change=None, subtitle=None):

        dropdown = Gtk.DropDown.new_from_strings([label for _, label in choices])
        keys = [key for key, _ in choices]
        current = getattr(self, attr)
        dropdown.set_selected(keys.index(current) if current in keys else 0)

        def changed(widget, param):
            index = widget.get_selected()
            if 0 <= index < len(keys):
                setattr(self, attr, keys[index])
                if on_change:
                    on_change()

        dropdown.connect("notify::selected", changed)
        self.control_row(box, title, dropdown, subtitle)

    def add_color(self, box, title, attr, on_change=None):

        button = Gtk.ColorDialogButton(dialog=Gtk.ColorDialog())
        button.set_rgba(getattr(self, attr))

        def changed(widget, param):
            setattr(self, attr, widget.get_rgba())
            if on_change:
                on_change()

        button.connect("notify::rgba", changed)
        self.control_row(box, title, button)

    # --- extra per-kind settings ---------------------------------------------

    def build_extra_controls(self, box):

        def hertz(value):
            return f"{value / 1000:.1f} kHz" if value >= 1000 else f"{value:.0f} Hz"

        if self.kind in ("spectrum", "spectrogram", "terrain", "waterfall"):
            self.add_scale(box, "Lowest Frequency", "freq_min", 20, 1000, 10, hertz, self.frequency_range_changed)
            self.add_scale(box, "Highest Frequency", "freq_max", 1000, SAMPLE_RATE / 2, 50, hertz, self.frequency_range_changed)

        if self.kind in ("xy", "vectorscope"):
            self.row_subtitles["Maintain Aspect Ratio"] = "Keep the plot and the window square"

        if self.kind == "peak":
            self.add_choice(
                box, "Scale", "peak_scale", [("db", "Decibels"), ("linear", "Linear")],
                subtitle="Decibels spread out quiet levels; Linear is raw amplitude"
            )

        if self.kind == "oscilloscope":
            self.add_scale(
                box, "Line Width", "scope_line_width", 0.5, 6.0, 0.1,
                lambda value: f"{value:.1f}px"
            )

        if self.kind == "vectorscope":
            self.add_switch(
                box, "Auto Gain", "vector_auto_gain",
                subtitle="Keep the cloud a steady size whatever the volume"
            )

        if self.kind == "xy":
            self.add_switch(
                box, "Goniometer View", "xy_goniometer",
                subtitle="Rotate 45°: mono is vertical, stereo width spreads sideways"
            )

        if self.kind in ("terrain", "waterfall"):
            self.add_switch(box, "Auto-Rotate", f"{self.kind}_auto_rotate")
            self.add_scale(
                box, "Rotate Speed", f"{self.kind}_rotate_speed", 1, 30, 1,
                lambda value: f"{value:.0f}°/s"
            )

    def build_color_controls(self, box):
        """Rows for the Colors group (build_settings_page moves the
        kind's own color rows - Color, Gradient, Peak Hold Color, Pipes
        band colors... - in beside these)."""

        line_kind = self.kind in ("xy", "spectrum", "vu", "peak", "oscilloscope", "vectorscope")

        if line_kind:
            choices = METER_STYLE_CHOICES if self.kind in ("vu", "peak") else STYLE_CHOICES
            self.add_choice(box, "Color Style", "color_style", choices)
            self.add_choice(box, "Gradient", "line_gradient", PALETTE_CHOICES)
            self.add_color(box, "Low Color", "line_color_lo")
            self.add_color(box, "High Color", "line_color_hi")

        if self.kind not in ("dvd", "pipes"):
            self.add_switch(box, "Reverse", "reverse_colors")

        self.add_scale(
            box, "Match Main Visualizer", "color_sync", 0.0, 1.0, 0.05,
            lambda value: "Off" if value <= 0 else f"{value * 100:.0f}%"
        )
        self.row_subtitles["Match Main Visualizer"] = (
            "100% uses its colors"
        )

        self.add_choice(box, "Background", "background_mode", BACKGROUND_CHOICES, self.background_changed)
        self.add_color(box, "Background Color", "background_color", self.background_changed)
        self.add_scale(
            box, "Background Opacity", "background_opacity", 0.05, 1.0, 0.05,
            lambda value: f"{value * 100:.0f}%", self.background_changed
        )
        self.add_switch(box, "Glass Finish", "glass_finish")
        self.row_subtitles["Glass Finish"] = "A soft highlight and edge, like a pane of glass"

    def build_shared_controls(self, box):

        self.build_color_controls(box)

        self.group_break(box, "Window")

        self.add_scale(box, "Gain", "gain", 0.25, 4.0, 0.05, lambda value: f"×{value:.2f}")
        self.add_choice(
            box, "Frame Rate", "frame_rate_cap", FRAME_RATE_CHOICES, self.frame_rate_changed,
            subtitle="Lower uses less CPU"
        )
        self.add_switch(
            box, "Compact Mode", "compact_mode", self.apply_compact_mode,
            subtitle="No title bar - right-click for the menu, drag to move"
        )

        reset_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
        reset_button = Gtk.Button(label="Reset to Defaults")
        reset_button.add_css_class("destructive-action")
        reset_button.connect("clicked", lambda button: self.reset_to_defaults())
        reset_row.append(reset_button)
        box.append(reset_row)

    def build_settings_sidebar(self, controls):

        page = self.build_settings_page(controls)

        header = Adw.HeaderBar(show_start_title_buttons=False)

        close_toggle = Gtk.ToggleButton(
            icon_name="sidebar-show-right-symbolic",
            tooltip_text="Settings",
        )
        close_toggle.bind_property(
            "active", self.settings_split, "show-sidebar",
            GObject.BindingFlags.BIDIRECTIONAL | GObject.BindingFlags.SYNC_CREATE
        )
        header.pack_start(close_toggle)
        header.set_title_widget(
            Adw.WindowTitle(title="Settings", subtitle=AUX_WINDOW_TITLES[self.kind])
        )

        sidebar = Adw.ToolbarView()
        sidebar.add_top_bar(header)
        sidebar.set_content(page)

        # Scrolls sideways rather than holding the window open (when
        # it's narrow the settings open in their own window instead -
        # see on_show_sidebar_changed).
        scroller = Gtk.ScrolledWindow(
            hscrollbar_policy=Gtk.PolicyType.AUTOMATIC,
            vscrollbar_policy=Gtk.PolicyType.EXTERNAL,
            child=sidebar,
        )

        return scroller

    # Settings as libadwaita rows: the sidebar's content, or the
    # separate settings window's (open_settings_window).
    def build_settings_page(self, controls):

        def children(widget):
            out = []
            child = widget.get_first_child()
            while child is not None:
                out.append(child)
                child = child.get_next_sibling()
            return out

        page = Adw.PreferencesPage()

        # All color rows go to one Colors group at the top (in
        # COLOR_ROW_ORDER), whichever part of the builder made them.
        colors_group = Adw.PreferencesGroup(title="Colors")
        color_rows = []
        page.add(colors_group)

        group = Adw.PreferencesGroup(title=AUX_WINDOW_TITLES[self.kind])

        if self.kind in VIEW_KINDS:
            group.set_description(VIEW_HINT + " · Arrow keys and +/− work too")

        page.add(group)
        buttons = []
        self.settings_rows = {}

        for row_box in children(controls):
            controls.remove(row_box)

            if isinstance(row_box, Gtk.Separator):
                group = Adw.PreferencesGroup(title=row_box.get_name().removeprefix("group:"))
                page.add(group)
                continue

            parts = children(row_box)

            for part in parts:
                row_box.remove(part)

            if parts and isinstance(parts[0], Gtk.Label):
                row = Adw.ActionRow(title=parts[0].get_label())
                self.settings_rows[row.get_title()] = row

                if row.get_title() in self.row_subtitles:
                    row.set_subtitle(self.row_subtitles[row.get_title()])

                for part in parts[1:]:
                    part.set_valign(Gtk.Align.CENTER)

                    if isinstance(part, Gtk.Scale):
                        part.set_hexpand(False)
                        part.set_size_request(130, -1)

                    row.add_suffix(part)

                if len(parts) == 2 and isinstance(parts[1], Gtk.Switch):
                    row.set_activatable_widget(parts[1])

                if row.get_title() in COLOR_ROW_ORDER:
                    color_rows.append(row)
                else:
                    group.add(row)
            else:
                buttons += [part for part in parts if isinstance(part, Gtk.Button)]

        color_rows.sort(key=lambda row: COLOR_ROW_ORDER.index(row.get_title()))

        for row in color_rows:
            colors_group.add(row)

        if buttons:
            button_box = Gtk.Box(
                orientation=Gtk.Orientation.VERTICAL, spacing=6, margin_top=6
            )

            for button in buttons:
                button.add_css_class("pill")
                button_box.append(button)

            button_group = Adw.PreferencesGroup()
            button_group.add(button_box)
            page.add(button_group)

        # Several settings only took effect on the next audio chunk
        # (nothing redraws while it's quiet or paused); redraw on every
        # change so it's visible straight away.
        for widget in self.iter_widgets(page):
            if isinstance(widget, Gtk.Switch):
                widget.connect_after("notify::active", self.settings_changed)
            elif isinstance(widget, (Gtk.Scale, Gtk.SpinButton)):
                widget.connect_after("value-changed", self.settings_changed)
            elif isinstance(widget, Gtk.DropDown):
                widget.connect_after("notify::selected", self.settings_changed)
            elif isinstance(widget, Gtk.ColorDialogButton):
                widget.connect_after("notify::rgba", self.settings_changed)

        self.zoom_scale = None
        zoom_row = self.settings_rows.get("Zoom")

        if zoom_row is not None and self.kind in VIEW_KINDS:
            self.zoom_scale = next(
                (w for w in self.iter_widgets(zoom_row) if isinstance(w, Gtk.Scale)), None
            )

        return page

    def iter_widgets(self, widget):

        yield widget
        child = widget.get_first_child()

        while child is not None:
            yield from self.iter_widgets(child)
            child = child.get_next_sibling()

    def settings_changed(self, *args):

        self.terrain_dirty = True
        self.waterfall_dirty = True
        self.vector_surface = None
        self.spectrogram_image_key = None
        self.update_row_sensitivity()
        self.drawing_area.queue_draw()
        self.schedule_save()

    def update_row_sensitivity(self):
        """Show only the rows that apply to the current choices."""

        rows = self.settings_rows

        def visible(title, value):
            if title in rows:
                rows[title].set_visible(value)

        palette = getattr(self, f"{self.kind}_palette", None)

        if palette is not None:
            gradient_shown, gradient = True, palette
        else:
            gradient_shown, gradient = self.color_style == "gradient", self.line_gradient

        needle = self.kind == "vu" and self.vu_style == "needle"

        visible("Color", self.color_style == "solid" or (self.color_style == "classic" and needle))
        visible("Gradient", gradient_shown)
        visible("Low Color", gradient_shown and gradient == "gradient")
        visible("High Color", gradient_shown and gradient == "gradient")
        visible("Reverse", gradient_shown)
        visible("Background Color", self.background_mode == "custom")

        if "Peak Hold Color" in rows:
            for widget in self.iter_widgets(rows["Peak Hold Color"]):
                if isinstance(widget, Gtk.ColorDialogButton):
                    widget.set_visible(self.peak_hold_color_mode == "custom")
        visible("Background Opacity", self.background_mode != "transparent")

        if "Rotate Speed" in rows:
            rows["Rotate Speed"].set_sensitive(getattr(self, f"{self.kind}_auto_rotate", True))

    def tick_interval_ms(self):

        fps = self.frame_rate_cap or 60

        return max(16, int(1000 / fps))

    def frame_rate_changed(self):

        if self.dvd_timer:
            GLib.source_remove(self.dvd_timer)
            self.dvd_timer = GLib.timeout_add(self.tick_interval_ms(), self.dvd_tick)

        if self.pipes_timer:
            GLib.source_remove(self.pipes_timer)
            self.pipes_timer = GLib.timeout_add(self.tick_interval_ms(), self.pipes_tick)

    def background_changed(self):

        # Transparent needs the window itself to stop painting the theme
        # background (same class as Transparency Mode, style.css).
        if self.background_mode == "transparent" or self.background_opacity < 0.999:
            self.add_css_class("transparency-active")
        else:
            self.remove_css_class("transparency-active")

    def frequency_range_changed(self):

        self.freq_max = max(self.freq_max, self.freq_min * 2)
        self.terrain_rows.clear()
        self.waterfall_rows.clear()
        self.spectrogram_columns.clear()

    def apply_compact_mode(self):

        # Keep the right-click menu's checkmark in step with the switch.
        action = getattr(self, "compact_action", None)
        if action is not None and action.get_state().get_boolean() != self.compact_mode:
            action.set_state(GLib.Variant("b", self.compact_mode))

        handle = getattr(self, "move_handle", None)
        if handle is not None:
            handle.set_visible(self.compact_mode and not self.is_fullscreen())

        if self.compact_mode:
            if self.hide_timer:
                GLib.source_remove(self.hide_timer)
                self.hide_timer = None
            self.toolbar_view.set_reveal_top_bars(False)
        else:
            self.toolbar_view.set_reveal_top_bars(True)

    def reset_to_defaults(self):

        for name, value in self.default_settings.items():
            decoded = decode_setting(value, getattr(self, name))
            if decoded is not None:
                setattr(self, name, decoded)

        self._icon_cache_key = None
        if self.settings_window is not None:
            # Reopen it so its controls show the defaults.
            settings_window, self.settings_window = self.settings_window, None
            settings_window.destroy()
            self.reset_to_current_controls()
            self.open_settings_window()
        else:
            self.reset_to_current_controls()
        self.background_changed()
        self.frame_rate_changed()
        self.apply_compact_mode()
        self.frequency_range_changed()
        self.settings_changed()

    # --- 3D views ------------------------------------------------------------

    def setup_extra_controls(self):

        # Compact Mode has no title bar, and the 3D kinds use left-drag
        # to rotate - so a grip along the top edge moves the window
        # (Gtk.WindowHandle: the same native move as a title bar).
        self.move_handle = Gtk.WindowHandle(valign=Gtk.Align.START)
        self.move_handle.add_css_class("compact-handle")
        self.move_handle.set_size_request(-1, 22)
        self.move_handle.set_tooltip_text("Drag to move")
        self.move_handle.set_cursor_from_name("grab")
        grip = Gtk.Box(halign=Gtk.Align.CENTER, valign=Gtk.Align.CENTER)
        grip.add_css_class("compact-grip")
        self.move_handle.set_child(grip)
        self.toolbar_view.get_content().add_overlay(self.move_handle)
        self.connect("notify::fullscreened", lambda *args: self.apply_compact_mode())

        self.background_changed()
        self.apply_compact_mode()
        self.update_row_sensitivity()

        # Right-click menu (the way in when Compact Mode hides the title
        # bar), and middle-drag moves the window for every kind - the 3D
        # ones use left-drag to rotate.
        menu_click = Gtk.GestureClick(button=Gdk.BUTTON_SECONDARY)
        menu_click.connect("pressed", self.on_context_menu_pressed)
        self.drawing_area.add_controller(menu_click)

        move_drag = Gtk.GestureDrag(button=Gdk.BUTTON_MIDDLE)
        move_drag.connect("drag-begin", self.on_drag_begin)
        self.drawing_area.add_controller(move_drag)

        show_settings = Gio.SimpleAction.new("show-settings", None)
        show_settings.connect(
            "activate", lambda *args: self.settings_split.set_show_sidebar(True)
        )  # on_show_sidebar_changed redirects to a window when narrow
        self.add_action(show_settings)

        compact = Gio.SimpleAction.new_stateful(
            "compact-mode", None, GLib.Variant("b", self.compact_mode)
        )

        def compact_changed(action, value):
            action.set_state(value)
            self.compact_mode = value.get_boolean()
            self.reset_to_current_controls()
            self.apply_compact_mode()
            self.schedule_save()

        compact.connect("change-state", compact_changed)
        self.add_action(compact)
        self.compact_action = compact

        reset_view = Gio.SimpleAction.new("reset-view", None)
        reset_view.connect("activate", lambda *args: self.reset_view())
        self.add_action(reset_view)

        if self.kind not in VIEW_KINDS:
            return

        double_click = Gtk.GestureClick(button=Gdk.BUTTON_PRIMARY)
        double_click.connect(
            "pressed", lambda gesture, n, x, y: self.reset_view() if n == 2 else None
        )
        self.drawing_area.add_controller(double_click)
        self.update_view_cursor()

        # How to move the view, shown once per kind over the canvas.
        if not self.view_hint_shown:
            hint = Gtk.Label(label=VIEW_HINT, wrap=True, justify=Gtk.Justification.CENTER)
            hint.add_css_class("osd")
            hint.add_css_class("view-hint")
            hint.set_halign(Gtk.Align.CENTER)
            hint.set_valign(Gtk.Align.END)
            hint.set_margin_bottom(16)
            hint.set_margin_start(12)
            hint.set_margin_end(12)
            hint.set_can_target(False)
            self.toolbar_view.get_content().add_overlay(hint)
            self.view_hint_shown = True
            self.schedule_save()

            def remove_hint():
                hint.set_visible(False)
                return False

            GLib.timeout_add_seconds(6, remove_hint)

    # Below this the sidebar would only show a sliver of each row, so
    # settings open in their own small window beside the visualizer.
    SETTINGS_WINDOW_BELOW = (300, 220)

    def on_show_sidebar_changed(self, split, param):

        if not split.get_show_sidebar():
            return

        min_width, min_height = self.SETTINGS_WINDOW_BELOW

        if self.get_width() >= min_width and self.get_height() >= min_height:
            return

        # Undo it once the toggle <-> sidebar binding has finished
        # propagating; doing it inside this notify left the toggle
        # stuck "on", so the next click did nothing.
        GLib.idle_add(self.redirect_to_settings_window)

    def redirect_to_settings_window(self):

        self.settings_split.set_show_sidebar(False)

        # The toggle/menu work as open/close for the window too.
        if self.settings_window is not None:
            self.settings_window.close()
        else:
            self.open_settings_window()

        return False

    def open_settings_window(self):

        page = self.build_settings_page(self.build_settings_controls())
        self.update_row_sensitivity()

        window = Adw.Window(
            title=f"{AUX_WINDOW_TITLES[self.kind]} Settings",
            transient_for=self,
            default_width=340,
            default_height=480,
        )
        window.set_size_request(260, 200)

        view = Adw.ToolbarView()
        view.add_top_bar(Adw.HeaderBar())
        view.set_content(page)
        window.set_content(view)

        keys = Gtk.EventControllerKey()
        keys.connect(
            "key-pressed",
            lambda controller, keyval, *args: (window.close() or True) if keyval == Gdk.KEY_Escape else False
        )
        window.add_controller(keys)

        window.connect("close-request", self.on_settings_window_closed)
        self.settings_window = window
        window.present()

    def on_settings_window_closed(self, window):

        self.settings_window = None
        # The sidebar's controls take over again, showing what changed.
        self.reset_to_current_controls()

        return False

    def reset_to_current_controls(self):
        """Rebuild the sidebar so its controls show the current values
        (after a change made somewhere else, e.g. the right-click menu)."""

        if self.settings_window is not None:
            self.settings_window.get_content().set_content(
                self.build_settings_page(self.build_settings_controls())
            )
            self.update_row_sensitivity()
            return

        showing = self.settings_split.get_show_sidebar()
        self.settings_split.set_sidebar(
            self.build_settings_sidebar(self.build_settings_controls())
        )
        self.settings_split.set_show_sidebar(showing)
        self.update_row_sensitivity()

    def on_context_menu_pressed(self, gesture, n_press, x, y):

        menu = Gio.Menu()
        menu.append("Settings", "win.show-settings")
        menu.append("Compact Mode", "win.compact-mode")
        menu.append("Fullscreen", "win.toggle-fullscreen")

        if self.kind in VIEW_KINDS:
            menu.append("Reset View", "win.reset-view")

        menu.append("Close", "window.close")

        if self.context_menu is not None:
            self.context_menu.unparent()

        self.context_menu = Gtk.PopoverMenu.new_from_model(menu)
        self.context_menu.set_parent(self.drawing_area)
        self.context_menu.set_has_arrow(False)
        rect = Gdk.Rectangle()
        rect.x, rect.y, rect.width, rect.height = int(x), int(y), 1, 1
        self.context_menu.set_pointing_to(rect)
        self.context_menu.popup()

    def update_view_cursor(self):

        if self.kind in VIEW_KINDS:
            self.drawing_area.set_cursor_from_name(
                "grabbing" if self.view_dragging else "grab"
            )

    def on_view_drag_begin(self):

        self.view_dragging = True
        self.update_view_cursor()

    def on_view_drag_end(self, *args):

        self.view_dragging = False
        self.last_auto_rotate_time = None
        self.update_view_cursor()
        self.schedule_save()

    def view_changed(self):

        self.terrain_dirty = True
        self.waterfall_dirty = True

        if self.zoom_scale is not None:
            zoom = getattr(self, f"{self.kind}_zoom")
            if abs(self.zoom_scale.get_value() - zoom) > 1e-6:
                self.zoom_scale.set_value(zoom)

        self.drawing_area.queue_draw()
        self.schedule_save()

    def zoom_view(self, factor):

        if self.kind not in VIEW_KINDS:
            return

        name = f"{self.kind}_zoom"
        setattr(self, name, max(0.4, min(3.0, getattr(self, name) * factor)))
        self.view_changed()

    def rotate_view(self, d_azimuth, d_elevation):

        if self.kind not in VIEW_KINDS:
            return

        low = math.radians(10 if self.kind == "waterfall" else -10)
        setattr(self, f"{self.kind}_azimuth", getattr(self, f"{self.kind}_azimuth") + d_azimuth)
        setattr(
            self, f"{self.kind}_elevation",
            max(low, min(math.radians(85), getattr(self, f"{self.kind}_elevation") + d_elevation))
        )
        self.view_changed()

    def reset_view(self):

        if self.kind not in VIEW_KINDS:
            return

        azimuth, elevation = VIEW_DEFAULTS[self.kind]
        setattr(self, f"{self.kind}_azimuth", math.radians(azimuth))
        setattr(self, f"{self.kind}_elevation", math.radians(elevation))
        setattr(self, f"{self.kind}_zoom", 1.0)
        self.view_changed()

    def auto_rotate_step(self):

        if not getattr(self, f"{self.kind}_auto_rotate", False) or self.view_dragging:
            self.last_auto_rotate_time = None
            return

        now = time.monotonic()

        if self.last_auto_rotate_time is not None:
            dt = min(0.1, now - self.last_auto_rotate_time)
            name = f"{self.kind}_azimuth"
            speed = getattr(self, f"{self.kind}_rotate_speed")
            setattr(self, name, getattr(self, name) + math.radians(speed * dt))
            self.terrain_dirty = True
            self.waterfall_dirty = True

        self.last_auto_rotate_time = now

    # --- remembered settings ---------------------------------------------

    def load_saved_settings(self):

        saved = read_saved_settings().get(self.kind, {})
        valid_icons = {key for key, _ in DVD_ICON_CHOICES}

        for name in SAVED_SETTINGS:
            if name not in saved or not hasattr(self, name):
                continue

            value = decode_setting(saved[name], getattr(self, name))

            if value is None:
                continue

            if name == "icon_name" and value not in valid_icons:
                continue

            setattr(self, name, value)

        self._icon_cache_key = None

        # Before Style + Gradient + the Match slider, one "Color Mode"
        # held all of it (and heatmap Palettes offered Main Visualizer).
        old_mode = saved.get("color_mode")
        if isinstance(old_mode, str) and "color_style" not in saved:
            if old_mode in ("solid", "classic"):
                self.color_style = old_mode
            elif old_mode == "main-solid":
                self.color_style, self.color_sync = "solid", 1.0
            elif old_mode == "main":
                self.color_style, self.color_sync = "gradient", 1.0
            elif old_mode in PALETTE_STOPS or old_mode == "rainbow":
                self.color_style, self.line_gradient = "gradient", old_mode

        palette_name = f"{self.kind}_palette"
        if saved.get(palette_name) == "main":
            setattr(self, palette_name, "rainbow")
            self.color_sync = 1.0

        # Peak Hold Color used to be an on/off "custom" switch.
        if saved.get("peak_hold_color_custom") is True and "peak_hold_color_mode" not in saved:
            self.peak_hold_color_mode = "custom"

        # DVD Bounce had its own Background Color before the shared one.
        if self.kind == "dvd" and "background_mode" not in saved and "dvd_bg_color" in saved:
            color = decode_setting(saved["dvd_bg_color"], self.background_color)
            if color is not None:
                self.background_mode = "custom"
                self.background_color = color

    def schedule_save(self, *args):

        if self.save_timer is not None:
            GLib.source_remove(self.save_timer)

        self.save_timer = GLib.timeout_add(SAVE_DELAY_MS, self.save_settings)

    def save_settings(self):

        if self.save_timer is not None:
            GLib.source_remove(self.save_timer)
            self.save_timer = None

        data = read_saved_settings()
        entry = data.get(self.kind, {}) if isinstance(data.get(self.kind), dict) else {}

        for name in SAVED_SETTINGS:
            if hasattr(self, name):
                entry[name] = encode_setting(getattr(self, name))

        if not (self.is_maximized() or self.is_fullscreen()):
            width, height = self.get_default_size()
            if width > 0 and height > 0:
                entry["window_size"] = [width, height]

        data[self.kind] = entry

        try:
            path = saved_settings_path()
            path.parent.mkdir(parents=True, exist_ok=True)
            GLib.file_set_contents(str(path), json.dumps(data, indent=1).encode())
        except (OSError, GLib.Error) as error:
            print(f"Couldn't save mini visualizer settings: {error}")

        return False

    def on_color_changed(self, button, param):

        self.color = button.get_rgba()
        self.terrain_dirty = True
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

        self.reset_view()

    def on_pipes_scroll(self, controller, dx, dy):

        # Proportional steps feel even at any zoom level.
        self.zoom_view(1.1 ** -dy)

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

    def format_xy_points(self, value):

        return "All" if value <= 0 else str(int(value))

    def on_xy_points_changed(self, scale):

        self.xy_points = int(scale.get_value())
        self.xy_points_value_label.set_label(self.format_xy_points(self.xy_points))
        self.drawing_area.queue_draw()

    def on_xy_curved_changed(self, switch, param):

        self.xy_curved = switch.get_active()
        self.drawing_area.queue_draw()

    def on_maintain_aspect_ratio_changed(self, switch, param):

        self.maintain_aspect_ratio = switch.get_active()
        self.drawing_area.queue_draw()
        self.schedule_square_snap()

    SQUARE_SNAP_DELAY_MS = 400

    def schedule_square_snap(self, *args):

        if self.kind not in ("xy", "vectorscope"):
            return

        if self.square_timer is not None:
            GLib.source_remove(self.square_timer)

        self.square_timer = GLib.timeout_add(self.SQUARE_SNAP_DELAY_MS, self.snap_square)

    def snap_square(self):

        self.square_timer = None

        if not self.maintain_aspect_ratio or self.is_maximized() or self.is_fullscreen():
            return False

        surface = self.get_surface()
        if surface is not None and isinstance(surface, Gdk.Toplevel):
            tiled = Gdk.ToplevelState.TILED
            if surface.get_state() & tiled:
                return False

        width, height = self.get_default_size()

        if width <= 0 or height <= 0 or abs(width - height) <= 2:
            self.last_square_side = max(width, height)
            return False

        # Follow whichever side the user changed most.
        last = self.last_square_side or min(width, height)
        side = width if abs(width - last) >= abs(height - last) else height
        side = max(side, AUX_MIN_WIDTH, AUX_MIN_HEIGHT)

        self.last_square_side = side
        self.set_default_size(side, side)

        return False

    def format_scope_zoom(self, value):

        return f"{value:.2f}x"

    def on_scope_zoom_changed(self, scale):

        self.scope_zoom = scale.get_value()
        self.scope_zoom_value_label.set_label(self.format_scope_zoom(self.scope_zoom))
        self.drawing_area.queue_draw()

    # Always paints the real background across the *full* canvas
    # first (so the caller never needs its own separate background
    # paint), then - only when Equal Aspect Ratio is on - confines the
    # rest of drawing to a centered square inscribed in whatever
    # rectangle the drawing area actually is, by translating cr so
    # (0,0) becomes that square's own top-left corner. Any leftover
    # strip on the long axis just reads as a plain margin, since it's
    # the same background color, not a visibly different letterbox
    # bar. Callers should treat the returned width/height as the
    # entire canvas from that point on; a no-op beyond the background
    # paint (original width/height, no translation) when the setting
    # is off.
    def apply_aspect_letterbox(self, cr, width, height):

        self.paint_background(cr)

        if not self.maintain_aspect_ratio:
            return width, height

        side = min(width, height)
        cr.translate((width - side) / 2, (height - side) / 2)

        return side, side

    def on_pipes_drag_begin(self, gesture, start_x, start_y):

        self.pipes_rotate_start = (self.pipes_azimuth, self.pipes_elevation)
        self.pipes_dragging = True
        self.on_view_drag_begin()

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
        self.on_view_drag_end()

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

    def on_terrain_drag_begin(self, gesture, start_x, start_y):

        self.terrain_rotate_start = (self.terrain_azimuth, self.terrain_elevation)
        self.on_view_drag_begin()

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

        self.reset_view()

    def on_terrain_scroll(self, controller, dx, dy):

        # Proportional steps feel even at any zoom level.
        self.zoom_view(1.1 ** -dy)

        return True

    def on_waterfall_drag_begin(self, gesture, start_x, start_y):

        self.waterfall_rotate_start = (
            self.waterfall_azimuth, self.waterfall_elevation
        )
        self.on_view_drag_begin()

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

        self.reset_view()

    def on_waterfall_scroll(self, controller, dx, dy):

        # Proportional steps feel even at any zoom level.
        self.zoom_view(1.1 ** -dy)

        return True

    def background_rgb(self):
        """The chosen background's color (an average for Frosted), or
        None for Transparent. Match Theme follows light/dark."""

        if self.background_mode == "transparent":
            return None
        if self.background_mode == "black":
            return (0.0, 0.0, 0.0)
        if self.background_mode == "custom":
            color = self.background_color
            return (color.red, color.green, color.blue)
        if self.background_mode == "frosted":
            # Dimmed in paint_background, so it reads dark.
            return tuple(c * 0.6 for c in MAIN_FRAME["average"])

        return self.theme_background_rgb()

    def background_is_opaque(self):

        return (
            self.background_mode in ("theme", "black", "custom")
            and self.background_opacity >= 0.999
        )

    def paint_background(self, cr):

        cr.save()
        cr.set_operator(cairo.OPERATOR_SOURCE)

        if self.background_mode == "transparent":
            cr.set_operator(cairo.OPERATOR_CLEAR)
            cr.paint()
        elif self.background_mode == "frosted" and MAIN_FRAME["surface"] is not None:
            clip = cr.clip_extents()
            width, height = clip[2] - clip[0], clip[3] - clip[1]
            cr.save()
            cr.translate(clip[0], clip[1])
            cr.scale(max(1, width) / FROST_SIZE, max(1, height) / FROST_SIZE)
            cr.set_source_surface(MAIN_FRAME["surface"], 0, 0)
            pattern = cr.get_source()
            pattern.set_filter(cairo.FILTER_BILINEAR)
            pattern.set_extend(cairo.EXTEND_PAD)
            cr.paint_with_alpha(self.background_opacity)
            cr.restore()
            # Dim it so the traces stay readable on top.
            cr.set_operator(cairo.OPERATOR_OVER)
            cr.set_source_rgba(0, 0, 0, 0.4 * self.background_opacity)
            cr.paint()
        else:
            rgb = self.background_rgb()
            cr.set_source_rgba(*rgb, self.background_opacity)
            cr.paint()

        cr.restore()

    def canvas_background_rgb(self):
        """An opaque color even when Transparent (for blending)."""

        rgb = self.background_rgb()

        return rgb if rgb is not None else self.theme_background_rgb()

    def theme_background_rgb(self):

        # Queried at every draw: Adw.StyleManager already reflects the
        # app's Light/Dark/Follow System choice.
        if Adw.StyleManager.get_default().get_dark():
            return (0.05, 0.05, 0.06)

        return (0.90, 0.90, 0.91)

    def canvas_foreground_rgba(self, alpha):

        # Axis lines, crosshairs, grids, labels: contrast with the actual
        # background, not just the theme (e.g. black in light mode).
        rgb = self.background_rgb()

        if rgb is None:
            dark = Adw.StyleManager.get_default().get_dark()
        else:
            dark = 0.299 * rgb[0] + 0.587 * rgb[1] + 0.114 * rgb[2] < 0.5

        return (1, 1, 1, alpha) if dark else (0, 0, 0, alpha)

    def peak_hold_draw_color(self, fallback_alpha=1.0):

        if self.peak_hold_color_mode == "custom":
            color = self.peak_hold_color
            return (color.red, color.green, color.blue, color.alpha)

        if self.peak_hold_color_mode == "main":
            return (*palette_color("main", 1.0), fallback_alpha)

        return self.canvas_foreground_rgba(fallback_alpha)

    def set_overlay_controls_visible(self, visible):

        buttons = ()    # no floating buttons left; kept for callers

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

        if self.compact_mode:
            # No title bar: just bring the move grip back for a while.
            self.move_handle.add_css_class("revealed")
            if self.hide_timer:
                GLib.source_remove(self.hide_timer)
            self.hide_timer = GLib.timeout_add_seconds(3, self.hide_toolbar)
            return

        self.toolbar_view.set_reveal_top_bars(True)
        self.set_overlay_controls_visible(True)

        if self.hide_timer:
            GLib.source_remove(self.hide_timer)

        self.hide_timer = GLib.timeout_add_seconds(3, self.hide_toolbar)

    def hide_toolbar(self):

        self.hide_timer = None

        handle = getattr(self, "move_handle", None)

        # Not while the pointer is on the grip itself.
        if handle is not None and self.compact_mode:
            if handle.get_state_flags() & Gtk.StateFlags.PRELIGHT:
                self.hide_timer = GLib.timeout_add_seconds(1, self.hide_toolbar)
                return False
            handle.remove_css_class("revealed")

        if self.mouse_over_toolbar:
            return False

        self.toolbar_view.set_reveal_top_bars(False)
        self.set_overlay_controls_visible(False)

        return False

    def push_audio(self, left, right):

        if self.gain != 1.0:
            gain = self.gain
            left = [sample * gain for sample in left]
            right = [sample * gain for sample in right]

        self.left = left
        self.right = right

        if self.kind in ("spectrum", "spectrogram", "terrain", "waterfall", "pipes"):

            mono = [(l + r) / 2.0 for l, r in zip(left, right)]

            self.spectrum_buffer.extend(mono)
            self.spectrum_buffer = self.spectrum_buffer[-FFT_SIZE:]

        if self.kind == "oscilloscope":
            self.scope_left.extend(left)
            self.scope_right.extend(right)

        if self.frame_rate_cap:
            now = time.monotonic()
            if now - self.last_draw_queued < 1.0 / self.frame_rate_cap:
                return
            self.last_draw_queued = now

        self.drawing_area.queue_draw()

    def on_draw(self, area, cr, width, height):

        self.draw_with_colors(area, cr, width, height)

        if self.glass_finish:
            self.draw_glass_finish(cr, width, height)

    def draw_glass_finish(self, cr, width, height):
        """A pane-of-glass sheen: soft highlight across the top, a thin
        light edge, a faint shade along the bottom."""

        cr.save()
        cr.reset_clip()

        highlight = cairo.LinearGradient(0, 0, 0, height * 0.45)
        highlight.add_color_stop_rgba(0, 1, 1, 1, 0.16)
        highlight.add_color_stop_rgba(1, 1, 1, 1, 0.0)
        cr.set_source(highlight)
        cr.rectangle(0, 0, width, height * 0.45)
        cr.fill()

        shade = cairo.LinearGradient(0, height * 0.75, 0, height)
        shade.add_color_stop_rgba(0, 0, 0, 0, 0.0)
        shade.add_color_stop_rgba(1, 0, 0, 0, 0.18)
        cr.set_source(shade)
        cr.rectangle(0, height * 0.75, width, height * 0.25)
        cr.fill()

        radius = min(14, width / 4, height / 4)
        self.rounded_rect_path(cr, 1, 1, width - 2, height - 2, radius)
        cr.set_source_rgba(1, 1, 1, 0.22)
        cr.set_line_width(1.2)
        cr.stroke()

        cr.restore()

    def draw_with_colors(self, area, cr, width, height):

        if self.color_sync <= 0 and not self.reverse_colors and self.color_mode != "gradient":
            self.color_signature = None
            self.draw_kind(area, cr, width, height)
            return

        # Swap in the matched/reversed colors for this frame only, so
        # the saved settings and controls keep the user's own choices.
        saved = self.apply_effective_colors()

        try:
            self.draw_kind(area, cr, width, height)
        finally:
            for name, value in saved.items():
                if name == "color_mode":
                    self._color_mode_override = None
                else:
                    setattr(self, name, value)

    def sync_blend(self, rgb, target):

        k = self.color_sync
        return tuple(rgb[c] + (target[c] - rgb[c]) * k for c in range(3))

    def effective_palette(self, slot, name, lo=None, hi=None):
        """A per-window palette: `name` (or the lo/hi gradient),
        reversed if asked, blended toward the main visualizer's."""

        key = f"effective-{id(self)}-{slot}"
        sync = self.color_sync
        signature = (
            name, self.reverse_colors, round(sync, 3),
            MAIN_PALETTE_VERSION[0] if sync > 0 else 0,
            lo.to_string() if lo is not None else None,
            hi.to_string() if hi is not None else None,
        )

        if self.effective_signatures.get(key) != signature or key not in _PALETTE_LUTS:
            lut = []
            for i in range(256):
                t = i / 255
                if self.reverse_colors:
                    t = 1.0 - t
                own = palette_color(name, t, lo, hi)
                if sync > 0:
                    own = self.sync_blend(own, palette_color("main", t))
                lut.append(own)

            PALETTE_STOPS[key] = None
            _PALETTE_LUTS[key] = lut
            self.effective_signatures[key] = signature

        return key, signature

    def apply_effective_colors(self):

        saved = {}
        signatures = []

        if self.color_mode not in ("solid", "classic"):
            saved["color_mode"] = self.color_mode
            self.color_mode, signature = self.effective_palette(
                "mode", self.color_mode, self.line_color_lo, self.line_color_hi
            )
            signatures.append(signature)

        palette_name = f"{self.kind}_palette"

        if hasattr(self, palette_name):
            saved[palette_name] = getattr(self, palette_name)
            key, signature = self.effective_palette(
                "palette", saved[palette_name],
                getattr(self, f"{self.kind}_color_lo"), getattr(self, f"{self.kind}_color_hi")
            )
            setattr(self, palette_name, key)
            signatures.append(signature)

        if self.color_sync > 0:
            dominant = MAIN_DOMINANT[0]

            def blended(color):
                rgb = self.sync_blend((color.red, color.green, color.blue), dominant)
                result = Gdk.RGBA()
                result.red, result.green, result.blue, result.alpha = (*rgb, color.alpha)
                return result

            saved["color"] = self.color
            self.color = blended(self.color)
            saved["dvd_color"] = self.dvd_color
            self.dvd_color = blended(self.dvd_color)

            saved["pipes_band_colors"] = self.pipes_band_colors
            self.pipes_band_colors = [
                (lambda c, t: (lambda rgb: Gdk.RGBA(red=rgb[0], green=rgb[1], blue=rgb[2], alpha=1.0))(
                    self.sync_blend((c.red, c.green, c.blue), palette_color("main", t))
                ))(color, (i + 1) / len(self.pipes_band_colors))
                for i, color in enumerate(self.pipes_band_colors)
            ]
            signatures.append(("solid", round(self.color_sync, 3), MAIN_PALETTE_VERSION[0]))

        signature = tuple(signatures)

        # 3D surfaces are cached - re-render only when the colors change.
        if signature != self.color_signature:
            self.terrain_dirty = True
            self.waterfall_dirty = True

        self.color_signature = signature

        return saved

    def draw_kind(self, area, cr, width, height):

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

        self.paint_background(cr)

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

        self.paint_background(cr)

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

        if self.vu_style == "needle":
            if height > width * 1.3:
                self.draw_meter_pair_stacked(
                    cr, width, height,
                    lambda c, x, y, w, h, level, hold: self.draw_vu_needle(c, x, y, w, h, level),
                    self.vu_left, self.vu_right, None, None
                )
                return
        elif width > height * 1.3:
            draw = self.draw_vu_led if self.vu_style == "led" else self.draw_vu_bar
            self.draw_meter_pair_horizontal(
                cr, width, height,
                lambda c, x, y, w, h, level, hold: draw(c, x, y, w, h, level, hold),
                self.vu_left, self.vu_right,
                self.vu_hold_left if self.vu_peak_hold else None,
                self.vu_hold_right if self.vu_peak_hold else None,
            )
            return

        margin = 12 if width >= 140 else 6
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

    def mode_color(self, level):
        """Trace/bar color at 0..1 for the current non-solid Color Mode."""

        return palette_color(self.color_mode, level)

    def meter_color(self, level, zones):
        """One color for a meter at `level`: Classic green/yellow/red
        zones, the Solid color, or the palette at that level."""

        if self.color_mode == "classic":
            if level > zones[1]:
                zone = (0.9, 0.2, 0.2)
            elif level > zones[0]:
                zone = (0.9, 0.8, 0.2)
            else:
                zone = (0.2, 0.8, 0.3)
            if self.color_sync > 0:
                zone = self.sync_blend(zone, palette_color("main", level))
            return zone

        if self.color_mode == "solid":
            return (self.color.red, self.color.green, self.color.blue)

        return palette_color(self.color_mode, level)

    def set_meter_fill(self, cr, x, y, height, level, zones):
        """Source for a vertical meter bar spanning y..y+height: palettes
        run bottom -> top along the bar; Classic/Solid are one color."""

        if self.color_mode == "classic":
            # Original thresholds: the color of the zone the level is in.
            if level < zones[0]:
                zone = (0.2, 0.8, 0.3)
            elif level < zones[1]:
                zone = (0.9, 0.8, 0.2)
            else:
                zone = (0.9, 0.2, 0.2)
            if self.color_sync > 0:
                zone = self.sync_blend(zone, palette_color("main", min(level, 1.0)))
            cr.set_source_rgb(*zone)
            return

        if self.color_mode == "solid":
            cr.set_source_rgb(self.color.red, self.color.green, self.color.blue)
            return

        gradient = cairo.LinearGradient(0, y + height, 0, y)

        for step in range(9):
            t = step / 8
            gradient.add_color_stop_rgb(t, *palette_color(self.color_mode, t))

        cr.set_source(gradient)

    def draw_meter_pair_horizontal(self, cr, width, height, draw, left, right, hold_left, hold_right):
        """Wide windows: L above R, filling left -> right. The vertical
        meter drawing is reused, turned 90 degrees."""

        margin = 8 if height >= 60 else 4
        label_width = 14 if self.show_labels else 0
        length = width - margin * 2 - label_width
        thickness = (height - margin * 3) / 2

        for row, (level, hold, name) in enumerate(((left, hold_left, "L"), (right, hold_right, "R"))):
            x0 = margin + label_width
            y0 = margin + row * (thickness + margin)

            cr.save()
            # Local bottom -> top of a vertical meter becomes left -> right.
            cr.translate(x0 + length, y0)
            cr.rotate(math.pi / 2)
            draw(cr, 0, 0, thickness, length, level, hold)
            cr.restore()

            if self.show_labels:
                self.draw_text_label(cr, margin - 1, y0 + thickness / 2 + 4, name)

    def draw_meter_pair_stacked(self, cr, width, height, draw, left, right, hold_left, hold_right):
        """Tall windows (VU needles): L above R, each full width."""

        margin = 8
        label_space = 14 if self.show_labels else 0
        cell_height = (height - margin * 3 - label_space * 2) / 2
        cell_width = width - margin * 2

        for row, (level, hold, name) in enumerate(((left, hold_left, "L"), (right, hold_right, "R"))):
            y0 = margin + row * (cell_height + label_space + margin)
            draw(cr, margin, y0, cell_width, cell_height, level, hold)

            if self.show_labels:
                self.draw_text_label(cr, width / 2 - 3, y0 + cell_height + label_space - 2, name)

    def draw_vu_bar(self, cr, x, y, bar_width, bar_height, level, hold_level=None):

        cr.set_source_rgba(1, 1, 1, 0.15)
        cr.rectangle(x, y, bar_width, bar_height)
        cr.fill()

        filled_height = bar_height * min(level, 1.0)

        # Color Mode: Classic green/yellow/red zones (like a hardware
        # VU meter), Solid, or a palette running up the bar.
        self.set_meter_fill(cr, x, y, bar_height, level, (0.7, 0.9))

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

            color = self.meter_color(threshold, (0.7, 0.9))

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

        if self.color_mode not in ("solid", "classic"):
            cr.set_source_rgb(*self.mode_color(level))
        else:
            cr.set_source_rgb(self.color.red, self.color.green, self.color.blue)

        cr.set_line_width(2.5)
        cr.move_to(cx, cy)
        cr.line_to(tip_x, tip_y)
        cr.stroke()

        cr.arc(cx, cy, 3.5, 0, 2 * math.pi)
        cr.fill()

    def db_frac(self, level):

        # Peak meter Scale: Linear shows the raw amplitude (0..1).
        if self.peak_scale == "linear":
            return max(0.0, min(1.0, level))

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
        self.set_meter_fill(cr, x, y, h, level_frac, (0.8, 0.95))

        cr.rectangle(x, y + (h - filled_height), w, filled_height)
        cr.fill()

        hold_y = y + h * (1.0 - min(hold_frac, 1.0))

        cr.set_source_rgba(*self.peak_hold_draw_color())
        cr.rectangle(x, hold_y - 1, w, 2)
        cr.fill()

    def draw_peak_scale(self, cr, x, y, h):

        last_y = -1e9

        if self.peak_scale == "linear":
            ticks = [(1.0, "1.0"), (0.75, ".75"), (0.5, ".5"), (0.25, ".25"), (0.0, "0")]
        else:
            ticks = [((db + 60) / 60, str(db)) for db in (0, -6, -12, -24, -40, -60)]

        for frac, label in ticks:
            tick_y = y + h * (1.0 - frac)

            # Short meters: drop ticks that would overlap.
            if abs(tick_y - last_y) < 13:
                continue

            last_y = tick_y
            self.draw_text_label(cr, x, tick_y + 3, label)

    def draw_peak_meter(self, cr, width, height):

        self.paint_background(cr)

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

        if width > height * 1.3:
            self.draw_meter_pair_horizontal(
                cr, width, height,
                lambda c, x, y, w, h, level, hold: self.draw_peak_bar(c, x, y, w, h, level, hold),
                self.db_frac(self.peak_left), self.db_frac(self.peak_right),
                self.db_frac(self.peak_hold_left), self.db_frac(self.peak_hold_right),
            )
            return

        margin = 12 if width >= 140 else 6
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

    # Catmull-Rom-to-Bezier: a smooth curve through every given point
    # (not just near them, unlike a plain Bezier fit) - requested for
    # X-Y Scope specifically ("more curved lines like a real x-y
    # scope"), since a real analog scope's electron beam physically
    # traces a continuous curve rather than the straight polyline
    # segments a plain move_to/line_to chain draws between discrete
    # samples. Standard technique: the Bezier control points for the
    # segment between p1 and p2 are derived from the *surrounding*
    # points p0 and p3, so each segment's tangent naturally continues
    # the curve's direction through its neighbors instead of putting a
    # visible kink at every sample.
    def catmull_rom_control_points(self, p0, p1, p2, p3):

        c1 = (p1[0] + (p2[0] - p0[0]) / 6.0, p1[1] + (p2[1] - p0[1]) / 6.0)
        c2 = (p2[0] - (p3[0] - p1[0]) / 6.0, p2[1] - (p3[1] - p1[1]) / 6.0)

        return c1, c2

    # One continuous smooth path through every point, single color -
    # the caller sets the source and calls stroke() once afterward.
    def stroke_smooth_path(self, cr, points):

        n = len(points)

        if n < 2:
            return

        cr.move_to(*points[0])

        if n == 2:
            cr.line_to(*points[1])
            return

        for i in range(n - 1):

            p0 = points[i - 1] if i > 0 else points[i]
            p1 = points[i]
            p2 = points[i + 1]
            p3 = points[i + 2] if i + 2 < n else points[i + 1]

            c1, c2 = self.catmull_rom_control_points(p0, p1, p2, p3)
            cr.curve_to(c1[0], c1[1], c2[0], c2[1], p2[0], p2[1])

    # Same smooth curve, but each segment gets its own color/stroke()
    # call - for the "rainbow comet trail" color mode, which needs a
    # color that changes along the trace's length.
    def stroke_smooth_path_rainbow(self, cr, points, opacity):

        n = len(points)

        if n < 2:
            return

        for i in range(n - 1):

            p0 = points[i - 1] if i > 0 else points[i]
            p1 = points[i]
            p2 = points[i + 1]
            p3 = points[i + 2] if i + 2 < n else points[i + 1]

            cr.set_source_rgba(*self.mode_color(i / max(1, n - 2)), opacity)
            cr.move_to(*p1)

            if n == 2:
                cr.line_to(*p2)
            else:
                c1, c2 = self.catmull_rom_control_points(p0, p1, p2, p3)
                cr.curve_to(c1[0], c1[1], c2[0], c2[1], p2[0], p2[1])

            cr.stroke()

    # The original plain polyline, kept as an option (Curved Lines
    # toggle) rather than replacing it outright.
    def stroke_straight_path(self, cr, points):

        if len(points) < 2:
            return

        cr.move_to(*points[0])

        for p in points[1:]:
            cr.line_to(*p)

    def stroke_straight_path_rainbow(self, cr, points, opacity):

        n = len(points)

        if n < 2:
            return

        for i in range(1, n):

            cr.set_source_rgba(*self.mode_color(i / max(1, n - 1)), opacity)
            cr.move_to(*points[i - 1])
            cr.line_to(*points[i])
            cr.stroke()

    def draw_xy_scope(self, cr, width, height):

        width, height = self.apply_aspect_letterbox(cr, width, height)

        cx = width / 2
        cy = height / 2
        scale = (min(width, height) / 2 - 8) * self.scope_zoom

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
            if self.xy_goniometer:
                self.draw_text_label(cr, width - 34, cy + 4, "Side")
                self.draw_text_label(cr, cx - 10, 14, "Mid")
            else:
                self.draw_text_label(cr, width - 16, cy + 4, "L")
                self.draw_text_label(cr, cx - 4, 14, "R")

        count = min(len(self.left), len(self.right))

        if count == 0:
            return

        # Points (self.xy_points, 0 = All) subsamples down to that many
        # evenly-spaced indices across the chunk instead of plotting
        # every raw sample - requested as an adjustable "number of
        # lines drawn" control. Evenly spaced (not just a stride) so
        # the last plotted sample is always the chunk's actual last
        # one, not whatever a fixed stride happens to land on.
        if 1 < self.xy_points < count:
            step = (count - 1) / (self.xy_points - 1)
            indices = [round(i * step) for i in range(self.xy_points)]
        else:
            indices = range(count)

        if self.xy_goniometer:
            # Rotated 45 degrees: mono (L = R) is vertical, out-of-phase
            # horizontal.
            k = 0.70710678
            points = [
                (cx + (self.left[i] - self.right[i]) * k * scale,
                 cy - (self.left[i] + self.right[i]) * k * scale)
                for i in indices
            ]
        else:
            points = [
                (cx + self.left[i] * scale, cy - self.right[i] * scale)
                for i in indices
            ]

        cr.set_line_width(self.xy_line_width)

        if self.color_mode not in ("solid", "classic"):
            if self.xy_curved:
                self.stroke_smooth_path_rainbow(cr, points, 0.85)
            else:
                self.stroke_straight_path_rainbow(cr, points, 0.85)
        else:
            cr.set_source_rgba(
                self.color.red, self.color.green, self.color.blue, 0.85
            )
            if self.xy_curved:
                self.stroke_smooth_path(cr, points)
            else:
                self.stroke_straight_path(cr, points)
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

        cr.set_line_width(self.scope_line_width)

        if self.color_mode not in ("solid", "classic"):

            prev_px, prev_py = None, None

            for i in range(count):

                px = x + (i / (count - 1)) * w
                py = y + h / 2 - samples[start + i] * (h / 2 * 0.9)

                if prev_px is not None:
                    cr.set_source_rgba(*self.mode_color(i / (count - 1)), opacity)
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

        self.paint_background(cr)

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

        width, height = self.apply_aspect_letterbox(cr, width, height)

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
            if self.background_is_opaque():
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
        if not self.background_is_opaque():
            # Fade the trail's alpha instead of painting a color over it.
            trail_cr.set_operator(cairo.OPERATOR_DEST_OUT)
            trail_cr.set_source_rgba(0, 0, 0, 1.0 - self.vector_persistence)
        else:
            trail_cr.set_operator(cairo.OPERATOR_OVER)
            trail_cr.set_source_rgba(*bg, 1.0 - self.vector_persistence)
        trail_cr.rectangle(0, 0, width, height)
        trail_cr.fill()

        cx, cy = width / 2, height / 2
        radius = min(width, height) / 2 - 12
        count = min(len(self.left), len(self.right))

        # Mid/side below are (L±R)/sqrt(2), so a full-scale signal
        # reaches sqrt(2) - divide that out so the full-scale diamond
        # (|L|, |R| <= 1) just touches the edges. It used to be plotted
        # sqrt(2) too big, so loud passages ran into the boundary.
        scale = radius / math.sqrt(2) * self.scope_zoom

        if self.vector_auto_gain and count:
            peak = max(
                max(abs(l + r), abs(l - r)) * 0.70710678
                for l, r in zip(self.left, self.right)
            )
            if peak > self.vector_envelope:
                self.vector_envelope = peak
            else:
                self.vector_envelope = self.vector_envelope * 0.97 + peak * 0.03

            # Loudest recent point at ~80% of the way to the edge; at
            # most 8x for near-silence.
            scale *= min(8.0, 0.8 * math.sqrt(2) / max(self.vector_envelope, 0.8 * math.sqrt(2) / 8.0))

        # Rotated 45° from a plain L/R plot - Mid ((L+R)/sqrt(2)) on
        # the vertical axis, Side ((L-R)/sqrt(2)) on the horizontal -
        # the standard goniometer convention: mono material (L == R)
        # draws a vertical line since Side is always 0, and fully out-
        # of-phase material (L == -R) draws a horizontal line since Mid
        # is always 0. A plain, unrotated L-vs-R plot (the existing X-Y
        # Scope) doesn't carry this same "read stereo width/phase at a
        # glance" meaning.
        half_dot = self.vector_dot_size / 2
        rainbow = self.color_mode not in ("solid", "classic")

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
                    *self.mode_color(i / max(1, count - 1)), 0.85
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

        # Full-scale reference: where |L| or |R| reaches 1 (without
        # Auto Gain the cloud can't go past it).
        if not self.vector_auto_gain:
            edge = radius * self.scope_zoom
            cr.set_source_rgba(*self.canvas_foreground_rgba(0.12))
            cr.move_to(cx, cy - edge)
            cr.line_to(cx + edge, cy)
            cr.line_to(cx, cy + edge)
            cr.line_to(cx - edge, cy)
            cr.close_path()
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

        dt = self.tick_interval_ms() / 1000.0

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
        # Never bigger than half the window, so it can still bounce.
        new_size = min(new_size, self.dvd_max_size(width, height))

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
        #
        # Because size changes almost every tick, a cache keyed on the
        # exact size missed nearly every time and re-rasterized the SVG
        # each frame (~770 times per 30s in a profile). Now it's
        # rasterized at size rounded *up* to the next 16px step, a few
        # of those are kept, and draw_dvd_text_logo scales down to the
        # exact size - same smooth size animation, a handful of
        # rasterizations instead of one per frame.
        size = -(-max(1, int(size)) // 16) * 16

        if size in self._dvd_logo_cache:
            return self._dvd_logo_cache[size]

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

        if len(self._dvd_logo_cache) >= 8:
            self._dvd_logo_cache.pop(next(iter(self._dvd_logo_cache)))

        self._dvd_logo_cache[size] = pixbuf

        return pixbuf

    def draw_dvd_text_logo(self, cr, size):

        pixbuf = self.load_dvd_logo_pixbuf(size)

        if pixbuf is not None:

            # The cached pixbuf is rasterized at size rounded up to a
            # 16px step (see load_dvd_logo_pixbuf) - scale it back down
            # to the exact requested size.
            scale = size / (-(-max(1, int(size)) // 16) * 16)

            cr.save()
            cr.translate(
                self.dvd_x, self.dvd_y + (size - pixbuf.get_height() * scale) / 2
            )
            cr.scale(scale, scale)

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

    def dvd_max_size(self, width, height):
        """dvd_size is the logo's width (DVD), height (Tux) or side."""

        if self.icon_name == DVD_TEXT_ICON:
            return max(8.0, min(width * 0.5, height * 0.5 * DVD_LOGO_ASPECT))
        if self.icon_name == TUX_ICON:
            return max(8.0, min(height * 0.5, width * 0.5 / TUX_ASPECT))
        return max(8.0, min(width, height) * 0.5)

    def draw_dvd_bounce(self, cr, width, height):

        self.paint_background(cr)

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

            magnitudes = spectrum_magnitudes(self.spectrum_buffer)

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

    def spectrum_bar_layout(self, width, bar_count):
        """(margin, gap, bar_width) that always fit the width - the gap
        shrinks with the space per bar instead of a fixed 3px."""

        margin = 8 if width >= 160 else 3
        area = max(1.0, width - margin * 2)
        gap = min(3.0, area * 0.25 / max(1, bar_count))
        bar_width = (area - gap * (bar_count - 1)) / max(1, bar_count)

        return margin, gap, bar_width

    def render_spectrum_bars(self, cr, width, height):

        bar_count = self.num_bars
        margin, gap, bar_width = self.spectrum_bar_layout(width, bar_count)
        bar_height = height - margin * 2

        rainbow = self.color_mode not in ("solid", "classic")

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
                cr.set_source_rgb(*self.mode_color(i / max(1, bar_count - 1)))

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
        margin = self.spectrum_bar_layout(width, bar_count)[0]
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

        if self.color_mode not in ("solid", "classic"):

            # A real left-to-right rainbow gradient fill (Cairo's own
            # LinearGradient), rather than per-bar solid fills the way
            # render_spectrum_bars does it - there's no per-bar
            # boundary left to color independently once this is one
            # continuous filled shape.
            gradient = cairo.LinearGradient(margin, 0, margin + inner_width, 0)

            stops = 8

            for s in range(stops + 1):
                t = s / stops
                gradient.add_color_stop_rgb(t, *self.mode_color(t))

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

        magnitudes = spectrum_magnitudes(self.spectrum_buffer)

        self.spectrogram_append_count += 1
        self.spectrogram_columns.append(
            self.bars_from_magnitudes(magnitudes, bin_count)
        )

    def spectrogram_color_key(self):

        lo, hi = self.spectrogram_color_lo, self.spectrogram_color_hi

        return (
            self.num_bars, self.spectrogram_palette,
            self.color_signature,
            MAIN_PALETTE_VERSION[0] if self.spectrogram_palette == "main" else 0,
            self.spectrogram_color_ceiling,
            (lo.red, lo.green, lo.blue), (hi.red, hi.green, hi.blue),
        )

    def spectrogram_pixel_table(self):

        # 256 precomputed BGRA pixels (cairo ARGB32, little-endian),
        # one per quantized color level, for the current palette/colors.
        table = []

        for i in range(256):

            level = i / 255

            if self.spectrogram_palette != "gradient":
                r, g, b = palette_color(self.spectrogram_palette, level)
            else:
                r, g, b = gradient_color(
                    level, self.spectrogram_color_lo, self.spectrogram_color_hi
                )

            table.append(bytes((
                int(b * 255 + 0.5), int(g * 255 + 0.5), int(r * 255 + 0.5), 255
            )))

        return table

    def write_spectrogram_column(self, data, stride, x, levels):

        bin_count = self.num_bars
        ceiling = max(0.05, self.spectrogram_color_ceiling)
        table = self.spectrogram_pixel_table_cache
        offset = x * 4

        for row_index in range(min(bin_count, len(levels))):

            scaled = levels[row_index] / ceiling
            index = 255 if scaled >= 1.0 else (0 if scaled <= 0.0 else int(scaled * 255 + 0.5))

            # Row 0 (lowest frequency) at the bottom of the image.
            start = (bin_count - 1 - row_index) * stride + offset
            data[start:start + 4] = table[index]

    def update_spectrogram_image(self):

        # One pixel per (column, frequency bin) cell, in a cached
        # SPECTROGRAM_COLUMNS x num_bars image (newest column at the
        # right edge, unfilled columns transparent), scaled up to the
        # window when drawn. Previously every draw re-filled every
        # cell as its own Cairo rectangle - ~12,800 fills per frame at
        # the default 64 bars, even though only one new column had
        # arrived. Now a new column just scrolls the image left by a
        # pixel and writes that one column; the full rebuild only
        # happens when a setting that changes the colors or bar count
        # does.
        key = self.spectrogram_color_key()
        new_columns = self.spectrogram_append_count - self.spectrogram_image_appends
        surface = self.spectrogram_image

        if surface is None or key != self.spectrogram_image_key:

            surface = cairo.ImageSurface(
                cairo.FORMAT_ARGB32, SPECTROGRAM_COLUMNS, max(1, self.num_bars)
            )
            self.spectrogram_image = surface
            self.spectrogram_image_key = key
            self.spectrogram_pixel_table_cache = self.spectrogram_pixel_table()
            new_columns = len(self.spectrogram_columns)

        if new_columns <= 0:
            return surface

        new_columns = min(new_columns, SPECTROGRAM_COLUMNS)

        surface.flush()
        data = surface.get_data()
        stride = surface.get_stride()
        row_bytes = SPECTROGRAM_COLUMNS * 4
        shift = new_columns * 4

        for y in range(surface.get_height()):
            start = y * stride
            data[start:start + row_bytes - shift] = data[start + shift:start + row_bytes]

        recent = list(self.spectrogram_columns)[-new_columns:]
        first_x = SPECTROGRAM_COLUMNS - len(recent)

        for offset, levels in enumerate(recent):
            self.write_spectrogram_column(data, stride, first_x + offset, levels)

        surface.mark_dirty()
        self.spectrogram_image_appends = self.spectrogram_append_count

        return surface

    def render_spectrogram(self, cr, width, height):

        if not self.spectrogram_columns:
            return

        surface = self.update_spectrogram_image()
        bin_count = max(1, self.num_bars)

        cr.save()

        if self.spectrogram_vertical:
            # Time runs top-to-bottom instead of left-to-right - newest
            # row at the top, scrolling downward, the usual convention
            # for a vertically-oriented waterfall (e.g. SDR receiver
            # software) - and frequency runs left-to-right, low at the
            # left. Image x (time, newest at the right) maps to screen
            # y (newest at the top); image y (frequency, low at the
            # bottom) maps to screen x (low at the left).
            column_width = width / bin_count
            row_height = height / SPECTROGRAM_COLUMNS
            cr.transform(cairo.Matrix(
                0, -row_height, -column_width, 0, width, height
            ))
        else:
            # Newest column at the right edge, low frequencies at the
            # bottom - same up-is-higher convention as Spectrum's bars.
            cr.scale(width / SPECTROGRAM_COLUMNS, height / bin_count)

        pattern = cairo.SurfacePattern(surface)
        # Crisp cell blocks, matching the old per-rectangle look,
        # rather than blurring between cells.
        pattern.set_filter(cairo.FILTER_NEAREST)
        cr.set_source(pattern)
        cr.rectangle(0, 0, SPECTROGRAM_COLUMNS, bin_count)
        cr.fill()

        cr.restore()

    def update_terrain_rows(self):

        bin_count = self.num_bars

        if len(self.spectrum_buffer) != FFT_SIZE:
            return

        now = time.monotonic()

        if now - self.last_terrain_frame_time < TERRAIN_FRAME_INTERVAL:
            return

        self.last_terrain_frame_time = now

        magnitudes = spectrum_magnitudes(self.spectrum_buffer)

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
        # Called once per projected point (~1M times per 30s with the
        # 3D kinds open) but the camera angles only change on drag, so
        # the trig is remembered for the last angles seen.
        trig = self._projection_trig

        if trig[0] != azimuth or trig[1] != elevation:
            trig = self._projection_trig = (
                azimuth, elevation,
                math.cos(azimuth), math.sin(azimuth),
                math.cos(elevation), math.sin(elevation),
            )

        _, _, cos_a, sin_a, cos_e, sin_e = trig

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

        self.paint_background(cr)

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

            if self.terrain_palette != "gradient":
                row_color = palette_color(self.terrain_palette, scaled_peak_level)
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

        self.auto_rotate_step()

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

        magnitudes = spectrum_magnitudes(self.spectrum_buffer)

        self.waterfall_rows.append(self.bars_from_magnitudes(magnitudes, bin_count))
        self.waterfall_dirty = True

    def render_waterfall_surface(self, width, height):

        self.waterfall_surface = cairo.ImageSurface(
            cairo.FORMAT_ARGB32, max(1, width), max(1, height)
        )
        cr = cairo.Context(self.waterfall_surface)

        self.paint_background(cr)

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
        if self.waterfall_palette != "gradient":
            row_colors = [
                [
                    palette_color(self.waterfall_palette, 
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

        self.auto_rotate_step()

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

        magnitudes = spectrum_magnitudes(self.spectrum_buffer)

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

        dt = self.tick_interval_ms() / 1000.0

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

        self.paint_background(cr)

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

    def label_width(self, cr, text):

        cr.select_font_face("sans-serif", cairo.FONT_SLANT_NORMAL, cairo.FONT_WEIGHT_NORMAL)
        cr.set_font_size(10)

        return cr.text_extents(text).width

    def draw_spectrum_labels(self, cr, width, height):

        bar_count = self.num_bars
        margin, gap, bar_width = self.spectrum_bar_layout(width, bar_count)
        smooth = self.spectrum_style == "smooth"
        inner_width = width - margin * 2
        last_end = -1e9

        for freq, label in ((100, "100Hz"), (1000, "1kHz"), (10000, "10kHz")):

            index = self.bar_index_for_frequency(freq, bar_count)

            if index is None:
                continue

            if smooth and bar_count > 1:
                x = margin + inner_width * index / (bar_count - 1)
            else:
                x = margin + index * (bar_width + gap)

            text_width = self.label_width(cr, label)
            x = max(2, min(x, width - text_width - 3))

            # Skip a label that would collide with the previous one.
            if x < last_end + 6:
                continue

            last_end = x + text_width
            self.draw_text_label(cr, x, height - 4, label)

    def draw_spectrogram_labels(self, cr, width, height):

        bin_count = self.num_bars
        last_end = -1e9
        last_y = 1e9

        if self.spectrogram_vertical:

            column_width = width / bin_count

            for freq, label in ((100, "100Hz"), (1000, "1kHz"), (10000, "10kHz")):

                index = self.bar_index_for_frequency(freq, bin_count)


                if index is None:

                    continue
                x = index * column_width

                x = min(x + 2, width - self.label_width(cr, label) - 3)
                if x < last_end + 6:
                    continue
                last_end = x + self.label_width(cr, label)
                self.draw_text_label(cr, x, 14, label)

        else:

            row_height = height / bin_count

            for freq, label in ((100, "100Hz"), (1000, "1kHz"), (10000, "10kHz")):

                index = self.bar_index_for_frequency(freq, bin_count)


                if index is None:

                    continue
                y = height - (index + 1) * row_height

                y = max(14, min(y + row_height - 3, height - 3))
                if y > last_y - 14:
                    continue
                last_y = y
                self.draw_text_label(cr, 4, y, label)

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
        min_freq = self.freq_min
        max_freq = max(self.freq_max, self.freq_min * 2)

        # Outside the shown range: no label for it.
        if freq < min_freq or freq > max_freq:
            return None

        ratio = max_freq / min_freq
        index = bar_count * math.log(freq / min_freq) / math.log(ratio)

        return max(0, min(bar_count - 1, int(index)))

    def bars_from_magnitudes(self, magnitudes, bar_count):

        # Log-spaced bins (20Hz-Nyquist) rather than linear - an
        # audio spectrum is perceived logarithmically, so linear
        # binning would crush almost the entire display into the
        # lowest handful of bars and leave the rest empty.
        min_freq = self.freq_min
        max_freq = max(self.freq_max, self.freq_min * 2)
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

        if keyval == Gdk.KEY_Escape and self.settings_split.get_show_sidebar():
            self.settings_split.set_show_sidebar(False)
            return True

        if self.kind in VIEW_KINDS and not self.settings_split.get_show_sidebar():
            step = math.radians(5)
            moves = {
                Gdk.KEY_Left: (step, 0), Gdk.KEY_Right: (-step, 0),
                Gdk.KEY_Up: (0, step), Gdk.KEY_Down: (0, -step),
            }
            if keyval in moves:
                self.rotate_view(*moves[keyval])
                return True
            if keyval in (Gdk.KEY_plus, Gdk.KEY_equal, Gdk.KEY_KP_Add):
                self.zoom_view(1.1)
                return True
            if keyval in (Gdk.KEY_minus, Gdk.KEY_KP_Subtract):
                self.zoom_view(1 / 1.1)
                return True
            if keyval in (Gdk.KEY_Home, Gdk.KEY_0, Gdk.KEY_KP_0):
                self.reset_view()
                return True

        if keyval == Gdk.KEY_Escape and self.is_fullscreen():
            self.unfullscreen()
            return True

        return False

    def on_close_request(self, window):

        if getattr(self, "square_timer", None) is not None:
            GLib.source_remove(self.square_timer)
            self.square_timer = None

        if self.settings_window is not None:
            settings_window, self.settings_window = self.settings_window, None
            settings_window.destroy()

        for key in list(self.effective_signatures):
            PALETTE_STOPS.pop(key, None)
            _PALETTE_LUTS.pop(key, None)
        self.effective_signatures.clear()

        if self.context_menu is not None:
            self.context_menu.unparent()
            self.context_menu = None

        self.save_settings()
        self.cursor_hider.stop()

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
        # alive there too. Default priority, not idle: with several aux
        # windows redrawing, the main loop can stay busy enough that a
        # plain idle callback never runs - the closed window (and all
        # its buffers) then just sat in memory forever.
        GLib.idle_add(
            self.destroy_and_dispose,
            priority=GLib.PRIORITY_DEFAULT
        )

        return True

    # destroy() alone left closed windows in memory for the rest of the
    # session. Child widgets/controllers stored as attributes (e.g.
    # self.drawing_area) hold callbacks bound to this window's methods
    # (set_draw_func, gesture/notify handlers...), so: window's Python
    # attributes -> child GObject -> C-side callback -> window. That
    # loop passes through GTK's C code, which Python's GC can't see
    # into, and run_dispose() doesn't break it either (the children
    # stay alive through our own attribute references). Dropping the
    # attributes once the window is torn down does: the children are
    # then finalized, their callbacks released, and the window, its
    # buffers and Cairo surfaces are actually freed. Event controllers
    # and win.* actions attached to the window itself form the same
    # loop through the window's own GObject (which GTK only releases on
    # finalize), so those are removed explicitly first.
    def destroy_and_dispose(self):

        self.destroy()

        for controller in list(self.observe_controllers()):
            self.remove_controller(controller)

        for action_name in self.list_actions():
            self.remove_action(action_name)

        self.run_dispose()
        self.__dict__.clear()

        return False
