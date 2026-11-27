#!/usr/bin/env python3
"""
Per-kind setting descriptors.

Each kind exports a ``<kind>_settings()`` function returning a list of
``Group`` / ``Setting`` objects.  The generic renderer in
``mini_viz_settings.py`` turns those into an ``Adw.PreferencesPage``.
"""

from melange.mini_viz_settings import (
    Group, Toggle, Scale, Choice, Color,
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
        Scale(
            attr="freq_min",
            title="Lowest Frequency",
            min=20,
            max=1000,
            step=10,
            format_fn=format_hz,
        ),
        Scale(
            attr="freq_max",
            title="Highest Frequency",
            min=1000,
            max=20000,
            step=50,
            format_fn=format_hz,
        ),
    ])


def peak_hold_group() -> Group:
    """Shared by VU / Peak / Spectrum meters."""
    return Group("Peak Hold", [
        Toggle(attr="peak_hold", title="Peak Hold"),
        Choice(
            attr="peak_hold_style",
            title="Style",
            options=LED_PEAK_STYLE_CHOICES,
            visible_when=lambda w: w.peak_hold,
        ),
        Scale(
            attr="peak_hold_seconds",
            title="Hold Time",
            min=0.2,
            max=4.0,
            step=0.1,
            visible_when=lambda w: w.peak_hold,
        ),
        Choice(
            attr="peak_hold_tint",
            title="Tint",
            options=PEAK_HOLD_TINT_CHOICES,
            visible_when=lambda w: w.peak_hold,
        ),
        Color(
            attr="peak_hold_color",
            title="Custom Color",
            visible_when=lambda w: w.peak_hold and w.peak_hold_tint == "custom",
        ),
    ])


def color_group() -> Group:
    """Shared color palette / style group for bar/line kinds."""
    return Group("Colors", [
        Choice(
            attr="color_style",
            title="Color Style",
            options=[
                ("solid", "Solid"),
                ("classic", "Classic"),
                ("gradient", "Gradient"),
            ],
        ),
        Color(
            attr="color",
            title="Color",
            visible_when=lambda w: (
                w.color_style == "solid"
                or (w.color_style == "classic" and getattr(w, "vu_style", None) == "needle")
            ),
        ),
        Choice(
            attr="line_gradient",
            title="Gradient",
            options=GRADIENT_CHOICES,
            visible_when=lambda w: w.color_style == "gradient",
        ),
        Color(
            attr="line_color_lo",
            title="Low Color",
            visible_when=lambda w: w.color_style == "gradient" and w.line_gradient == "gradient",
        ),
        Color(
            attr="line_color_hi",
            title="High Color",
            visible_when=lambda w: w.color_style == "gradient" and w.line_gradient == "gradient",
        ),
        Toggle(
            attr="reverse_colors",
            title="Reverse Colors",
            visible_when=lambda w: w.color_style == "gradient",
        ),
        Scale(
            attr="color_sync",
            title="Match Main Visualizer",
            subtitle="Blend colors toward the main visualizer palette",
            min=0.0,
            max=1.0,
            step=0.05,
            format_fn=lambda v: f"{v:.0%}",
        ),
    ])


def background_group() -> Group:
    """Background settings (always visible, separate from Colors)."""
    return Group("Background", [
        Choice(
            attr="background_mode",
            title="Background",
            options=BACKGROUND_CHOICES,
        ),
        Color(
            attr="background_color",
            title="Background Color",
            visible_when=lambda w: w.background_mode == "custom",
        ),
    ])


def window_group() -> Group:
    """Shared window settings."""
    return Group("Window", [
        Scale(
            attr="frame_rate_cap",
            title="Frame Rate",
            min=0,
            max=100,
            step=1,
            format_fn=lambda v: "Unlimited" if v == 0 else f"{v:.0f} fps",
        ),
    ])


# ---------------------------------------------------------------------------
# Per-kind descriptors
# ---------------------------------------------------------------------------

