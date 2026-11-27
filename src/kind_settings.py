#!/usr/bin/env python3
"""
Per-kind setting descriptors.

Each kind exports a ``<kind>_settings()`` function returning a list of
``Group`` / ``Setting`` objects.  The generic renderer in
``mini_viz_settings.py`` turns those into an ``Adw.PreferencesPage``.
"""

from src.mini_viz_settings import (
    Group, Setting, Toggle, Scale, Choice, Color,
    format_hz, format_percent, format_times, format_px, format_dps,
    collect_saved_attrs,
)

# ---------------------------------------------------------------------------
# Re-exported constants --- keep in sync with aux_window.py                
# ---------------------------------------------------------------------------

# DVD
DVD_TEXT_ICON = "__dvd_text__"
TUX_ICON = "tux-symbolic"
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

# Common choice lists
FRAME_RATE_CHOICES = [
    (0, "Unlimited"), (60, "60 fps"), (30, "30 fps"),
    (20, "20 fps"), (15, "15 fps"), (10, "10 fps"),
]

BACKGROUND_CHOICES = [
    ("custom", "Color"),
    ("main", "Main Visualizer"),
    ("system", "System Theme"),
    ("transparent", "Transparent"),
]

# Palette choices shared by several kinds
PALETTE_LABELS = [
    ("rainbow", "Rainbow"), ("fire", "Fire"), ("ice", "Ice"),
    ("viridis", "Viridis"), ("magma", "Magma"), ("plasma", "Plasma"),
    ("ocean", "Ocean"), ("sunset", "Sunset"), ("neon", "Neon"),
    ("forest", "Forest"), ("matrix", "Matrix"), ("candy", "Candy"),
    ("grayscale", "Grayscale"),
]

# Gradient choices for kinds that support it
GRADIENT_CHOICES = [("gradient", "Custom")] + PALETTE_LABELS

# Meter / spectrum specific
SPECTRUM_STYLE_CHOICES = [
    ("led", "LED Bars"), ("bars", "Solid Bars"), ("smooth", "Smooth Curve"),
]

SPECTRUM_GRADIENT_CHOICES = [
    ("vertical", "Vertical"), ("lengthwise", "Lengthwise"),
]

VU_STYLE_CHOICES = [
    ("bars", "Bars"), ("led", "LED"), ("needle", "Needle"),
]

LED_PEAK_STYLE_CHOICES = [
    ("line", "Line"), ("dot", "Dot"), ("glow", "Glow"),
]

PEAK_HOLD_TINT_CHOICES = [
    ("white", "White"), ("meter", "Meter Color"), ("custom", "Custom"),
]

PEAK_SCALE_CHOICES = [("db", "Decibels"), ("linear", "Linear")]

PIPE_STYLE_CHOICES = [("round", "Round"), ("square", "Square")]

TERRAIN_AUTO_ROTATE_CHOICES = [
    ("none", "None"), ("cw", "Clockwise"), ("ccw", "Counter-Clockwise"),
]

# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

def frequency_range_group() -> Group:
    """Shared by spectrum / spectrogram / terrain / waterfall."""
    return Group("Frequency Range", [
        Scale("freq_min", "Lowest Frequency", 20, 1000, 10, format_fn=format_hz),
        Scale("freq_max", "Highest Frequency", 1000, 20000, 50, format_fn=format_hz),
    ])


def peak_hold_group() -> Group:
    """Shared by VU / Peak / Spectrum meters."""
    return Group("Peak Hold", [
        Toggle("peak_hold", "Peak Hold"),
        Choice("peak_hold_style", "Style", LED_PEAK_STYLE_CHOICES,
               visible_when=lambda w: w.peak_hold),
        Scale("peak_hold_seconds", "Hold Time", 0.2, 4.0, 0.1,
              visible_when=lambda w: w.peak_hold),
        Choice("peak_hold_tint", "Tint", PEAK_HOLD_TINT_CHOICES,
               visible_when=lambda w: w.peak_hold),
        Color("peak_hold_color", "Custom Color",
              visible_when=lambda w: w.peak_hold and w.peak_hold_tint == "custom"),
    ])


