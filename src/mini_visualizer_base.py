# mini_visualizer_base.py
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

"""What every mini visualizer window shares, whatever it draws.

MiniVisualizerWindow: the settings sidebar / separate settings window
and the row builders behind them, saving and loading, the background,
colour matching with the main visualizer, Compact Mode's auto-hiding
header, fullscreen, and small drawing helpers. aux_window.py's
AuxVisualizerWindow builds on it and still holds every kind's own state
and drawing (step 2 splits those into one class per kind).

Also here: the colour palettes and the shared constants and helpers for
settings persistence.
"""

import colorsys
import json
import math
import time
from pathlib import Path

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")

from gi.repository import Adw, Gdk, Gio, GLib, GObject, Gtk
import cairo


# Remembered between launches, per kind, in
# ~/.config/melange/mini_visualizers.json (see load_saved_settings).
# Everything a setting control changes, plus the 3D views' camera.
SAVED_SETTINGS = [
    "color", "color_style", "line_gradient", "line_color_lo", "line_color_hi", "num_bars", "decay", "show_labels",
    "mirror_reflection", "maintain_aspect_ratio", "scope_zoom",
    "scope_scale_x", "scope_scale_y",
    "xy_line_width", "xy_points", "xy_curved",
    "vu_style", "peak_hold", "peak_hold_style", "vu_segments", "vu_height_scale",
    "vu_yellow_zone", "vu_red_zone",
    "peak_hold_seconds", "peak_hold_color_mode", "peak_hold_tint", "peak_line_thickness", "vu_led_thickness", "spectrum_led_thickness", "peak_hold_color",
    "spectrum_height_scale", "spectrum_style", "spectrum_gradient_direction", "spectrum_led_segments",
    "spectrum_yellow_zone", "spectrum_red_zone",
    "scope_time_base", "scope_trigger", "scope_gradient_direction", "vector_persistence", "vector_dot_size",
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
    "pipes_rotate_speed", "pipes_rotate_direction", "pipes_beats_enabled", "pipes_beat_rotation_enabled",
    "pipes_beat_zoom_enabled", "pipes_pulse_width", "pipes_pulse_color",
    "pipes_tube_shading", "pipes_zoom", "pipes_azimuth", "pipes_elevation",
    # Shared (every kind)
    "background_mode", "main_bg_plain", "background_color", "gain", "frame_rate_cap",
    "compact_mode", "freq_min", "freq_max", "xy_goniometer",
    "terrain_auto_rotate", "terrain_rotate_speed", "terrain_rotate_direction",
    "waterfall_auto_rotate", "waterfall_rotate_speed", "waterfall_rotate_direction",
    "view_hint_shown", "vector_auto_gain", "color_sync", "local_sync_style", "reverse_colors",
    "terrain_pan_x", "terrain_pan_y", "waterfall_pan_x", "waterfall_pan_y",
    "pipes_pan_x", "pipes_pan_y",
    "scope_line_width", "peak_scale",
]


# Starting camera (azimuth, elevation in degrees) of the 3D kinds -
# Reset View / double-click / Home go back to these.
VIEW_DEFAULTS = {"terrain": (35, 55), "waterfall": (35, 55), "pipes": (35, 28)}


VIEW_KINDS = tuple(VIEW_DEFAULTS)


VIEW_HINT = "Drag to rotate · Shift+drag to move · Scroll to zoom · Double-click to reset"


SAVE_DELAY_MS = 1000


def saved_settings_path():

    return Path(GLib.get_user_config_dir()) / "melange" / "mini_visualizers.json"


def read_saved_settings():

    try:
        data = json.loads(saved_settings_path().read_text())
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def reset_all_saved_settings():
    """Forget every kind's remembered settings and size."""

    try:
        saved_settings_path().unlink(missing_ok=True)
    except OSError as error:
        print(f"Couldn't reset mini visualizer settings: {error}")


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
    MAIN_DOMINANT[0] = tuple(old[c] + (dominant[c] - old[c]) * 0.25 for c in range(3))

    current = PALETTE_STOPS["main"]
    PALETTE_STOPS["main"] = [
        tuple(old[c] + (new[c] - old[c]) * 0.25 for c in range(3))
        for old, new in zip(current, stops)
    ]
    _PALETTE_LUTS.pop("main", None)
    MAIN_PALETTE_VERSION[0] += 1


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

PEAK_HOLD_TINT_CHOICES = [("brighter", "Brighter"), ("same", "Same"), ("dimmer", "Dimmer")]


METER_STYLE_CHOICES = [("classic", "Classic")] + STYLE_CHOICES


# Rows that belong in the Colors group, in display order (whichever a
# kind has). Built partly by build_settings_controls, partly by
# build_color_controls; build_settings_page gathers them.
COLOR_ROW_ORDER = [
    "Color Style", "Color", "Gradient", "Gradient Direction",
    "Low Color", "High Color", "High Level",
    "Reverse", "Bass Color", "Low Mid Color", "High Mid Color", "Treble Color",
    "Pulse Color", "Peak Hold Color",
    "Match Main Visualizer",
    "Background", "Background Color",
]