def dvd_settings() -> list:
    return [
        Group("Appearance", [
            Choice(
                attr="icon_name",
                title="Icon",
                options=DVD_ICON_CHOICES,
            ),
            Scale(
                attr="dvd_base_size",
                title="Size",
                min=24,
                max=160,
                step=2,
                format_fn=lambda v: f"{v:.0f}px",
            ),
            Scale(
                attr="dvd_speed_scale",
                title="Speed",
                min=0.25,
                max=3.0,
                step=0.05,
                format_fn=format_times,
            ),
            Scale(
                attr="dvd_reactivity",
                title="Audio Reactivity",
                min=0.0,
                max=2.5,
                step=0.05,
                format_fn=format_times,
            ),
        ]),
        Group("Audio Effects", [
            Toggle(attr="dvd_beats_enabled", title="React to Beats"),
            Scale(
                attr="dvd_beat_sensitivity",
                title="Beat Sensitivity",
                min=1.05,
                max=2.5,
                step=0.05,
                visible_when=lambda w: w.dvd_beats_enabled,
            ),
        ]),
        background_group(),
        window_group(),
    ]


def oscilloscope_settings() -> list:
    return [
        Group("Display", [
            Scale(
                attr="scope_time_base",
                title="Time Base",
                min=1,
                max=20,
                step=1,
                format_fn=lambda v: f"{v:.0f}x",
            ),
            Toggle(
                attr="scope_trigger",
                title="Trigger",
                subtitle="Show horizontal trigger line",
            ),
            Toggle(attr="show_labels", title="Show Labels"),
            Scale(
                attr="scope_line_width",
                title="Line Width",
                min=0.5,
                max=6.0,
                step=0.1,
                format_fn=format_px,
            ),
        ]),
        color_group(),
        background_group(),
        window_group(),
    ]


def xy_scope_settings() -> list:
    return [
        Group("Display", [
            Toggle(
                attr="xy_goniometer",
                title="Goniometer View",
                subtitle="Rotate 45°: mono is vertical, stereo width spreads sideways",
            ),
            Scale(
                attr="xy_line_width",
                title="Line Width",
                min=0.5,
                max=6.0,
                step=0.1,
                format_fn=format_px,
            ),
            Toggle(attr="xy_points", title="Show Points"),
            Toggle(
                attr="xy_curved",
                title="Curved",
                subtitle="Smooth bezier curves",
            ),
            Toggle(
                attr="maintain_aspect_ratio",
                title="Maintain Aspect Ratio",
                subtitle="Keep the plot and the window square",
            ),
        ]),
        color_group(),
        background_group(),
        window_group(),
    ]


def vectorscope_settings() -> list:
    return [
        Group("Display", [
            Scale(
                attr="vector_dot_size",
                title="Dot Size",
                min=1.0,
                max=8.0,
                step=0.5,
                format_fn=format_px,
            ),
            Scale(
                attr="vector_persistence",
                title="Persistence",
                min=0.0,
                max=0.95,
                step=0.01,
                format_fn=format_percent,
            ),
            Toggle(
                attr="vector_auto_gain",
                title="Auto Gain",
                subtitle="Keep the cloud a steady size whatever the volume",
            ),
            Toggle(
                attr="maintain_aspect_ratio",
                title="Maintain Aspect Ratio",
                subtitle="Keep the plot and the window square",
            ),
        ]),
        color_group(),
        background_group(),
        window_group(),
    ]


def vu_meter_settings() -> list:
    return [
        Group("Display", [
            Choice(
                attr="vu_style",
                title="Style",
                options=VU_STYLE_CHOICES,
            ),
            Scale(
                attr="vu_height_scale",
                title="Height Scale",
                min=0.25,
                max=2.5,
                step=0.05,
                format_fn=format_times,
            ),
            Scale(
                attr="vu_segments",
                title="LED Segments",
                min=4,
                max=30,
                step=1,
                visible_when=lambda w: w.vu_style == "led",
            ),
            Scale(
                attr="vu_yellow_zone",
                title="Yellow Zone",
                subtitle="Threshold for yellow warning",
                min=0.1,
                max=0.9,
                step=0.05,
                visible_when=lambda w: w.vu_style == "led",
            ),
            Scale(
                attr="vu_red_zone",
                title="Red Zone",
                subtitle="Threshold for red clipping",
                min=0.2,
                max=0.98,
                step=0.05,
                visible_when=lambda w: w.vu_style == "led",
            ),
            Scale(
                attr="vu_led_thickness",
                title="Line Thickness",
                min=0.5,
                max=3.0,
                step=0.25,
                format_fn=format_times,
            ),
        ]),
        color_group(),
        peak_hold_group(),
        background_group(),
        window_group(),
    ]


