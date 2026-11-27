#!/usr/bin/env python3
"""
Declarative settings system for mini visualizer windows.

Replaces the ad-hoc manual row builders in aux_window.py with a
GNOME-aligned, descriptor-based approach. One descriptor list per
kind, shared groups, progressive disclosure, auto-generated save
lists.
"""

from dataclasses import dataclass, field
from typing import List, Tuple, Callable, Optional, Union, Any


# ---------------------------------------------------------------------------
# Value formatting helpers (used by Scale descriptors)
# ---------------------------------------------------------------------------

def format_hz(value):
    """Format a frequency in Hz for display."""
    return f"{value / 1000:.1f} kHz" if value >= 1000 else f"{value:.0f} Hz"


def format_percent(value):
    """Format a 0..1 value as a percentage."""
    return f"{int(value * 100)}%"


def format_times(value):
    """Format a gain multiplier."""
    return f"×{value:.2f}"


def format_px(value):
    """Format a pixel value."""
    return f"{value:.1f}px"


def format_dps(value):
    """Format degrees per second."""
    return f"{value:.0f}°/s"


# ---------------------------------------------------------------------------
# Descriptor classes
# ---------------------------------------------------------------------------

@dataclass
class Setting:
    """Base class for all settings descriptors."""
    attr: str                    # Attribute name on AuxVisualizerWindow
    title: str                   # Row title (shown to user)
    subtitle: Optional[str] = None  # Optional subtitle/explanation
    visible_when: Optional[Callable[[Any], bool]] = None

    def is_visible(self, window) -> bool:
        """Check if this setting should be shown in the current state."""
        return True if self.visible_when is None else self.visible_when(window)


@dataclass
class Toggle(Setting):
    """Boolean toggle (Adw.SwitchRow)."""
    on_change: Optional[str] = None   # Method name on window to call


@dataclass
class Scale(Setting):
    """Numeric slider/spinner (Adw.SpinRow)."""
    min: float = 0.0
    max: float = 1.0
    step: float = 0.1
    format_fn: Callable[[float], str] = str


@dataclass
class Choice(Setting):
    """Dropdown selection (Adw.ComboRow)."""
    options: List[Tuple[str, str]] = field(default_factory=list)
    on_change: Optional[str] = None


@dataclass
class Color(Setting):
    """Color picker (Adw.ActionRow + Gtk.ColorDialogButton)."""
    pass


@dataclass
class Group:
    """A titled group of settings (Adw.PreferencesGroup)."""
    title: str
    items: List[Union[Setting, 'Group']]
    visible_when: Optional[Callable[[Any], bool]] = None

    def all_settings(self) -> List[Setting]:
        """Flatten all settings (not sub-groups) in this group."""
        result = []
        for item in self.items:
            if isinstance(item, Setting):
                result.append(item)
            elif isinstance(item, Group):
                result.extend(item.all_settings())
        return result

    def is_visible(self, window) -> bool:
        return True if self.visible_when is None else self.visible_when(window)


# Type alias for the descriptor list returned by kind factories
KindSettings = List[Union[Setting, Group]]


# ---------------------------------------------------------------------------
# Generic renderer
# ---------------------------------------------------------------------------

def build_settings_page(window, descriptors: KindSettings):
    """Build an Adw.PreferencesPage from a list of setting descriptors.

    Parameters
    ----------
    window : AuxVisualizerWindow
        The mini visualizer window whose attributes the settings control.
    descriptors : KindSettings
        Groups and settings for this kind.

    Returns
    -------
    Adw.PreferencesPage
    """
    import gi
    gi.require_version("Gtk", "4.0")
    gi.require_version("Adw", "1")
    from gi.repository import Gtk, Adw

    page = Adw.PreferencesPage()
    settings_rows = {}  # title -> Adw.ActionRow (for disclosure updates)

    for item in descriptors:
        if isinstance(item, Group):
            if not item.is_visible(window):
                continue

            group = Adw.PreferencesGroup(title=item.title)
            page.add(group)

            for setting in item.all_settings():
                row = _build_row(window, setting)
                if row is not None:
                    group.add(row)
                    settings_rows[setting.title] = row

        else:
            # Ungrouped setting — add to a default "Appearance" group
            group = Adw.PreferencesGroup(title="General")
            page.add(group)
            row = _build_row(window, item)
            if row is not None:
                group.add(row)
                settings_rows[item.title] = row

    # Store reference on window for update_row_sensitivity
    window.settings_rows = settings_rows

    # Auto-connect value-changed → settings_changed on every widget that
    # supports it (same behavior as the old build_settings_page).
    for widget in _iter_widgets(page):
        if isinstance(widget, Gtk.Switch):
            widget.connect_after("notify::active", lambda w, p: _on_value_changed(window))
        elif isinstance(widget, (Gtk.Scale, Gtk.SpinButton)):
            widget.connect_after("value-changed", lambda w: _on_value_changed(window))
        elif isinstance(widget, Gtk.DropDown):
            widget.connect_after("notify::selected", lambda w, p: _on_value_changed(window))
        elif isinstance(widget, Gtk.ColorDialogButton):
            widget.connect_after("notify::rgba", lambda w, p: _on_value_changed(window))

    return page


