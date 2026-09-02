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

from gi.repository import Gtk, Adw, Gdk, Gio, GLib


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
    #
    # Since this displays the primary's actual live webview - not a
    # copy - anything the primary's page renders (including the
    # on-canvas preset-nav arrows) shows up here too; there's only one
    # underlying DOM. Gtk.Picture doesn't forward input back to a
    # paintable's source widget, though, so those arrows (and
    # anything else in the page) are inert here - purely visual, same
    # as everything else in the mirror.
    def __init__(self, primary, mirror_number, **kwargs):

        super().__init__(
            application=primary.get_application(),
            **kwargs
        )

        self.primary = primary
        self.mirror_number = mirror_number
        self.hide_timer = None

        self.set_default_size(800, 600)
        self.update_title(primary.current_preset_name)

        self.toolbar_view = Adw.ToolbarView()
        self.toolbar_view.set_extend_content_to_top_edge(True)

        self.header = Adw.HeaderBar()
        self.header.add_css_class("melange-header")
        self.header.set_show_title(True)

        resize_button = Gtk.Button(
            icon_name="zoom-fit-best-symbolic",
            tooltip_text="Match Main Window Size"
        )

        resize_button.connect(
            "clicked",
            self.match_primary_size_clicked
        )

        self.header.pack_end(resize_button)

        # Same style/behavior as the primary window's own header
        # fullscreen button - reuses this window's existing
        # win.toggle-fullscreen action (see __init__ below), and its
        # icon is kept in sync the same way, via notify::fullscreened.
        self.fullscreen_button = Gtk.Button(
            icon_name="view-fullscreen-symbolic",
            tooltip_text="Toggle Fullscreen",
            action_name="win.toggle-fullscreen"
        )

        self.header.pack_end(self.fullscreen_button)

        # A guaranteed-reliable alternative to the double-click-to-
        # focus-primary handlers below - those depend on correctly
        # disambiguating a double-click from a drag via GTK gesture
        # arbitration, which has been reported as still not working
        # even after fixing one real bug in it (see
        # on_picture_drag_begin/on_picture_pressed), and can't be
        # tested directly in the environment this was built in. A
        # plain button click has none of that ambiguity.
        find_primary_button = Gtk.Button(
            icon_name="focus-windows-symbolic",
            tooltip_text="Find Main Window"
        )

        find_primary_button.connect(
            "clicked",
            lambda button: self.primary.bring_to_attention()
        )

        self.header.pack_end(find_primary_button)

        self.toolbar_view.add_top_bar(self.header)

        self.picture = Gtk.Picture()

        # FILL rather than the default CONTAIN: always fill the whole
        # window, even if that means stretching/distorting the aspect
        # ratio, rather than letterboxing - a full-bleed mirror on a
        # second monitor reads better than black bars, and the user
        # asked for this specifically.
        self.picture.set_content_fit(Gtk.ContentFit.FILL)

        self.refresh_paintable()

        self.toolbar_view.set_content(self.picture)

        self.set_content(self.toolbar_view)

        # Drag-from-anywhere (not just the header bar), plus
        # double-click to raise/focus the primary - split across two
        # separate gesture types rather than one GestureClick calling
        # begin_move() straight from "pressed", which was tried first
        # and didn't work: begin_move() grabs the pointer for an
        # interactive move the moment the *first* press of a would-be
        # double-click happens, before GTK ever gets a chance to
        # recognize a second press following it - the click sequence
        # gets consumed by the move grab instead of being delivered as
        # a second discrete press. Gtk.GestureDrag only fires
        # drag-begin once real motion happens past its own built-in
        # threshold, so a plain double-click (no movement in between)
        # never triggers it at all, leaving the separate GestureClick
        # below free to see both presses and count them correctly.
        drag_gesture = Gtk.GestureDrag()
        drag_gesture.set_button(Gdk.BUTTON_PRIMARY)

        drag_gesture.connect(
            "drag-begin",
            self.on_picture_drag_begin
        )

        self.picture.add_controller(drag_gesture)

        click_gesture = Gtk.GestureClick()
        click_gesture.set_button(Gdk.BUTTON_PRIMARY)
        click_gesture.set_propagation_phase(Gtk.PropagationPhase.CAPTURE)

        click_gesture.connect(
            "pressed",
            self.on_picture_pressed
        )

        self.picture.add_controller(click_gesture)

        # Same double-click-raises-the-primary behavior, but for the
        # header bar specifically - nothing above covers it. CAPTURE
        # phase so this reliably sees every press (and can count a
        # real double-click) ahead of the header bar's own internal
        # click/drag handling, without claiming the sequence itself -
        # its native double-click-to-maximize keeps working alongside
        # this rather than being overridden.
        header_click = Gtk.GestureClick()
        header_click.set_button(Gdk.BUTTON_PRIMARY)
        header_click.set_propagation_phase(Gtk.PropagationPhase.CAPTURE)

        header_click.connect(
            "pressed",
            self.on_header_pressed
        )

        self.header.add_controller(header_click)

        # Same auto-hide behavior as the primary window's toolbar -
        # reveal on motion, hide again after a few seconds unless the
        # pointer is actually over the header bar. No WebKit involved
        # here (Gtk.Picture doesn't swallow events the way WebKit's own
        # hit-testing does), so unlike the primary this doesn't need
        # CAPTURE-phase controllers or a synthetic-motion-event filter.
        motion = Gtk.EventControllerMotion()
        motion.connect("motion", self.mouse_move)
        self.add_controller(motion)

        header_motion = Gtk.EventControllerMotion()
        header_motion.connect("enter", self.toolbar_enter)
        header_motion.connect("leave", self.toolbar_leave)
        self.header.add_controller(header_motion)

        self.mouse_over_toolbar = False

        fullscreen_action = Gio.SimpleAction.new("toggle-fullscreen", None)

        fullscreen_action.connect(
            "activate",
            self.toggle_fullscreen
        )

        self.add_action(fullscreen_action)

        self.connect(
            "notify::fullscreened",
            self.update_fullscreen_button_icon
        )

        escape_controller = Gtk.EventControllerKey()

        escape_controller.connect(
            "key-pressed",
            self.on_key_pressed
        )

        self.add_controller(escape_controller)

        # Workaround: maximizing was observed to leave the picture
        # showing broken/frozen content instead of continuing to track
        # the live source - worse on a rotated secondary monitor,
        # where the window itself resizes correctly but the picture's
        # content doesn't. A plain queue_draw() (an earlier, weaker
        # version of this fix) wasn't enough there - the paintable's
        # backing render state seems to need a genuine reset, not just
        # a repaint request, likely because maximizing onto a monitor
        # with a different transform/scale than the primary window's
        # own forces GTK to rebuild GL-backed render state that a
        # WidgetPaintable instance doesn't automatically follow.
        # Recreating the paintable outright (rather than reusing the
        # same instance) is the more thorough reset. Also covers
        # scale-factor changes generally (dragging the mirror onto a
        # differently-scaled monitor, not just maximizing there), since
        # that's the same underlying class of problem.
        self.connect(
            "notify::maximized",
            lambda *args: self.refresh_paintable()
        )

        self.connect(
            "notify::scale-factor",
            lambda *args: self.refresh_paintable()
        )

        # Reported directly: maximizing frozen on the rotated monitor,
        # then clicking a different window (so this one loses active
        # state) unfroze it on its own - meaning an active-state change
        # alone is enough to make GTK reconcile the broken render
        # state, with no maximize/scale-factor change involved at all.
        # Hooking that directly means a mirror stuck this way recovers
        # as soon as its focus changes for any reason, rather than
        # only on the two triggers above.
        self.connect(
            "notify::is-active",
            lambda *args: self.refresh_paintable()
        )

        self.connect(
            "close-request",
            self.on_close_request
        )

    def on_header_pressed(self, gesture, n_press, x, y):

        if n_press >= 2:
            self.primary.bring_to_attention()

    def on_picture_pressed(self, gesture, n_press, x, y):

        if n_press >= 2:
            self.primary.bring_to_attention()

    def on_picture_drag_begin(self, gesture, start_x, start_y):

        # Reported: clicking the mirror while it's maximized made the
        # picture go blank/grey. begin_move() on an already-maximized
        # surface is a plausible trigger - dragging a maximized window
        # is meaningless anyway (there's nowhere to move it while
        # filling the monitor), so skip the move attempt entirely
        # rather than hand a maximized surface a move request.
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

    def toolbar_enter(self, controller, x, y):

        self.mouse_over_toolbar = True

        if self.hide_timer:
            GLib.source_remove(self.hide_timer)
            self.hide_timer = None

        self.toolbar_view.set_reveal_top_bars(True)
        self.set_cursor(None)

    def toolbar_leave(self, controller):

        self.mouse_over_toolbar = False

        self.hide_timer = GLib.timeout_add_seconds(
            3,
            self.hide_toolbar
        )

    def mouse_move(self, controller, x, y):

        self.set_cursor(None)
        self.toolbar_view.set_reveal_top_bars(True)

        if self.hide_timer:
            GLib.source_remove(self.hide_timer)

        self.hide_timer = GLib.timeout_add_seconds(
            3,
            self.hide_toolbar
        )

    def hide_toolbar(self):

        self.hide_timer = None

        if self.mouse_over_toolbar:
            return False

        self.toolbar_view.set_reveal_top_bars(False)

        if self.is_fullscreen():
            self.set_cursor(Gdk.Cursor.new_from_name("none"))

        return False

    def refresh_paintable(self):

        self.picture.set_paintable(
            Gtk.WidgetPaintable.new(self.primary.webview)
        )

    def update_title(self, preset_name):

        title = f"Melange (Mirror {self.mirror_number})"

        if preset_name:
            title += f' - "{preset_name}"'

        self.set_title(title)

    def match_primary_size_clicked(self, button):

        self.set_default_size(
            self.primary.get_width(),
            self.primary.get_height()
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

    def on_key_pressed(self, controller, keyval, keycode, state):

        if keyval == Gdk.KEY_Escape and self.is_fullscreen():
            self.unfullscreen()
            return True

        return False

    def on_close_request(self, window):

        if self in self.primary.mirror_windows:
            self.primary.mirror_windows.remove(self)
            self.primary.rebuild_mirror_windows_menu()

        # Returning False here (the usual "let the default handler run"
        # convention for this signal) left the window fully alive -
        # confirmed via get_visible() staying True - regardless of
        # whether a plain return or an explicit self.destroy() came
        # first. Destroying from an idle callback (deferring past this
        # signal's own emission) and returning True ("this is handled,
        # don't do anything further") is what actually tears it down;
        # get_visible() correctly flips to False right after.
        GLib.idle_add(self.destroy)

        return True