def peak_meter_settings() -> list:
    return [
        Group("Display", [
            Choice(
                attr="peak_scale",
                title="Scale",
                options=PEAK_SCALE_CHOICES,
                subtitle="Decibels spread out quiet levels; Linear is raw amplitude",
            ),
            Scale(
                attr="peak_line_thickness",
                title="Hold Line Thickness",
                min=0.5,
                max=3.0,
                step=0.25,
                format_fn=format_times,
            ),
        ]),
        color_group(),
        peak_hold_group(),
        background_group(),
        window_group(),
    ]


def spectrum_settings() -> list:
    return [
        Group("Display", [
            Scale(
                attr="num_bars",
                title="Bars",
                min=4,
                max=64,
                step=1,
            ),
            Choice(
                attr="spectrum_style",
                title="Style",
                options=SPECTRUM_STYLE_CHOICES,
            ),
            Scale(
                attr="spectrum_led_segments",
                title="LED Segments",
                min=4,
                max=64,
                step=2,
                visible_when=lambda w: w.spectrum_style == "led",
            ),
            Scale(
                attr="spectrum_yellow_zone",
                title="Yellow Zone",
                subtitle="Threshold for yellow warning",
                min=0.1,
                max=0.9,
                step=0.05,
                visible_when=lambda w: w.spectrum_style == "led",
            ),
            Scale(
                attr="spectrum_red_zone",
                title="Red Zone",
                subtitle="Threshold for red clipping",
                min=0.2,
                max=0.98,
                step=0.05,
                visible_when=lambda w: w.spectrum_style == "led",
            ),
            Scale(
                attr="spectrum_height_scale",
                title="Height Scale",
                min=0.25,
                max=2.5,
                step=0.05,
                format_fn=format_times,
            ),
            Scale(
                attr="spectrum_led_thickness",
                title="Line Thickness",
                min=0.5,
                max=3.0,
                step=0.25,
                format_fn=format_times,
            ),
            Toggle(attr="show_labels", title="Show Labels"),
            Toggle(attr="mirror_reflection", title="Mirror Reflection"),
        ]),
        color_group(),
        peak_hold_group(),
        background_group(),
        window_group(),
    ]


def spectrogram_settings() -> list:
    return [
        Group("Display", [
            Choice(
                attr="spectrogram_vertical",
                title="Orientation",
                options=[(True, "Vertical"), (False, "Horizontal")],
            ),
            Scale(
                attr="spectrogram_speed",
                title="Scroll Speed",
                min=1,
                max=10,
                step=1,
                format_fn=lambda v: f"{v:.0f}x",
            ),
        ]),
        Group("Colors", [
            Choice(
                attr="spectrogram_palette",
                title="Palette",
                options=PALETTE_LABELS,
            ),
            Color(attr="spectrogram_color_lo", title="Low Color"),
            Color(attr="spectrogram_color_hi", title="High Color"),
            Scale(
                attr="spectrogram_color_ceiling",
                title="Color Ceiling",
                min=0.5,
                max=2.0,
                step=0.05,
                format_fn=format_times,
            ),
        ]),
        frequency_range_group(),
        background_group(),
        window_group(),
    ]


def terrain_settings() -> list:
    return [
        Group("Display", [
            Choice(
                attr="terrain_palette",
                title="Palette",
                options=PALETTE_LABELS,
            ),
            Color(attr="terrain_color_lo", title="Low Color"),
            Color(attr="terrain_color_hi", title="High Color"),
            Scale(
                attr="terrain_color_ceiling",
                title="Color Ceiling",
                min=0.5,
                max=2.0,
                step=0.05,
                format_fn=format_times,
            ),
            Scale(
                attr="terrain_zoom",
                title="Zoom",
                min=0.25,
                max=4.0,
                step=0.05,
                format_fn=format_times,
            ),
            Toggle(attr="terrain_show_axes", title="Show Axes"),
            Toggle(attr="terrain_auto_rotate", title="Auto-Rotate"),
            Scale(
                attr="terrain_rotate_speed",
                title="Rotate Speed",
                min=1,
                max=60,
                step=1,
                format_fn=format_dps,
                visible_when=lambda w: w.terrain_auto_rotate,
            ),
            Choice(
                attr="terrain_rotate_direction",
                title="Rotate Direction",
                options=[(1, "Clockwise"), (-1, "Counter-Clockwise")],
                visible_when=lambda w: w.terrain_auto_rotate,
            ),
        ]),
        frequency_range_group(),
        background_group(),
        window_group(),
    ]