def color_group() -> Group:
    """Shared color palette / style group for bar/line kinds."""
    return Group("Colors", [
        Choice("color_style", "Color Style", [
            ("solid", "Solid"), ("classic", "Classic"), ("gradient", "Gradient"),
        ]),
        Color("color", "Color",
              visible_when=lambda w: w.color_style == "solid" or
                                    (w.color_style == "classic" and
                                     getattr(w, "vu_style", None) == "needle")),
        Choice("line_gradient", "Gradient", GRADIENT_CHOICES,
               visible_when=lambda w: w.color_style == "gradient"),
        Color("line_color_lo", "Low Color",
              visible_when=lambda w: w.color_style == "gradient" and
                                    w.line_gradient == "gradient"),
        Color("line_color_hi", "High Color",
              visible_when=lambda w: w.color_style == "gradient" and
                                    w.line_gradient == "gradient"),
        Toggle("reverse_colors", "Reverse Colors",
               visible_when=lambda w: w.color_style == "gradient"),
    ])


def background_group() -> Group:
    """Background settings (always visible, separate from Colors)."""
    return Group("Background", [
        Choice("background_mode", "Background", BACKGROUND_CHOICES),
        Color("background_color", "Background Color",
              visible_when=lambda w: w.background_mode == "custom"),
    ])


# ---------------------------------------------------------------------------
# Per-kind descriptors
# ---------------------------------------------------------------------------

def dvd_settings() -> list:
    return [
        Group("Appearance", [
            Choice("icon_name", "Icon", DVD_ICON_CHOICES),
            Scale("dvd_base_size", "Size", 24, 160, 2,
                  format_fn=lambda v: f"{v:.0f}px"),
            Scale("dvd_speed_scale", "Speed", 0.25, 3.0, 0.05,
                  format_fn=format_times),
            Scale("dvd_reactivity", "Audio Reactivity", 0.0, 2.5, 0.05,
                  format_fn=format_times),
        ]),
        Group("Audio Effects", [
            Toggle("dvd_beats_enabled", "React to Beats"),
            Scale("dvd_beat_sensitivity", "Beat Sensitivity",
                  1.05, 2.5, 0.05,
                  visible_when=lambda w: w.dvd_beats_enabled),
        ]),
        background_group(),
        Group("Window", [
            Scale("frame_rate_cap", "Frame Rate", 0, 100, 1,
                  format_fn=lambda v: "Unlimited" if v == 0 else f"{v:.0f} fps",
                  visible_when=lambda w: True),  # always visible, but we keep the pattern
        ]),
    ]


def oscilloscope_settings() -> list:
    return [
        Group("Display", [
            Scale("scope_time_base", "Time Base", 1, 20, 1,
                  format_fn=lambda v: f"{v:.0f}x"),
            Toggle("scope_trigger", "Trigger",
                   subtitle="Show horizontal trigger line"),
        ]),
        color_group(),
        background_group(),
        Group("Window", [
            Scale("frame_rate_cap", "Frame Rate", 0, 100, 1,
                  format_fn=lambda v: "Unlimited" if v == 0 else f"{v:.0f} fps"),
        ]),
    ]


def xy_scope_settings() -> list:
    return [
        Group("Display", [
            Toggle("xy_goniometer", "Goniometer View",
                   subtitle="Rotate 45°: mono is vertical, stereo width spreads sideways"),
            Scale("xy_line_width", "Line Width", 0.5, 6.0, 0.1,
                  format_fn=format_px),
            Toggle("xy_points", "Show Points"),
            Toggle("xy_curved", "Curved", subtitle="Smooth bezier curves"),
        ]),
        color_group(),
        background_group(),
        Group("Window", [
            Scale("frame_rate_cap", "Frame Rate", 0, 100, 1,
                  format_fn=lambda v: "Unlimited" if v == 0 else f"{v:.0f} fps"),
        ]),
    ]


