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
    """Build the right Adw.*Row for a single Setting descriptor."""
    import gi
    gi.require_version("Gtk", "4.0")
    gi.require_version("Adw", "1")
    from gi.repository import Gtk, Adw

    if not setting.is_visible(window):
        return None

    if isinstance(setting, Toggle):
        row = Adw.SwitchRow(
            title=setting.title,
            subtitle=setting.subtitle,
        )
        row.set_active(getattr(window, setting.attr))

        def _toggle_changed(widget, param, attr=setting.attr, on_change=setting.on_change):
            setattr(window, attr, widget.get_active())
            if on_change:
                getattr(window, on_change, lambda: None)()

        row.connect("notify::active", _toggle_changed)
        return row

    elif isinstance(setting, Scale):
        row = Adw.SpinRow(
            title=setting.title,
            subtitle=setting.subtitle,
        )
        adj = Gtk.Adjustment(
            value=getattr(window, setting.attr),
            lower=setting.min,
            upper=setting.max,
            step_increment=setting.step,
            page_increment=setting.step * 10,
        )
        row.set_adjustment(adj)

        def _scale_changed(widget, attr=setting.attr):
            cast = type(getattr(window, attr))
            setattr(window, attr, cast(widget.get_value()))

        adj.connect("value-changed", lambda a: _scale_changed(row, setting.attr))
        row.set_digits(_digits_for_step(setting.step))
        return row

    elif isinstance(setting, Choice):
        row = Adw.ComboRow(
            title=setting.title,
            subtitle=setting.subtitle,
        )
        model = Gtk.StringList()
        for _, label in setting.options:
            model.append(label)
        row.set_model(model)

        keys = [key for key, _ in setting.options]
        current = getattr(window, setting.attr)
        try:
            row.set_selected(keys.index(current))
        except ValueError:
            row.set_selected(0)

        def _choice_changed(widget, param, attr=setting.attr, keys=keys, on_change=setting.on_change):
            idx = widget.get_selected()
            if 0 <= idx < len(keys):
                setattr(window, attr, keys[idx])
                if on_change:
                    getattr(window, on_change, lambda: None)()

        row.connect("notify::selected", _choice_changed)
        return row

    elif isinstance(setting, Color):
        row = Adw.ActionRow(
            title=setting.title,
            subtitle=setting.subtitle,
        )
        button = Gtk.ColorDialogButton(dialog=Gtk.ColorDialog())
        button.set_rgba(getattr(window, setting.attr))
        button.set_valign(Gtk.Align.CENTER)
        row.add_suffix(button)

        def _color_changed(widget, param, attr=setting.attr):
            setattr(window, attr, widget.get_rgba())

        button.connect("notify::rgba", _color_changed)
        return row

    return None


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