def waterfall_settings() -> list:
    return [
        Group("Display", [
            Choice(
                attr="waterfall_palette",
                title="Palette",
                options=PALETTE_LABELS,
            ),
            Color(attr="waterfall_color_lo", title="Low Color"),
            Color(attr="waterfall_color_hi", title="High Color"),
            Scale(
                attr="waterfall_color_ceiling",
                title="Color Ceiling",
                min=0.5,
                max=2.0,
                step=0.05,
                format_fn=format_times,
            ),
            Scale(
                attr="waterfall_zoom",
                title="Zoom",
                min=0.25,
                max=4.0,
                step=0.05,
                format_fn=format_times,
            ),
            Toggle(attr="waterfall_show_axes", title="Show Axes"),
            Scale(
                attr="waterfall_height_scale",
                title="Height Scale",
                min=0.0,
                max=2.0,
                step=0.05,
                format_fn=format_times,
            ),
            Toggle(attr="waterfall_textured", title="Textured"),
            Toggle(attr="waterfall_auto_rotate", title="Auto-Rotate"),
            Scale(
                attr="waterfall_rotate_speed",
                title="Rotate Speed",
                min=1,
                max=60,
                step=1,
                format_fn=format_dps,
                visible_when=lambda w: w.waterfall_auto_rotate,
            ),
            Choice(
                attr="waterfall_rotate_direction",
                title="Rotate Direction",
                options=[(1, "Clockwise"), (-1, "Counter-Clockwise")],
                visible_when=lambda w: w.waterfall_auto_rotate,
            ),
        ]),
        frequency_range_group(),
        background_group(),
        window_group(),
    ]


def pipes_settings() -> list:
    return [
        Group("Display", [
            Choice(
                attr="pipes_style",
                title="Tube Style",
                options=PIPE_STYLE_CHOICES,
            ),
            Scale(
                attr="pipes_max_pipes",
                title="Max Pipes",
                min=1,
                max=8,
                step=1,
            ),
            Scale(
                attr="pipes_base_width",
                title="Tube Width",
                min=1.0,
                max=16.0,
                step=0.5,
                format_fn=format_px,
            ),
            Toggle(attr="pipes_fade_enabled", title="Fade on Reset"),
            Scale(
                attr="pipes_fade_seconds",
                title="Fade Time",
                min=0.0,
                max=5.0,
                step=0.1,
                format_fn=lambda v: f"{v:.1f}s",
                visible_when=lambda w: w.pipes_fade_enabled,
            ),
            Toggle(
                attr="pipes_auto_rotate",
                title="Auto-Rotate",
            ),
            Scale(
                attr="pipes_rotate_speed",
                title="Rotate Speed",
                min=1,
                max=60,
                step=1,
                format_fn=format_dps,
                visible_when=lambda w: w.pipes_auto_rotate,
            ),
            Toggle(
                attr="pipes_beats_enabled",
                title="React to Beats",
                subtitle="Spawn burst of pipes on beat",
            ),
            Scale(
                attr="pipes_beat_sensitivity",
                title="Beat Sensitivity",
                min=1.05,
                max=2.5,
                step=0.05,
                visible_when=lambda w: w.pipes_beats_enabled,
            ),
            Toggle(
                attr="pipes_pulse_width",
                title="Pulse Width",
                subtitle="Tube width reacts to audio",
            ),
            Toggle(
                attr="pipes_tube_shading",
                title="Tube Shading",
                subtitle="Pseudo-3D glossy highlight",
            ),
        ]),
        window_group(),
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
