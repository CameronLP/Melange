# cursor_hider.py
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

"""Hides the mouse pointer in fullscreen after it's been still a moment.

Used by the main window, mirror windows and mini visualizer windows.
This is the only code that changes their pointer, so nothing else
undoes it.
"""

from gi.repository import Gdk, Gtk, GLib


class FullscreenCursorHider:

    # How long the pointer has to be still before it disappears.
    IDLE_SECONDS = 2.0

    # Movement smaller than this doesn't count. GTK sends synthetic
    # motion events at the pointer's current position when the layout
    # under it changes (e.g. the header bar auto-hiding); those used to
    # bring the pointer straight back the moment it was hidden.
    MOVEMENT_THRESHOLD_PX = 3

    def __init__(self, window, may_hide=None, on_hide=None):
        """may_hide() - optional extra condition, e.g. "the pointer is
        over the visualizer, not the sidebar". on_hide() - called when
        the pointer hides, for hiding on-screen controls at the same
        moment."""

        self.window = window
        self.may_hide = may_hide or (lambda: True)
        self.on_hide = on_hide
        self.timer = None
        self.last_position = None
        self.hidden = False

        motion = Gtk.EventControllerMotion()
        motion.set_propagation_phase(Gtk.PropagationPhase.CAPTURE)
        motion.connect("motion", self.on_motion)
        window.add_controller(motion)

        window.connect("notify::fullscreened", lambda *args: self.restart())

    def on_motion(self, controller, x, y):

        if self.last_position is not None:
            last_x, last_y = self.last_position

            if (
                abs(x - last_x) < self.MOVEMENT_THRESHOLD_PX
                and abs(y - last_y) < self.MOVEMENT_THRESHOLD_PX
            ):
                return

        self.last_position = (x, y)
        self.restart()

    def restart(self):
        """Show the pointer and start counting idle time again."""

        if self.timer is not None:
            GLib.source_remove(self.timer)
            self.timer = None

        self.show()

        if self.window.is_fullscreen():
            self.timer = GLib.timeout_add(int(self.IDLE_SECONDS * 1000), self.on_idle)

    def on_idle(self):

        self.timer = None

        if self.window.is_fullscreen() and self.may_hide():
            self.window.set_cursor(Gdk.Cursor.new_from_name("none"))
            self.hidden = True

            if self.on_hide:
                self.on_hide()
        elif self.window.is_fullscreen():
            # Not allowed right now (e.g. over the sidebar) - check again.
            self.timer = GLib.timeout_add(int(self.IDLE_SECONDS * 1000), self.on_idle)

        return False

    def show(self):

        if self.hidden:
            self.window.set_cursor(None)
            self.hidden = False

    def stop(self):

        if self.timer is not None:
            GLib.source_remove(self.timer)
            self.timer = None

        self.show()