def vectorscope_settings() -> list:
    return [
        Group("Display", [
            Scale("vector_dot_size", "Dot Size", 1.0, 8.0, 0.5,
                  format_fn=format_px),
            Scale("vector_persistence", "Persistence", 0.0, 0.95, 0.01,
                  format_fn=format_percent),
            Toggle("vector_auto_gain", "Auto Gain",
                   subtitle="Keep the cloud a steady size whatever the volume"),
        ]),
        color_group(),
        background_group(),
        Group("Window", [
            Scale("frame_rate_cap", "Frame Rate", 0, 100, 1,
                  format_fn=lambda v: "Unlimited" if v == 0 else f"{v:.0f} fps"),
        ]),
    ]


def vu_meter_settings() -> list:
    return [
        Group("Display", [
            Choice("vu_style", "Style", VU_STYLE_CHOICES),
            Scale("vu_height_scale", "Height Scale", 0.25, 2.5, 0.05,
                  format_fn=format_times),
            Scale("vu_segments", "LED Segments", 4, 30, 1,
                  visible_when=lambda w: w.vu_style == "led"),
            Scale("vu_yellow_zone", "Yellow Zone", 0.1, 0.9, 0.05,
                  visible_when=lambda w: w.vu_style == "led"),
            Scale("vu_red_zone", "Red Zone", 0.2, 0.98, 0.05,
                  visible_when=lambda w: w.vu_style == "led"),
        ]),
        color_group(),
        peak_hold_group(),
        background_group(),
        Group("Window", [
            Scale("frame_rate_cap", "Frame Rate", 0, 100, 1,
                  format_fn=lambda v: "Unlimited" if v == 0 else f"{v:.0f} fps"),
        ]),
    ]


def peak_meter_settings() -> list:
    return [
        Group("Display", [
            Choice("peak_scale", "Scale", PEAK_SCALE_CHOICES,
                   subtitle="Decibels spread out quiet levels; Linear is raw amplitude"),
            Scale("peak_line_thickness", "Hold Line Thickness",
                  0.5, 3.0, 0.25, format_fn=format_times),
        ]),
        color_group(),
        peak_hold_group(),
        background_group(),
        Group("Window", [
            Scale("frame_rate_cap", "Frame Rate", 0, 100, 1,
                  format_fn=lambda v: "Unlimited" if v == 0 else f"{v:.0f} fps"),
        ]),
    ]


def spectrum_settings() -> list:
    return [
        Group("Display", [
            Scale("num_bars", "Bars", 4, 64, 1),
            Choice("spectrum_style", "Style", SPECTRUM_STYLE_CHOICES),
            Scale("spectrum_led_segments", "LED Segments", 4, 64, 2,
                  visible_when=lambda w: w.spectrum_style == "led"),
            Scale("spectrum_yellow_zone", "Yellow Zone", 0.1, 0.9, 0.05,
                  visible_when=lambda w: w.spectrum_style == "led"),
            Scale("spectrum_red_zone", "Red Zone", 0.2, 0.98, 0.05,
                  visible_when=lambda w: w.spectrum_style == "led"),
            Scale("spectrum_height_scale", "Height Scale",
                  0.25, 2.5, 0.05, format_fn=format_times),
        ]),
        color_group(),
        peak_hold_group(),
        background_group(),
        Group("Window", [
            Scale("frame_rate_cap", "Frame Rate", 0, 100, 1,
                  format_fn=lambda v: "Unlimited" if v == 0 else f"{v:.0f} fps"),
        ]),
    ]


def spectrogram_settings() -> list:
    return [
        Group("Display", [
            Choice("spectrogram_vertical", "Orientation",
                   [(True, "Vertical"), (False, "Horizontal")]),
            Scale("spectrogram_speed", "Scroll Speed", 1, 10, 1,
                  format_fn=lambda v: f"{v:.0f}x"),
        ]),
        Group("Colors", [
            Choice("spectrogram_palette", "Palette", PALETTE_LABELS),
            Color("spectrogram_color_lo", "Low Color"),
            Color("spectrogram_color_hi", "High Color"),
            Scale("spectrogram_color_ceiling", "Color Ceiling",
                  0.5, 2.0, 0.05, format_fn=format_times),
        ]),
        frequency_range_group(),
        background_group(),
        Group("Window", [
            Scale("frame_rate_cap", "Frame Rate", 0, 100, 1,
                  format_fn=lambda v: "Unlimited" if v == 0 else f"{v:.0f} fps"),
        ]),
    ]