def _build_row(window, setting: Setting):
    """Build Adw.ActionRow + suffix widget for a single Setting descriptor.

    Uses GTK4/libadwaita 1.0-compatible widgets (not Adw.SwitchRow/etc.)
    so it works with any libadwaita version >= 1.0.

    All rows are built regardless of visibility so progressive disclosure
    can show/hide them dynamically after the page is created.
    """
    import gi
    gi.require_version("Gtk", "4.0")
    gi.require_version("Adw", "1")
    from gi.repository import Gtk, Adw

    row = Adw.ActionRow(
        title=setting.title,
        subtitle=setting.subtitle or "",
    )

    if isinstance(setting, Toggle):
        switch = Gtk.Switch()
        switch.set_active(getattr(window, setting.attr))
        switch.set_valign(Gtk.Align.CENTER)
        row.add_suffix(switch)
        row.set_activatable_widget(switch)

        def _toggle_changed(widget, param, attr=setting.attr, on_change=setting.on_change):
            setattr(window, attr, widget.get_active())
            if on_change:
                getattr(window, on_change, lambda: None)()

        switch.connect("notify::active", _toggle_changed)

    elif isinstance(setting, Scale):
        scale = Gtk.Scale.new_with_range(
            Gtk.Orientation.HORIZONTAL,
            setting.min,
            setting.max,
            setting.step,
        )
        scale.set_value(getattr(window, setting.attr))
        scale.set_size_request(120, -1)
        scale.set_draw_value(True)
        scale.set_value_pos(Gtk.PositionType.RIGHT)
        scale.set_format_value_func(
            lambda widget, value: setting.format_fn(value)
        )
        scale.set_hexpand(False)
        scale.set_valign(Gtk.Align.CENTER)
        row.add_suffix(scale)

        def _scale_changed(widget, attr=setting.attr):
            cast = type(getattr(window, attr))
            setattr(window, attr, cast(widget.get_value()))

        scale.connect("value-changed", lambda w: _scale_changed(w, setting.attr))

    elif isinstance(setting, Choice):
        dropdown = Gtk.DropDown.new_from_strings(
            [label for _, label in setting.options]
        )
        keys = [key for key, _ in setting.options]
        current = getattr(window, setting.attr)
        try:
            dropdown.set_selected(keys.index(current))
        except ValueError:
            dropdown.set_selected(0)
        dropdown.set_valign(Gtk.Align.CENTER)
        row.add_suffix(dropdown)

        def _choice_changed(widget, param, attr=setting.attr, keys=keys, on_change=setting.on_change):
            idx = widget.get_selected()
            if 0 <= idx < len(keys):
                setattr(window, attr, keys[idx])
                if on_change:
                    getattr(window, on_change, lambda: None)()

        dropdown.connect("notify::selected", _choice_changed)

    elif isinstance(setting, Color):
        button = Gtk.ColorDialogButton(dialog=Gtk.ColorDialog())
        button.set_rgba(getattr(window, setting.attr))
        button.set_valign(Gtk.Align.CENTER)
        row.add_suffix(button)

        def _color_changed(widget, param, attr=setting.attr):
            setattr(window, attr, widget.get_rgba())

        button.connect("notify::rgba", _color_changed)

    return row


def _digits_for_step(step: float) -> int:
    """Return the number of decimal digits needed for a step size."""
    s = str(step)
    if '.' in s:
        return len(s.split('.')[1].rstrip('0'))
    return 0


def _on_value_changed(window):
    """Delegate to the window's existing settings_changed handler."""
    if hasattr(window, "settings_changed"):
        window.settings_changed()


def _iter_widgets(widget):
    """Yield the widget and every descendant in depth-first order."""
    yield widget
    if hasattr(widget, "get_first_child"):
        child = widget.get_first_child()
        while child is not None:
            yield from _iter_widgets(child)
            child = child.get_next_sibling()


# ---------------------------------------------------------------------------
# Progressive disclosure helper
# ---------------------------------------------------------------------------

def update_row_sensitivity(window, descriptors: KindSettings):
    """Show/hide rows based on progressive disclosure rules.

    Call this after any setting changes that affect visibility of others.
    """
    rows = getattr(window, "settings_rows", {})

    def _walk_descriptors(items):
        for item in items:
            if isinstance(item, Group):
                if item.is_visible(window):
                    _walk_descriptors(item.items)
            elif isinstance(item, Setting):
                row = rows.get(item.title)
                if row is not None:
                    row.set_visible(item.is_visible(window))

    _walk_descriptors(descriptors)


# ---------------------------------------------------------------------------
# Auto-generated SAVED_SETTINGS
# ---------------------------------------------------------------------------

def collect_saved_attrs(kind_registry: dict) -> List[str]:
    """Derive SAVED_SETTINGS by walking all kind descriptors.

    Parameters
    ----------
    kind_registry : dict
        Maps kind string to a callable returning KindSettings.

    Returns
    -------
    list of str
        Sorted, deduplicated attribute names that appear in any descriptor.
    """
    attrs = set()

    for kind, get_settings in kind_registry.items():
        descriptors = get_settings()
        _collect_attrs(descriptors, attrs)

    return sorted(attrs)


def _collect_attrs(items, attrs: set):
    """Recursively collect attr names from descriptors."""
    for item in items:
        if isinstance(item, Setting):
            attrs.add(item.attr)
        elif isinstance(item, Group):
            _collect_attrs(item.items, attrs)