# A color (Background Color), the main visualizer's darkest color, or
# see-through to the desktop.
BACKGROUND_CHOICES = [
    ("custom", "Color"), ("main", "Main Visualizer"), ("system", "System Theme"), ("transparent", "Transparent"),
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


class MiniVisualizerWindow(Adw.ApplicationWindow):
    """Base for the mini visualizer windows (see the module docstring).
    Subclasses provide draw_kind(area, cr, width, height) and the
    kind-specific state the shared code reads."""

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

    # Settings as libadwaita rows: the dialog's content.
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

    def tick_interval_ms(self):

        fps = self.frame_rate_cap or 60

        return max(16, int(1000 / fps))

    def open_settings_dialog(self):

        page = self.build_settings_page(self.build_settings_controls())
        self.update_row_sensitivity()

        dialog = Adw.Dialog(
            title=f"{AUX_WINDOW_TITLES[self.kind]} Settings",
        )
        dialog.set_content_width(360)
        dialog.set_content_height(480)
        dialog.set_size_request(260, 200)

        view = Adw.ToolbarView()
        view.add_top_bar(Adw.HeaderBar())
        view.set_content(page)
        dialog.set_child(view)

        keys = Gtk.EventControllerKey()
        keys.connect(
            "key-pressed",
            lambda controller, keyval, *args: (dialog.close() or True) if keyval == Gdk.KEY_Escape else False
        )
        dialog.add_controller(keys)

        dialog.connect("closed", self.on_settings_dialog_closed)
        self.settings_dialog = dialog
        # If the mini visualizer itself is tiny, present without a
        # parent so the dialog floats freely on the desktop (pop-out).
        if self.get_width() < 360 or self.get_height() < 240:
            self._settings_dialog_floating = True
            dialog.set_presentation_mode(Adw.DialogPresentationMode.FLOATING)
            dialog.present()
        else:
            self._settings_dialog_floating = False
            dialog.set_presentation_mode(Adw.DialogPresentationMode.BOTTOM_SHEET)
            dialog.present(self)

    def on_settings_dialog_closed(self, dialog):

        self.settings_dialog = None
        self._settings_dialog_floating = False
        self.reset_to_current_controls()

        return False

    def reset_to_current_controls(self):
        """Rebuild the dialog so its controls show the current values
        (after a change made somewhere else, e.g. the right-click menu)."""

        if self.settings_dialog is not None:
            self.settings_dialog.get_child().set_content(
                self.build_settings_page(self.build_settings_controls())
            )
            self.update_row_sensitivity()

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

    def background_rgb(self):
        """The background color (sampled for Main Visualizer), or None
        for Transparent."""

        if self.background_mode == "transparent":
            return None

        if self.background_mode == "main":
            if getattr(self, "main_bg_plain", False):
                if Adw.StyleManager.get_default().get_dark():
                    return (0.05, 0.05, 0.06)
                return (0.93, 0.93, 0.94)
            base = palette_color("main", 0.0)
            if Adw.StyleManager.get_default().get_dark():
                # Dark mode: its darkest color, darker still so traces stay readable.
                return tuple(c * 0.55 for c in base)
            # Light mode: light base with a stronger tint from the palette.
            return tuple(0.80 + c * 0.16 for c in base)

        if self.background_mode == "system":
            return self.theme_background_rgb()

        color = self.background_color
        return (color.red, color.green, color.blue)

    def background_is_opaque(self):

        return self.background_mode != "transparent"

    def paint_background(self, cr):

        rgb = self.background_rgb()
        cr.save()

        if rgb is None:
            cr.set_operator(cairo.OPERATOR_CLEAR)
        else:
            cr.set_source_rgb(*rgb)

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

    def on_draw(self, area, cr, width, height):

        self.draw_with_colors(area, cr, width, height)

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

    def mode_color(self, level):
        """Trace/bar color at 0..1 for the current non-solid Color Mode."""

        if self.reverse_colors:
            level = 1.0 - level
        return palette_color(self.color_mode, level)

    def rounded_rect_path(self, cr, x, y, w, h, r):

        r = min(r, w / 2, h / 2)

        cr.new_sub_path()
        cr.arc(x + w - r, y + r, r, -math.pi / 2, 0)
        cr.arc(x + w - r, y + h - r, r, 0, math.pi / 2)
        cr.arc(x + r, y + h - r, r, math.pi / 2, math.pi)
        cr.arc(x + r, y + r, r, math.pi, 3 * math.pi / 2)
        cr.close_path()

    def label_width(self, cr, text):

        cr.select_font_face("sans-serif", cairo.FONT_SLANT_NORMAL, cairo.FONT_WEIGHT_NORMAL)
        cr.set_font_size(10)

        return cr.text_extents(text).width

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

        # Clean up any per-window palette entries that might have been
        # missed if the close path bypassed the normal cleanup.
        for key in list(getattr(self, "effective_signatures", {})):
            PALETTE_STOPS.pop(key, None)
            _PALETTE_LUTS.pop(key, None)

        self.__dict__.clear()

        return False