def terrain_settings() -> list:
    return [
        Group("Display", [
            Choice("terrain_palette", "Palette", PALETTE_LABELS),
            Color("terrain_color_lo", "Low Color"),
            Color("terrain_color_hi", "High Color"),
            Scale("terrain_color_ceiling", "Color Ceiling",
                  0.5, 2.0, 0.05, format_fn=format_times),
            Scale("terrain_zoom", "Zoom", 0.25, 4.0, 0.05,
                  format_fn=format_times),
            Toggle("terrain_show_axes", "Show Axes"),
            Choice("terrain_auto_rotate", "Auto-Rotate", TERRAIN_AUTO_ROTATE_CHOICES),
            Scale("terrain_rotate_speed", "Rotate Speed", 1, 60, 1,
                  format_fn=format_dps,
                  visible_when=lambda w: w.terrain_auto_rotate != "none"),
        ]),
        frequency_range_group(),
        background_group(),
        Group("Window", [
            Scale("frame_rate_cap", "Frame Rate", 0, 100, 1,
                  format_fn=lambda v: "Unlimited" if v == 0 else f"{v:.0f} fps"),
        ]),
    ]


def waterfall_settings() -> list:
    return [
        Group("Display", [
            Choice("waterfall_palette", "Palette", PALETTE_LABELS),
            Color("waterfall_color_lo", "Low Color"),
            Color("waterfall_color_hi", "High Color"),
            Scale("waterfall_color_ceiling", "Color Ceiling",
                  0.5, 2.0, 0.05, format_fn=format_times),
            Scale("waterfall_zoom", "Zoom", 0.25, 4.0, 0.05,
                  format_fn=format_times),
            Toggle("waterfall_show_axes", "Show Axes"),
            Choice("waterfall_auto_rotate", "Auto-Rotate", TERRAIN_AUTO_ROTATE_CHOICES),
            Scale("waterfall_rotate_speed", "Rotate Speed", 1, 60, 1,
                  format_fn=format_dps,
                  visible_when=lambda w: w.waterfall_auto_rotate != "none"),
        ]),
        frequency_range_group(),
        background_group(),
        Group("Window", [
            Scale("frame_rate_cap", "Frame Rate", 0, 100, 1,
                  format_fn=lambda v: "Unlimited" if v == 0 else f"{v:.0f} fps"),
        ]),
    ]


def pipes_settings() -> list:
    return [
        Group("Display", [
            Choice("pipes_style", "Tube Style", PIPE_STYLE_CHOICES),
            Scale("pipes_max_pipes", "Max Pipes", 1, 8, 1),
            Scale("pipes_base_width", "Tube Width", 1.0, 16.0, 0.5,
                  format_fn=format_px),
            Scale("pipes_fade_time", "Fade Time", 0.0, 5.0, 0.1,
                  format_fn=lambda v: f"{v:.1f}s",
                  visible_when=lambda w: w.pipes_fade_enabled),
        ]),
        color_group(),
        background_group(),
        Group("Window", [
            Scale("frame_rate_cap", "Frame Rate", 0, 100, 1,
                  format_fn=lambda v: "Unlimited" if v == 0 else f"{v:.0f} fps"),
        ]),
    ]


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------

KIND_REGISTRY = {
    "dvd": dvd_settings,
    "oscilloscope": oscilloscope_settings,
    "xy": xy_scope_settings,
    "vectorscope": vectorscope_settings,
    "vu": vu_meter_settings,
    "peak": peak_meter_settings,
    "spectrum": spectrum_settings,
    "spectrogram": spectrogram_settings,
    "terrain": terrain_settings,
    "waterfall": waterfall_settings,
    "pipes": pipes_settings,
}

# Convenience export for wiring up in aux_window.py
SAVED_SETTINGS = collect_saved_attrs(KIND_REGISTRY)

# Legacy migration map: old attr -> new attr
LEGACY_ATTR_MAP = {
    "vu_peak_hold": "peak_hold",
    "spectrum_peak_hold": "peak_hold",
    "vu_peak_hold_style": "peak_hold_style",
    "spectrum_peak_hold_style": "peak_hold_style",
}
