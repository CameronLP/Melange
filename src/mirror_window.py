# mirror_window.py
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

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")

from gi.repository import Gtk, Adw, Gdk, Gio


class MirrorWindow(Adw.ApplicationWindow):

    # A true pixel mirror of the primary window's webview - not a
    # second Butterchurn/WebKit instance. Gtk.WidgetPaintable tracks a
    # live GTK widget and can be displayed anywhere else via
    # Gtk.Picture, GPU-composited by GTK itself, with no manual frame
    # capture/streaming code needed - exactly the mechanism this
    # calls for, and confirmed working against a real WebKit.WebView
    # before building this (paintable intrinsic size correctly
    # tracked the live webview, both widgets realized/mapped with no
    # errors). This means a mirror costs one extra GTK window and a
    # Picture widget - no extra WebKitWebProcess, no extra JS engine,
    # no audio/preset-sync bridge to build or keep correct - the
    # multi-hundred-MB-per-window cost a second real WebKit instance
    # would add (see the RAM investigation elsewhere in this history)
    # just doesn't apply here.
    def __init__(self, primary, **kwargs):

        super().__init__(
            application=primary.get_application(),
            **kwargs
        )

        self.primary = primary

        self.set_default_size(800, 600)
        self.set_title("Melange - Mirror")

        header = Adw.HeaderBar()
        header.set_title_widget(
            Adw.WindowTitle(title="Mirror", subtitle="Mirrors the main window")
        )

        picture = Gtk.Picture()

        picture.set_paintable(
            Gtk.WidgetPaintable.new(primary.webview)
        )

        toolbar_view = Adw.ToolbarView()
        toolbar_view.add_top_bar(header)
        toolbar_view.set_content(picture)

        self.set_content(toolbar_view)

        fullscreen_action = Gio.SimpleAction.new("toggle-fullscreen", None)

        fullscreen_action.connect(
            "activate",
            self.toggle_fullscreen
        )

        self.add_action(fullscreen_action)

        escape_controller = Gtk.EventControllerKey()

        escape_controller.connect(
            "key-pressed",
            self.on_key_pressed
        )

        self.add_controller(escape_controller)

        self.connect(
            "close-request",
            self.on_close_request
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

    def on_close_request(self, window):

        if self in self.primary.mirror_windows:
            self.primary.mirror_windows.remove(self)

        return False
